"""Getting one verified pack archive onto local disk, for the commands that consume packs (push, install).

A pack is taken from the local cache when a verified copy is there, else
downloaded into a temporary staging folder (``keep``: into the cache), and
staged under its published filename.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn
from rich.text import Text

from hcli.lib.console import console, stderr_console
from hcli.lib.packs.errors import IntegrityError, LocalIOError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository, pack_key
from hcli.lib.packs.store import ProgressCallback, cached_pack_path, copy_atomic

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def download_progress(show: bool, description: str, total: int) -> Generator[ProgressCallback | None, None, None]:
    """A transient progress bar on stderr, or no callback at all."""
    if not show:
        yield None
        return
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=stderr_console,
        transient=True,
    ) as progress:
        task = progress.add_task(description, total=total)
        yield lambda n: progress.update(task, advance=n)


def link_or_copy(source: Path, dest: Path) -> None:
    """Stage ``source`` at ``dest``: a hardlink when the filesystem allows, else a copy."""
    try:
        os.link(source, dest)
    except OSError:
        copy_atomic(source, dest)


@contextlib.contextmanager
def temp_dir(prefix: str) -> Generator[Path, None, None]:
    try:
        context = tempfile.TemporaryDirectory(prefix=prefix)
    except OSError as e:
        raise LocalIOError("create a temporary folder in", Path(tempfile.gettempdir()), e) from e
    with context as name:
        yield Path(name)


class PackSource(str, Enum):
    """Where a staged pack came from; ``would_download`` only in a dry run."""

    CACHED = "cached"  # a verified cache entry
    TEMP = "temp"  # downloaded into the staging folder, deleted afterwards
    DOWNLOADED = "downloaded"  # downloaded into the cache (--keep, or replacing a damaged entry)
    WOULD_DOWNLOAD = "would_download"


SOURCE_TEXT: Final = {
    PackSource.CACHED: "cached",
    PackSource.TEMP: "downloaded to temp",
    PackSource.DOWNLOADED: "downloaded to the cache",
}


@dataclass(frozen=True)
class StagedPack:
    path: Path
    source: PackSource
    # Set when the cached copy was unusable and the pack was downloaded instead.
    warning: str | None = None


async def obtain_pack(
    repo: PackRepository, fmt: PackFormat, pack: ReleasePack, staging: Path, *, keep: bool, as_json: bool
) -> StagedPack:
    """The pack, verified, under its published filename in ``staging``.

    The Lumina import matches a pack file to its Release entry by
    basename, and the cache names packs by sha256, so the pack is staged
    under the name the Release publishes. A cached copy that fails
    verification is never staged: it is replaced in the cache by a fresh
    download (so later runs and ``list`` stop treating it as cached); one
    that cannot be read is bypassed with a download to temp.
    """
    staged = staging / pack.filename
    cached = cached_pack_path(pack)
    replace = False
    warning: str | None = None
    if cached.is_file():
        try:
            await repo.fetch_pack(fmt, pack)  # re-verifies by sha256
        except IntegrityError:
            logger.debug("cached %s failed verification", pack.address, exc_info=True)
            replace = True
            warning = "cached copy failed verification; downloading it again"
        except LocalIOError as e:
            logger.debug("cached %s could not be read", pack.address, exc_info=True)
            warning = f"cached copy could not be read ({e.message}); downloading it again"
        else:
            link_or_copy(cached, staged)
            return StagedPack(staged, PackSource.CACHED)
        if not as_json:
            console.print(
                Text.assemble(
                    ("! ", "yellow"), f"{fmt.value} ", (pack.address, "magenta"), f" r{pack.revision}: {warning}"
                ),
                soft_wrap=True,
            )
    show_progress = not as_json and stderr_console.is_terminal
    with download_progress(show_progress, f"{fmt.value} {pack.address}", pack.size) as on_progress:
        if keep or replace:
            path, _ = await repo.fetch_pack(fmt, pack, force=warning is not None, on_progress=on_progress)
            link_or_copy(path, staged)
            return StagedPack(staged, PackSource.DOWNLOADED, warning)
        await repo.download_verified(
            pack_key(fmt, pack), staged, size=pack.size, sha256=pack.sha256, on_progress=on_progress
        )
    return StagedPack(staged, PackSource.TEMP, warning)
