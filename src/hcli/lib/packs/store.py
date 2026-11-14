"""The local content-addressed cache and verified, atomic file writes.

Cache layout, under hcli's cache directory (``HCLI_CACHE_DIR`` overrides)::

    packs/<sha256>.tar.zst      pack archives
    packs/<sha256>.jsonl.zst    contents sidecars

Entries are keyed by the sha256 the Release publishes and only ever appear
through a ``VerifiedWriter`` (temp file, size + sha256 check, atomic rename), so
a present entry of the right size is a verified one unless something outside
hcli changed it — ``get`` re-hashes before reusing it anyway.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import time
from collections.abc import Callable, Generator, Iterable
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, BinaryIO, Final

from hcli.lib.packs.errors import IntegrityError, LocalIOError
from hcli.lib.packs.models import MetaReceipt, ReleasePack
from hcli.lib.util.cache import get_cache_directory, get_default_cache_directory

if TYPE_CHECKING:
    # Type-only: typing.Self needs Python 3.11 (pydantic brings typing_extensions).
    from typing_extensions import Self

CACHE_KEY: Final = "packs"
_HASH_CHUNK: Final = 1024 * 1024
_STALE_PARTIAL_AGE: Final = 24 * 60 * 60
_PARTIAL_NAME: Final = re.compile(r"^\..+\.[0-9a-f]{16}\.partial$")
# A fresh download that fails verification: a transient fault, or bad bytes upstream.
_DOWNLOAD_HINT: Final = (
    "re-run the command: the portal may have been updating; if it fails again, report it to Hex-Rays"
)

ProgressCallback = Callable[[int], None]


def cache_dir(*, create: bool = True) -> Path:
    """``<hcli cache>/packs`` (created on first use); ``create=False`` only computes it (a dry run must not create folders)."""
    return get_cache_directory(CACHE_KEY) if create else get_default_cache_directory() / CACHE_KEY


def cached_pack_path(pack: ReleasePack, root: Path | None = None) -> Path:
    return (root or cache_dir()) / f"{pack.sha256}.tar.zst"


def cached_meta_path(receipt: MetaReceipt, root: Path | None = None) -> Path:
    return (root or cache_dir()) / f"{receipt.sha256}.jsonl.zst"


def is_cached(path: Path, size: int) -> bool:
    """Cheap presence check (no hashing): the entry exists with the published size."""
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def check_digest(label: str, *, size: int, sha256: str, actual_size: int, actual_sha256: str, hint: str) -> None:
    """Raise IntegrityError unless the actual size and sha256 match the published ones."""
    if actual_size != size:
        raise IntegrityError(f"{label} does not match the Release: expected {size} bytes, got {actual_size}.", hint)
    if actual_sha256 != sha256:
        raise IntegrityError(
            f"{label} does not match the Release: expected sha256 {sha256}, got {actual_sha256}.",
            hint,
        )


def verify_file(path: Path, *, size: int, sha256: str, label: str) -> None:
    """Verify a cached file against the Release (size first: it is free)."""
    hint = f"the cached copy {path} is corrupt or truncated; re-run with --force"
    with local_io("read", path):
        actual_size = path.stat().st_size
        # A size mismatch is reported before the sha256, so skip hashing then.
        actual_sha256 = sha256_file(path) if actual_size == size else ""
    check_digest(label, size=size, sha256=sha256, actual_size=actual_size, actual_sha256=actual_sha256, hint=hint)


def verify_bytes(data: bytes, *, size: int, sha256: str, label: str) -> None:
    check_digest(
        label,
        size=size,
        sha256=sha256,
        actual_size=len(data),
        actual_sha256=hashlib.sha256(data).hexdigest(),
        hint=_DOWNLOAD_HINT,
    )


@contextlib.contextmanager
def local_io(action: str, path: Path) -> Generator[None, None, None]:
    """Report an OSError from a local file operation as LocalIOError (never a repository error)."""
    try:
        yield
    except OSError as e:
        raise LocalIOError(action, path, e) from e


def _sweep_stale_partials(directory: Path) -> None:
    """Best-effort removal of temp files a killed run (e.g. SIGKILL) left behind."""
    cutoff = time.time() - _STALE_PARTIAL_AGE
    with contextlib.suppress(OSError):
        for entry in directory.iterdir():
            if not _PARTIAL_NAME.match(entry.name):
                continue
            with contextlib.suppress(OSError):
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()


def _temp_sibling(dest: Path) -> tuple[Path, BinaryIO]:
    """A fresh temp file next to ``dest``, so the final rename stays on one filesystem.

    Created with mode 0666 so the umask applies exactly as for a plain
    ``open()``. Temp files are removed on every failure this process sees;
    one killed outright can leave ``.<name>.<random>.partial`` behind, which
    a later write into the same folder removes once it is a day old.
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise LocalIOError("create the folder", dest.parent, e, directory=dest.parent) from e
    _sweep_stale_partials(dest.parent)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    while True:
        temp = dest.parent / f".{dest.name}.{secrets.token_hex(8)}.partial"
        try:
            fd = os.open(temp, flags, 0o666)
        except FileExistsError:
            continue
        except OSError as e:
            raise LocalIOError("create a temporary file for", dest, e) from e
        return temp, os.fdopen(fd, "wb")


class VerifiedWriter:
    """Write chunks to ``dest`` only if together they match ``size`` and ``sha256``.

    A context manager: the bytes land in a temp file beside ``dest`` and are
    renamed into place on a clean exit, after verification, so ``dest`` never
    holds unverified content; on any failure the temp file is removed and
    ``dest`` is left untouched. ``write`` refuses a chunk that takes the total
    past ``size``, so the caller stops reading at once.

    Only the local file operations raise LocalIOError; a failure while
    producing the chunks propagates unchanged for the caller to classify.
    """

    def __init__(
        self, dest: Path, *, size: int, sha256: str, label: str, on_progress: ProgressCallback | None = None
    ) -> None:
        self.dest, self.size, self.sha256, self.label, self.on_progress = dest, size, sha256, label, on_progress
        self._digest = hashlib.sha256()
        self._written = 0

    def __enter__(self) -> Self:
        self._temp, self._out = _temp_sibling(self.dest)
        return self

    def write(self, chunk: bytes) -> None:
        self._written += len(chunk)
        if self._written > self.size:
            raise IntegrityError(
                f"{self.label} does not match the Release: expected {self.size} bytes, got more.", _DOWNLOAD_HINT
            )
        with local_io("write", self.dest):
            self._out.write(chunk)
        self._digest.update(chunk)
        if self.on_progress is not None:
            self.on_progress(len(chunk))

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            if exc_type is None:
                with local_io("write", self.dest):
                    self._out.close()  # flushes: a full disk can surface only here
                check_digest(
                    self.label,
                    size=self.size,
                    sha256=self.sha256,
                    actual_size=self._written,
                    actual_sha256=self._digest.hexdigest(),
                    hint=_DOWNLOAD_HINT,
                )
                with local_io("rename the temporary file to", self.dest):
                    os.replace(self._temp, self.dest)
        finally:
            with contextlib.suppress(OSError):
                self._out.close()
            with contextlib.suppress(OSError):
                self._temp.unlink()


def write_verified(
    chunks: Iterable[bytes],
    dest: Path,
    *,
    size: int,
    sha256: str,
    label: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Write ``chunks`` to ``dest`` through a VerifiedWriter.

    Reading stops as soon as more than ``size`` bytes arrive, and a generator
    passed as ``chunks`` is closed either way (releasing its connection).
    """
    try:
        with VerifiedWriter(dest, size=size, sha256=sha256, label=label, on_progress=on_progress) as writer:
            for chunk in chunks:
                writer.write(chunk)
    finally:
        if isinstance(chunks, Generator):
            chunks.close()


def copy_atomic(source: Path, dest: Path) -> None:
    """Copy ``source`` to ``dest`` via a temp sibling and an atomic rename."""
    temp, out = _temp_sibling(dest)
    try:
        with local_io("copy to", dest), out, source.open("rb") as src:
            shutil.copyfileobj(src, out, _HASH_CHUNK)
        with local_io("rename the temporary file to", dest):
            os.replace(temp, dest)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()


def write_atomic(dest: Path, data: bytes) -> None:
    """Replace ``dest`` with ``data`` via a temp sibling and an atomic rename (readers never see a partial file)."""
    temp, out = _temp_sibling(dest)
    try:
        with local_io("write", dest), out:
            out.write(data)
        with local_io("rename the temporary file to", dest):
            os.replace(temp, dest)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()
