"""``hcli packs <format> list`` — the packs a format's stable Release currently publishes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import rich_click as click
from rich import box
from rich.table import Table
from rich.text import Text

from hcli.commands.packs.common import (
    format_content,
    format_mib,
    format_timestamp,
    json_option,
    pack_json,
    packs_errors,
    short_snapshot,
    with_repository,
)
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json
from hcli.lib.packs.errors import PacksError
from hcli.lib.packs.install import InstallManifest, install_dir, read_installed, resolve_sig_dir
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.server import LuminaServer, server_from_env
from hcli.lib.packs.state import ServerState, load_state
from hcli.lib.packs.store import cache_dir, cached_pack_path, is_cached

logger = logging.getLogger(__name__)

PushState = tuple[LuminaServer, ServerState]


def _push_state() -> PushState | None:
    """The recorded pushes to ``HCLI_LUMINA_SERVER``, if set; best effort (one small local file)."""
    try:
        server = server_from_env()
        return (server, load_state(server)) if server is not None else None
    except PacksError:
        logger.debug("ignoring the push state of HCLI_LUMINA_SERVER", exc_info=True)
        return None


def _pushed_json(state: ServerState, pack: ReleasePack) -> dict[str, Any] | None:
    record = state.packs.get(pack.address)
    if record is None:
        return None
    return {
        "revision": record.revision,
        "sha256": record.sha256,
        "pushed_at": record.pushed_at,
        "up_to_date": record.sha256 == pack.sha256,
    }


def _pushed_text(state: ServerState, pack: ReleasePack) -> Text:
    """`` ↑r2`` (green: this exact pack; yellow: another build), or nothing when never pushed."""
    record = state.packs.get(pack.address)
    if record is None:
        return Text()
    return Text(f" ↑r{record.revision}", style="green" if record.sha256 == pack.sha256 else "yellow")


class LocalStatus:
    """What the LOCAL column adds after ``cached`` for one format; the base adds nothing.

    Lumina adds the pushes recorded for HCLI_LUMINA_SERVER; FLIRT adds the
    installs found under IDA's signature folder ($IDAUSR/sig).
    """

    def legend(self) -> Text | None:
        """The line printed under the Release header explaining the marks, if any."""
        return None

    def summary_json(self) -> dict[str, Any]:
        """Top-level ``--json`` fields naming where the local state comes from."""
        return {}

    def pack_json(self, pack: ReleasePack) -> dict[str, Any]:
        """Per-pack ``--json`` fields."""
        return {}

    def pack_text(self, pack: ReleasePack) -> Text:
        """What the LOCAL cell shows after the cache state."""
        return Text()


class LuminaPushes(LocalStatus):
    def __init__(self, server: LuminaServer, state: ServerState) -> None:
        self.server = server
        self.state = state

    def legend(self) -> Text:
        """``↑rN`` means pushed to the server."""
        return Text.assemble(
            ("  ↑rN    ", "dim"), ("pushed to ", "dim"), self.server.host, (" (as recorded by this machine)", "dim")
        )

    def summary_json(self) -> dict[str, Any]:
        """The server the push records are for."""
        return {"lumina_server": self.server.url}

    def pack_json(self, pack: ReleasePack) -> dict[str, Any]:
        """The recorded push of this pack, as ``pushed``."""
        return {"pushed": _pushed_json(self.state, pack)}

    def pack_text(self, pack: ReleasePack) -> Text:
        """`` ↑rN`` for a recorded push."""
        return _pushed_text(self.state, pack)


class FlirtInstalls(LocalStatus):
    def __init__(self, root: Path) -> None:
        self.root = root

    def _manifest(self, pack: ReleasePack) -> InstallManifest | None:
        try:
            target = install_dir(self.root, pack)
        except PacksError:
            # An arch hcli cannot install is simply not installed.
            return None
        return read_installed(target, pack).manifest

    def legend(self) -> Text:
        """``✓rN`` means installed in the signature folder."""
        return Text.assemble(("  ✓rN    ", "dim"), ("installed in ", "dim"), str(self.root))

    def summary_json(self) -> dict[str, Any]:
        """The signature folder the installs are read from."""
        return {"sig_dir": str(self.root)}

    def pack_json(self, pack: ReleasePack) -> dict[str, Any]:
        """The installed build of this pack, as ``installed``."""
        manifest = self._manifest(pack)
        if manifest is None:
            return {"installed": None}
        return {
            "installed": {
                "revision": manifest.revision,
                "sha256": manifest.sha256,
                "installed_at": manifest.installed_at,
                "up_to_date": manifest.sha256 == pack.sha256,
            }
        }

    def pack_text(self, pack: ReleasePack) -> Text:
        """`` ✓rN`` (green: this exact pack; yellow: another build), or nothing when not installed."""
        manifest = self._manifest(pack)
        if manifest is None:
            return Text()
        return Text(f" ✓r{manifest.revision}", style="green" if manifest.sha256 == pack.sha256 else "yellow")


def local_status(fmt: PackFormat) -> LocalStatus:
    """The LOCAL extras of ``fmt``: only when its local target (server, signature folder) is configured."""
    match fmt:
        case PackFormat.LUMINA:
            pushes = _push_state()
            return LuminaPushes(*pushes) if pushes is not None else LocalStatus()
        case PackFormat.FLIRT:
            return FlirtInstalls(resolve_sig_dir())


_LIST_HELP: Final = {
    PackFormat.LUMINA: """List the Lumina packs published in the stable Release.

    LOCAL shows ``cached`` when the exact published archive (by sha256) is
    already in the local cache. With HCLI_LUMINA_SERVER set, it also shows
    ``↑rN`` for a pack this machine recorded pushing to that server.
    """,
    PackFormat.FLIRT: """List the FLIRT packs published in the stable Release.

    LOCAL shows ``cached`` when the exact published archive (by sha256) is
    already in the local cache. It also shows ``✓rN`` for a pack installed
    in IDA's signature folder ($IDAUSR/sig).
    """,
}


def list_command(fmt: PackFormat) -> click.Command:
    """``list`` for one format's group (``hcli packs <format> list``)."""

    @auth_command(name="list", help=_LIST_HELP[fmt])
    @click.option("--component", default=None, help="Only packs of this component (e.g. msvc).")
    @click.option("--arch", default=None, help="Only packs for this architecture (e.g. arm64).")
    @json_option
    @packs_errors
    @async_command
    @with_repository
    async def list_packs(repo: PackRepository, component: str | None, arch: str | None, as_json: bool) -> None:
        await _list(repo, fmt, component, arch, as_json)

    return list_packs


async def _list(repo: PackRepository, fmt: PackFormat, component: str | None, arch: str | None, as_json: bool) -> None:
    release = await repo.read_release(fmt)
    root = cache_dir()
    status = local_status(fmt)

    rows = [
        (pack, is_cached(cached_pack_path(pack, root), pack.size))
        for pack in release.packs
        if (component is None or pack.component == component) and (arch is None or pack.arch == arch)
    ]

    if as_json:
        print_json(
            {
                "repository": repo.url,
                "releases": {
                    fmt.value: {
                        "snapshot": release.snapshot,
                        "previous_snapshot": release.previous_snapshot,
                        "published_at": release.published_at,
                    }
                },
                "packs": [pack_json(fmt, pack, cached=cached, **status.pack_json(pack)) for pack, cached in rows],
                **status.summary_json(),
            }
        )
        return

    console.print(Text.assemble(("Repository ", "dim"), repo.url))
    console.print(
        Text.assemble(
            (f"  {fmt.value:<7}", "blue"),
            ("snapshot ", "dim"),
            (short_snapshot(release.snapshot), "cyan"),
            ("  published ", "dim"),
            format_timestamp(release.published_at),
        )
    )
    if (legend := status.legend()) is not None:
        console.print(legend, soft_wrap=True)

    if not rows:
        console.print("No packs match.", style="dim")
        return

    # Compact and unwrapped so a row (and the PACK address, which is
    # copy-pasted into show/get) fits the 80 columns non-terminal output gets.
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=0)
    table.add_column("PACK", style="magenta", no_wrap=True)
    table.add_column("REV", justify="right", no_wrap=True)
    table.add_column("SIZE", justify="right", no_wrap=True)
    table.add_column("CONTENT", justify="right", no_wrap=True)
    table.add_column("BUILT", style="green", no_wrap=True)
    table.add_column("LOCAL", no_wrap=True)
    for pack, cached in rows:
        local = Text("cached", style="green") if cached else Text("-", style="dim")
        local.append_text(status.pack_text(pack))
        table.add_row(
            pack.address,
            f"r{pack.revision}",
            format_mib(pack.size),
            format_content(fmt, pack),
            format_timestamp(pack.built_at, date_only=True),
            local,
        )
    console.print()
    console.print(table)
