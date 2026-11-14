"""``hcli packs <format> get`` — verified pack downloads through the local cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import rich_click as click
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.text import Text

from hcli.commands.packs.common import FORMAT_NAMES, json_option, packs_errors, with_repository
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json, stderr_console
from hcli.lib.packs.errors import InvalidReleaseError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.resolve import resolve_packs
from hcli.lib.packs.store import copy_atomic


def output_targets(output_dir: Path, fmt: PackFormat, packs: list[ReleasePack]) -> list[tuple[ReleasePack, Path]]:
    """Each pack's local target, ``<output_dir>/<format>/<component>/<filename>``, checked up front.

    Raises before anything is downloaded when a target would leave the
    output folder (the models already reject unsafe names; this is the
    belt to their braces) or when two packs would overwrite each other.
    """
    root = output_dir.resolve()
    targets: list[tuple[ReleasePack, Path]] = []
    # Keyed case-insensitively: macOS and Windows folders usually are.
    claimed: dict[str, ReleasePack] = {}
    for pack in packs:
        target = output_dir / pack.output_path(fmt)
        resolved = target.resolve()
        if not resolved.is_relative_to(root):
            raise InvalidReleaseError(
                f"Refusing to write {pack.address} to {target}: outside the output folder {output_dir}.",
                "the Release names an unsafe path; report it to Hex-Rays",
            )
        key = str(resolved).casefold()
        if (other := claimed.get(key)) is not None:
            raise InvalidReleaseError(
                f"{other.address} and {pack.address} would both be written to {target}.",
                "download them one at a time into different folders, and report the Release to Hex-Rays",
            )
        claimed[key] = pack
        targets.append((pack, target))
    return targets


async def _fetch_with_progress(
    repo: PackRepository, fmt: PackFormat, pack: ReleasePack, force: bool, show_progress: bool
) -> tuple[Path, bool]:
    """``fetch_pack`` with a per-pack progress bar on stderr (transient), shown only when downloading."""
    if not show_progress or not repo.needs_download(pack, force):
        return await repo.fetch_pack(fmt, pack, force=force)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=stderr_console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"{fmt.value} {pack.address}", total=pack.size)
        return await repo.fetch_pack(fmt, pack, force=force, on_progress=lambda n: progress.update(task, advance=n))


_GET_HELP: Final = """Download {name} packs, verified by size and sha256 against the stable Release.

    PACKS are ``component/platform-group-arch`` addresses or globs (e.g.
    ``msvc/windows-all-x86_64``, ``'msvc/*'``). Each pack is downloaded into
    the local cache (keyed by sha256), verified before it is exposed, then
    copied to ``<output-dir>/{fmt}/<component>/`` under its published
    filename, unchanged (e.g. ``packs/{fmt}/msvc/windows-all-arm64-r2.tar.zst``).
    """


def get_command(fmt: PackFormat) -> click.Command:
    """``get`` for one format's group (``hcli packs <format> get``)."""

    @auth_command(name="get", help=_GET_HELP.format(name=FORMAT_NAMES[fmt], fmt=fmt.value))
    @click.argument("packs", nargs=-1, required=True)
    @click.option(
        "--output-dir",
        "-o",
        type=click.Path(file_okay=False, path_type=Path),
        default=Path("packs"),
        show_default=True,
        help=f"Folder the packs are copied to, as {fmt.value}/<component>/<published filename>.",
    )
    @click.option(
        "--force",
        "-f",
        is_flag=True,
        default=False,
        help="Re-download even when a verified copy is cached.",
    )
    @json_option
    @packs_errors
    @async_command
    @with_repository
    async def get(repo: PackRepository, packs: tuple[str, ...], output_dir: Path, force: bool, as_json: bool) -> None:
        await _get(repo, fmt, packs, output_dir, force, as_json)

    return get


async def _get(
    repo: PackRepository, fmt: PackFormat, packs: tuple[str, ...], output_dir: Path, force: bool, as_json: bool
) -> None:
    release = await repo.read_release(fmt)
    # Resolve every argument and target before downloading anything: a typo
    # or a clash fails fast.
    selected = resolve_packs(release.packs, packs, fmt)
    targets = output_targets(output_dir, fmt, selected)

    results: list[dict[str, Any]] = []
    for pack, target in targets:
        cached, downloaded = await _fetch_with_progress(
            repo, fmt, pack, force, show_progress=not as_json and stderr_console.is_terminal
        )
        copy_atomic(cached, target)
        results.append(
            {
                "format": fmt.value,
                "address": pack.address,
                "revision": pack.revision,
                "sha256": pack.sha256,
                "size": pack.size,
                "path": str(target),
                "cache_path": str(cached),
                "downloaded": downloaded,
            }
        )
        if not as_json:
            status = "downloaded" if downloaded else "already cached"
            console.print(
                Text.assemble(
                    ("✓ ", "green"),
                    (pack.address, "magenta"),
                    f" r{pack.revision}: {status}, sha256 ✓ → ",
                    (str(target), "cyan"),
                ),
                soft_wrap=True,
            )

    if as_json:
        print_json({"repository": repo.url, "snapshot": release.snapshot, "packs": results})
