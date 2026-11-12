"""``hcli packs doctor`` — step-by-step access diagnostics for the packs on the portal."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Final

import httpx
import rich_click as click
from rich.text import Text

from hcli.commands.packs.common import format_timestamp, json_option, packs_errors, short_snapshot
from hcli.env import ENV
from hcli.lib.auth import get_auth_service
from hcli.lib.commands import async_command
from hcli.lib.console import console, print_json
from hcli.lib.packs.errors import CredentialsError, PacksError, ReleaseUnavailableError
from hcli.lib.packs.install import resolve_sig_dir
from hcli.lib.packs.models import PackFormat
from hcli.lib.packs.repository import PackRepository, RepoBackend
from hcli.lib.packs.server import (
    API_KEY_ENV,
    SERVER_ENV,
    ca_cert_from_env,
    probe_key,
    probe_ready,
    server_from_env,
)


class Status(str, Enum):
    OK = "ok"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


_SYMBOLS: Final = {
    Status.OK: ("✓", "green"),
    Status.FAIL: ("✗", "red"),
    Status.WARN: ("!", "yellow"),
    Status.SKIP: ("-", "dim"),
}


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    hint: str | None = None
    kind: str | None = None

    @classmethod
    def failed(cls, name: str, error: PacksError) -> Check:
        return cls(name, Status.FAIL, error.message, error.hint, error.kind)


def _render(check: Check) -> None:
    symbol, style = _SYMBOLS[check.status]
    console.print(Text.assemble((f"{symbol} ", style), (f"{check.name:<16}", "bold"), check.detail), soft_wrap=True)
    if check.hint and check.status in (Status.FAIL, Status.WARN):
        console.print(Text.assemble(" " * 18, ("Probably fixed by: ", "dim"), check.hint), soft_wrap=True)


def _check_credentials() -> tuple[Check, bool]:
    """Whether hcli has portal credentials (no network), which gates the Release reads.

    doctor is not an AuthCommand (a missing login is one of the things it reports), so it loads the login itself.
    """
    auth = get_auth_service()
    auth.ensure_initialized()
    if auth.is_logged_in():
        return Check("credentials", Status.OK, "logged in"), True
    if auth.has_expired_session():
        error = CredentialsError("Your portal session has expired.", "log in again with `hcli login`")
    else:
        error = CredentialsError("Not logged in to the portal.", "log in with `hcli login`, or set HCLI_API_KEY")
    return Check.failed("credentials", error), False


def _check_sig_dir() -> Check:
    """The FLIRT signature folder ``flirt install`` and ``sync`` default to (``$IDAUSR/sig``).

    Only needed to install packs, and install creates it, so anything short of OK is a warning.
    """
    name = "flirt sig dir"
    root = resolve_sig_dir()
    if not root.exists():
        return Check(name, Status.WARN, f"{root}: does not exist yet (install creates it)")
    if not root.is_dir():
        return Check(
            name, Status.WARN, f"{root}: not a folder", "pass --sig-dir DIR to install elsewhere, or set HCLI_IDAUSR"
        )
    if not os.access(root, os.W_OK):
        return Check(name, Status.WARN, f"{root}: not writable", f"check the permissions of {root}")
    return Check(name, Status.OK, f"{root}: exists, writable")


def _check_server(transport: httpx.BaseTransport | None) -> list[Check]:
    """``HCLI_LUMINA_SERVER`` readiness and, with ``LUMINA_API_KEY``, a read-only key check.

    ``HCLI_LUMINA_CA_CERT`` adds trusted CAs, as ``--ca-cert`` does for push.
    """
    try:
        server = server_from_env()
    except PacksError as e:
        return [Check.failed("lumina server", e), Check("lumina api key", Status.SKIP, "skipped: no usable server")]
    if server is None:
        return [
            Check("lumina server", Status.SKIP, f"{SERVER_ENV} not set (only needed to push packs)"),
            Check("lumina api key", Status.SKIP, f"skipped: {SERVER_ENV} not set"),
        ]
    ca_cert = ca_cert_from_env()
    try:
        checks = [Check("lumina server", Status.OK, probe_ready(server, transport, ca_cert))]
    except PacksError as e:
        return [Check.failed("lumina server", e), Check("lumina api key", Status.SKIP, "skipped: server not ready")]
    key = (ENV.LUMINA_API_KEY or "").strip()
    if not key:
        checks.append(Check("lumina api key", Status.SKIP, f"{API_KEY_ENV} not set"))
        return checks
    try:
        checks.append(Check("lumina api key", Status.OK, f"{API_KEY_ENV} {probe_key(server, key, transport, ca_cert)}"))
    except PacksError as e:
        checks.append(Check.failed("lumina api key", e))
    return checks


async def _run_checks(
    backend: RepoBackend | None = None,
    server_transport: httpx.BaseTransport | None = None,
) -> list[Check]:
    """Every check in order; the arguments override the transports (tests)."""
    checks: list[Check] = []

    async with PackRepository(backend) as repo:
        checks.append(Check("portal", Status.OK, repo.url))
        credentials, credentials_ok = _check_credentials()
        checks.append(credentials)

        try:
            target, elapsed = repo.probe_network()
            checks.append(Check("network", Status.OK, f"{target} connected in {elapsed * 1000:.0f} ms"))
        except PacksError as e:
            checks.append(Check.failed("network", e))

        for fmt in PackFormat:
            name = f"{fmt.value} release"
            if not credentials_ok:
                checks.append(Check(name, Status.SKIP, "skipped: no usable credentials"))
                continue
            try:
                release = await repo.read_release(fmt)
                checks.append(
                    Check(
                        name,
                        Status.OK,
                        f"snapshot {short_snapshot(release.snapshot)}, {len(release.packs)} packs, "
                        f"published {format_timestamp(release.published_at)}",
                    )
                )
            except ReleaseUnavailableError as e:
                # A format not published yet; the other may still work.
                checks.append(Check(name, Status.WARN, e.message, e.hint, e.kind))
            except PacksError as e:
                checks.append(Check.failed(name, e))

    checks.append(_check_sig_dir())
    checks.extend(_check_server(server_transport))
    return checks


@click.command()
@json_option
@packs_errors
@async_command
async def doctor(as_json: bool) -> None:
    """Check access to the packs step by step: portal, login, network, Releases, FLIRT sig dir.

    With HCLI_LUMINA_SERVER set, also checks the server is ready and (with
    LUMINA_API_KEY) that it accepts the key, using a read-only lookup;
    HCLI_LUMINA_CA_CERT trusts a server with a private CA. With
    Also checks that IDA's signature folder ($IDAUSR/sig) is writable.

    Exits non-zero when any check fails (warnings do not count).
    """
    checks = await _run_checks()
    ok = not any(check.status is Status.FAIL for check in checks)

    if as_json:
        print_json({"ok": ok, "checks": [{**asdict(check), "status": check.status.value} for check in checks]})
    else:
        for check in checks:
            _render(check)

    if not ok:
        raise SystemExit(1)
