"""``hcli packs lumina push`` — push Lumina packs to a private Lumina server.

Also the push machinery ``sync`` shares. Each pack is pushed on its own
(``lib.packs.lumina``, the in-process importer): a result
line per pack as it finishes, its state recorded as soon as it is fully
pushed (a failure on the third pack keeps the first two recorded), and
temporary disk use bounded to one pack.

Everything that can fail cheaply is checked before the first download: the
server URL, the API key (a read-only lookup), and the push state file.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeVar

import rich_click as click
from pydantic import BaseModel
from rich.text import Text

from hcli.commands.packs.acquire import (
    SOURCE_TEXT,
    PackSource,
    obtain_pack,
    temp_dir,
)
from hcli.commands.packs.common import format_mib, json_option, packs_errors, with_repository
from hcli.env import ENV
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json, stderr_console
from hcli.lib.packs.errors import MissingApiKeyError, PacksError, PushInterruptedError
from hcli.lib.packs.lumina.importer import ImportTotals, import_pack
from hcli.lib.packs.lumina.pack import Digest
from hcli.lib.packs.models import PackFormat, Release, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.resolve import resolve_packs
from hcli.lib.packs.server import (
    CA_CERT_ENV,
    SERVER_ENV,
    ApiKey,
    LuminaServer,
    check_api_key,
    missing_api_key,
    resolve_api_key,
    server_from_option,
)
from hcli.lib.packs.state import PushRecord, load_state, record_push, utc_now
from hcli.lib.packs.store import cache_dir, cached_pack_path, is_cached

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

LUMINA: Final = PackFormat.LUMINA


def push_options(f: F) -> F:
    """The options ``push`` and ``sync`` share (``--json`` is added separately, as everywhere)."""
    options = [
        click.option(
            "--server",
            "server_url",
            default=lambda: ENV.HCLI_LUMINA_SERVER,
            metavar="URL",
            help=f"Lumina server REST base URL (default: ${SERVER_ENV}).",
        ),
        click.option(
            "--keep",
            is_flag=True,
            default=False,
            help="Keep downloaded packs in the local cache (default: download to a temp folder and delete).",
        ),
        click.option(
            "--dry-run", is_flag=True, default=False, help="Show what would be pushed; download and run nothing."
        ),
        click.option(
            "--api-key-file",
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            default=None,
            help="File holding the Lumina API key (default: $LUMINA_API_KEY).",
        ),
        click.option(
            "--ca-cert",
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            default=lambda: ENV.HCLI_LUMINA_CA_CERT,
            metavar="PEM",
            help=f"Extra trusted CA certificates (PEM) for a server with a private CA (default: ${CA_CERT_ENV}).",
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


@dataclass(frozen=True)
class PushSettings:
    server: LuminaServer
    # None only in a dry run without a key.
    api_key: ApiKey | None
    ca_cert: Path | None
    keep: bool
    as_json: bool

    @property
    def show_progress(self) -> bool:
        return not self.as_json and stderr_console.is_terminal


def push_settings(
    server_url: str | None, api_key_file: Path | None, ca_cert: Path | None, keep: bool, dry_run: bool, as_json: bool
) -> PushSettings:
    """Validate the server and (unless a dry run) the API key before anything is downloaded."""
    server = server_from_option(server_url)
    try:
        api_key: ApiKey | None = resolve_api_key(api_key_file)
    except MissingApiKeyError:
        if not dry_run:
            raise
        api_key = None
    return PushSettings(server, api_key, ca_cert, keep, as_json)


def say(settings: PushSettings, *parts: str | tuple[str, str]) -> None:
    """One human output line (nothing with ``--json``)."""
    if not settings.as_json:
        console.print(Text.assemble(*parts), soft_wrap=True)


def _functions_text(pack: ReleasePack) -> str:
    return f"{pack.rows:,} functions" if pack.rows is not None else f"{pack.members:,} members"


def _pack_label(pack: ReleasePack) -> tuple[str | tuple[str, str], ...]:
    return f"{LUMINA.value} ", (pack.address, "magenta"), f" r{pack.revision}"


def check_server_key(settings: PushSettings) -> None:
    """The read-only key lookup doctor runs: a wrong key, URL or CA fails here, before any download."""
    if settings.api_key is None:
        raise missing_api_key()
    check_api_key(settings.server, settings.api_key.read(), ca_cert=settings.ca_cert)
    say(
        settings,
        ("• ", "blue"),
        (settings.server.url, "cyan"),
        f": {settings.api_key.source} accepted (read-only lookup)",
    )


def _totals_text(totals: ImportTotals, seconds: float) -> str:
    text = f"{totals.pushed:,} pushed · {totals.added:,} added · {totals.ok:,} already present"
    if totals.other:
        text += f" · {totals.other:,} other"
    return f"{text} · {seconds:.1f} s"


class PushResult(BaseModel):
    """One pack pushed in full (its ``--json`` entry)."""

    address: str
    revision: int
    sha256: str
    source: PackSource
    totals: ImportTotals
    seconds: float
    # The import's warnings and hcli's own (cache, state).
    warnings: list[str] = []
    # False when the push succeeded but the state file could not be written.
    recorded: bool = True


async def _push_one(repo: PackRepository, snapshot: str, pack: ReleasePack, settings: PushSettings) -> PushResult:
    api_key = settings.api_key
    if api_key is None:
        raise missing_api_key()
    label = f"{pack.address} r{pack.revision}"
    with temp_dir("hcli-push-") as staging:
        staged = await obtain_pack(repo, LUMINA, pack, staging, keep=settings.keep, as_json=settings.as_json)
        say(settings, ("• ", "blue"), *_pack_label(pack), f": {SOURCE_TEXT[staged.source]}, sha256 ✓")
        say(settings, ("↑ ", "cyan"), f"pushing {_functions_text(pack)} to ", (settings.server.url, "cyan"), " …")
        started = time.monotonic()
        try:
            report = import_pack(
                staged.path,
                # The bytes the Release lists: the import reads exactly these, twice.
                expected=Digest(pack.sha256, pack.size),
                server=settings.server,
                api_key=api_key,
                ca_cert=settings.ca_cert,
            )
        except PacksError as e:
            raise e.add_context(f"Could not push {label}: ")
        except KeyboardInterrupt as e:
            raise PushInterruptedError(
                f"Interrupted while pushing {label}.", "re-run to push it (pushes are idempotent)"
            ) from e
        seconds = time.monotonic() - started

    warnings = ([staged.warning] if staged.warning else []) + report.warnings
    for warning in report.warnings:
        if not settings.as_json:
            stderr_console.print(Text(f"  warning: {warning}", style="dim"), soft_wrap=True)
    totals = report.total
    say(settings, ("✓ ", "green"), (pack.address, "magenta"), f" r{pack.revision}: {_totals_text(totals, seconds)}")
    record = PushRecord(
        address=pack.address,
        revision=pack.revision,
        sha256=pack.sha256,
        snapshot=snapshot,
        pushed_at=utc_now(),
        totals=totals,
    )
    recorded = True
    try:
        record_push(settings.server, record)
    except PacksError as e:
        # The push itself succeeded: say so and go on (sync only re-pushes it).
        logger.debug("could not record the push of %s", pack.address, exc_info=True)
        recorded = False
        warning = f"pushed, but not recorded for sync (it will push it again): {e.message}"
        warnings.append(warning)
        say(settings, ("! ", "yellow"), (pack.address, "magenta"), f" r{pack.revision}: {warning}")
        if e.hint:
            say(settings, "  ", ("Probably fixed by: ", "dim"), e.hint)
    return PushResult(
        address=pack.address,
        revision=pack.revision,
        sha256=pack.sha256,
        source=staged.source,
        totals=totals,
        seconds=round(seconds, 3),
        warnings=warnings,
        recorded=recorded,
    )


@dataclass
class PushOutcome:
    packs: list[PushResult]
    total: ImportTotals

    def to_json(self) -> dict[str, Any]:
        return {
            "packs": [result.model_dump(mode="json") for result in self.packs],
            "total": self.total.model_dump(),
        }


async def push_packs(
    repo: PackRepository, release: Release, packs: Sequence[ReleasePack], settings: PushSettings
) -> PushOutcome:
    """Push each pack in turn, recording each success; stops at the first failure.

    Before anything is downloaded: the state file must be readable (a
    corrupt one would otherwise fail only after the first push) and the
    server must accept the key. A failure after the first pack carries the
    packs already pushed in ``completed`` (printed with ``--json``).
    """
    load_state(settings.server)
    check_server_key(settings)
    outcome = PushOutcome([], ImportTotals())
    try:
        for pack in packs:
            result = await _push_one(repo, release.snapshot, pack, settings)
            outcome.packs.append(result)
            outcome.total += result.totals
    except PacksError as e:
        e.completed = outcome.to_json()
        raise
    return outcome


def dry_run_packs(packs: Sequence[ReleasePack], settings: PushSettings) -> dict[str, Any]:
    """Describe what a push would do: each pack's source and size.

    Nothing is downloaded, pushed or created; ``cached`` is the size check ``list`` uses.
    """
    if settings.api_key is None:
        say(settings, ("! ", "yellow"), "no API key: set LUMINA_API_KEY or pass --api-key-file before pushing")

    root = cache_dir(create=False)
    entries: list[dict[str, Any]] = []
    for pack in packs:
        cached = is_cached(cached_pack_path(pack, root), pack.size)
        where = "to the cache" if settings.keep else "to temp"
        state = "cached" if cached else f"would download {format_mib(pack.size)} {where}"
        say(
            settings,
            ("↑ ", "cyan"),
            "would push ",
            (pack.address, "magenta"),
            f" r{pack.revision} ({_functions_text(pack)}; {state}) to ",
            (settings.server.url, "cyan"),
        )
        entries.append(
            {
                "address": pack.address,
                "revision": pack.revision,
                "sha256": pack.sha256,
                "rows": pack.rows,
                "size": pack.size,
                "source": (PackSource.CACHED if cached else PackSource.WOULD_DOWNLOAD).value,
            }
        )
    say(settings, ("dry run: nothing downloaded or pushed", "dim"))
    return {
        "api_key": settings.api_key.source if settings.api_key else None,
        "packs": entries,
    }


@auth_command()
@click.argument("packs", nargs=-1, required=True)
@push_options
@json_option
@packs_errors
@async_command
@with_repository
async def push(
    repo: PackRepository,
    packs: tuple[str, ...],
    server_url: str | None,
    keep: bool,
    dry_run: bool,
    api_key_file: Path | None,
    ca_cert: Path | None,
    as_json: bool,
) -> None:
    """Push Lumina packs to a private Lumina server.

    PACKS are ``component/platform-group-arch`` addresses or globs (e.g.
    ``msvc/windows-all-x86_64``, ``'msvc/*'``) from the stable Lumina
    Release. Each pack is taken from the local cache when a verified copy is
    there, else downloaded to a temp folder (``--keep``: into the cache),
    then verified against the Release and pushed, one member at a time.

    The API key comes from LUMINA_API_KEY or --api-key-file and is never put
    on a command line; it is checked with a read-only lookup before anything
    is downloaded. Each successful push is recorded for ``sync``.
    """
    settings = push_settings(server_url, api_key_file, ca_cert, keep, dry_run, as_json)
    release = await repo.read_release(LUMINA)
    selected = resolve_packs(release.packs, packs, LUMINA)
    base = {"server": settings.server.url, "snapshot": release.snapshot, "dry_run": dry_run}

    if dry_run:
        described = dry_run_packs(selected, settings)
        if as_json:
            print_json({**base, **described})
        return

    outcome = await push_packs(repo, release, selected, settings)
    if as_json:
        print_json({**base, **outcome.to_json()})
