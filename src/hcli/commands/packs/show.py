"""``hcli packs <format> show`` — one pack's Release entry plus its contents sidecar."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

import rich_click as click
from rich.table import Table
from rich.text import Text

from hcli.commands.packs.common import (
    FORMAT_NAMES,
    SHORT_COMMIT_LEN,
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
from hcli.lib.packs.models import MetaRecord, PackFormat, Release, ReleasePack
from hcli.lib.packs.repository import PackRepository, pack_key
from hcli.lib.packs.resolve import match_packs
from hcli.lib.packs.store import cached_pack_path, is_cached

# Library names are full provenance paths; the part after this marker
# (``<version>/lib/<arch>/foo.lib``) is what tells MSVC libraries apart.
_MSVC_TOOLS_MARKER = "Tools/MSVC/"


def _version_key(value: str) -> tuple[int, tuple[int | str, ...]]:
    """Sort dotted versions numerically; non-numeric values (``Preview``) last."""
    parts = value.split(".")
    if all(part.isdigit() for part in parts):
        return 0, tuple(int(part) for part in parts)
    return 1, (value,)


def toolsets(records: Sequence[MetaRecord]) -> list[str]:
    return sorted({r.toolset for r in records if r.toolset}, key=_version_key)


def ida_versions(records: Sequence[MetaRecord]) -> list[str]:
    return sorted({r.ida_version for r in records if r.ida_version}, key=_version_key)


def short_library_name(name: str) -> str:
    """The part of a library path after ``Tools/MSVC/``, or the full name."""
    _, marker, rest = name.partition(_MSVC_TOOLS_MARKER)
    return rest if marker else name


def _toolset_range(values: Sequence[str]) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return values[0]
    return f"{values[0]} – {values[-1]} ({len(values)} toolsets)"


def _row(label: str, value: str | Text, style: str = "") -> Text:
    return Text.assemble((f"  {label:<12}", "dim"), value if isinstance(value, Text) else (value, style))


def _render_pack(fmt: PackFormat, release: Release, pack: ReleasePack, records: list[MetaRecord], files: bool) -> None:
    sizes = format_mib(pack.size)
    if pack.size_uncompressed is not None:
        sizes += f" ({format_mib(pack.size_uncompressed)} unpacked)"
    built = format_timestamp(pack.built_at)
    if pack.packer_commit:
        built += f" (packer {pack.packer_commit[:SHORT_COMMIT_LEN]})"
    content = format_content(fmt, pack)
    if fmt is PackFormat.LUMINA:
        content = f"{pack.members:,} members, {content}"
    cached = is_cached(cached_pack_path(pack), pack.size)

    console.print(Text.assemble((fmt.value, "blue"), " ", (pack.address, "bold magenta"), f"  r{pack.revision}"))
    console.print(_row("Asset", pack_key(fmt, pack)))
    console.print(_row("SHA256", pack.sha256, "cyan"))
    console.print(_row("Size", sizes))
    console.print(_row("Built", built, "green"))
    console.print(_row("Content", content))
    console.print(_row("Toolsets", _toolset_range(toolsets(records))))
    console.print(_row("IDA", ", ".join(ida_versions(records)) or "-"))
    console.print(
        _row("Snapshot", f"{short_snapshot(release.snapshot)} (previous {short_snapshot(release.previous_snapshot)})")
    )
    console.print(_row("Local", Text("cached", style="green") if cached else Text("-", style="dim")))

    if not files:
        return
    table = Table()
    table.add_column("LIBRARY", style="magenta", overflow="fold")
    table.add_column("TOOLSET")
    table.add_column("ROWS", justify="right")
    for record in records:
        retained = record.rows.retained if record.rows else None
        table.add_row(
            short_library_name(record.name),
            record.toolset or "-",
            f"{retained:,}" if retained is not None else "-",
        )
    console.print(table)


def _pack_detail_json(
    fmt: PackFormat, release: Release, pack: ReleasePack, records: list[MetaRecord], files: bool
) -> dict[str, Any]:
    detail = pack_json(
        fmt,
        pack,
        cached=is_cached(cached_pack_path(pack), pack.size),
        snapshot=release.snapshot,
        previous_snapshot=release.previous_snapshot,
        toolsets=toolsets(records),
        ida_versions=ida_versions(records),
    )
    if files:
        detail["files"] = [record.model_dump(mode="json") for record in records]
    return detail


_SHOW_HELP: Final = """Show a {name} pack's Release entry and contents summary.

    PACK is ``component/platform-group-arch`` (e.g. ``msvc/windows-all-arm64``);
    globs such as ``'msvc/*-arm64'`` show every match.
    """


def show_command(fmt: PackFormat) -> click.Command:
    """``show`` for one format's group (``hcli packs <format> show``)."""

    @auth_command(name="show", help=_SHOW_HELP.format(name=FORMAT_NAMES[fmt]))
    @click.argument("pack")
    @click.option("--files", is_flag=True, default=False, help="Also list every library in the pack.")
    @json_option
    @packs_errors
    @async_command
    @with_repository
    async def show(repo: PackRepository, pack: str, files: bool, as_json: bool) -> None:
        await _show(repo, fmt, pack, files, as_json)

    return show


async def _show(repo: PackRepository, fmt: PackFormat, pack: str, files: bool, as_json: bool) -> None:
    release = await repo.read_release(fmt)
    selected = match_packs(release.packs, pack, fmt)
    details = [(entry, await repo.read_meta(fmt, entry)) for entry in selected]

    if as_json:
        print_json([_pack_detail_json(fmt, release, entry, records, files) for entry, records in details])
        return

    for index, (entry, records) in enumerate(details):
        if index:
            console.print()
        _render_pack(fmt, release, entry, records, files)
