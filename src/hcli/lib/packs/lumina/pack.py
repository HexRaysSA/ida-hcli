"""Reading a Lumina pack (``<platform>-<group>-<arch>-r<n>.tar.zst``), member by member.

A pack is zstd, then a plain ustar archive whose every entry is a regular file
``arrows/<producer sha256>.arrow``. Anything else is a verification failure:
other paths (traversal and absolute paths can never match that shape),
links, directories, PAX and GNU long-name headers (refused from their header,
their bodies never read), duplicates, and members outside the size bounds.

Each member is copied out once into memory, or, above IN_MEMORY_MAX, into a
temp file read through mmap, and hashed; every compressed byte of the file is
hashed too, so a second pass can prove it read the same pack.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import mmap
import re
import tempfile
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, cast

import zstandard

from hcli.lib.packs.errors import IntegrityError, LocalIOError
from hcli.lib.packs.store import local_io

MAX_MEMBER_BYTES: Final = 2 << 30
MIN_MEMBER_BYTES: Final = 8 + 4 + 6
MAX_MEMBERS: Final = 1_000_000
# Members up to this size stay in memory; bigger ones go to a temp file read
# through mmap. Mapping a fresh file costs milliseconds on macOS, so mapping
# every small member would dominate a push.
IN_MEMORY_MAX: Final = 64 << 20
_CHUNK: Final = 1 << 20
_BLOCK: Final = 512
_MEMBER: Final = re.compile(rb"arrows/([0-9a-f]{64})\.arrow")
_REGULAR: Final = (b"0", b"\0")
VERIFY_HINT: Final = "the pack or the Release failed verification; re-run, and if it fails again report it to Hex-Rays"


def verification(message: str) -> IntegrityError:
    return IntegrityError(message, VERIFY_HINT)


def rust_debug(text: str) -> str:
    """A string quoted as Rust's ``{:?}`` does, so messages match the pack builder's importer."""
    return json.dumps(text, ensure_ascii=False)


@dataclass(frozen=True)
class Digest:
    """The sha256 and size of some bytes."""

    sha256: str
    size: int


class _Hashing:
    """A reader that hashes and counts every byte read through it."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner
        self._hash = hashlib.sha256()
        self._size = 0

    def read(self, size: int = -1) -> bytes:
        data = self._inner.read(size)
        self._hash.update(data)
        self._size += len(data)
        return data

    def drain(self) -> None:
        while self.read(_CHUNK):
            pass

    def digest(self) -> Digest:
        return Digest(self._hash.hexdigest(), self._size)


def member_sha256(path: bytes) -> str:
    """The producer sha256 a member path names; the path must be exactly ``arrows/<64 lowercase hex>.arrow``."""
    match = _MEMBER.fullmatch(path)
    if match is None:
        shown = rust_debug(path.decode("utf-8", "replace"))
        if path.startswith(b"arrows/") and path.endswith(b".arrow"):
            raise ValueError(f"pack member {shown} is not named by a lowercase hex SHA-256")
        raise ValueError(f"unexpected pack member {shown}")
    return match.group(1).decode("ascii")


def _number(field: bytes) -> int:
    """A tar numeric field: octal text, or GNU base-256 for large values."""
    if field[:1] and field[0] & 0x80:
        value = field[0] & 0x7F
        for byte in field[1:]:
            value = (value << 8) | byte
        return value
    text = field.split(b"\0", 1)[0].strip(b" ")
    if not text or any(c not in b"01234567" for c in text):
        raise ValueError(f"bad numeric field {field!r} in a tar header")
    return int(text, 8)


def _entry_path(header: bytes) -> bytes:
    name = header[:100].split(b"\0", 1)[0]
    if header[257:265] == b"ustar\x0000":  # POSIX ustar: the name may continue in the prefix
        prefix = header[345:500].split(b"\0", 1)[0]
        if prefix:
            return prefix + b"/" + name
    return name


def _check_header(header: bytes) -> tuple[str, int]:
    """The member's producer sha256 and size, from its raw header; the body is not read yet."""
    if _number(header[148:156]) != sum(header[:148]) + 8 * ord(" ") + sum(header[156:]):
        raise ValueError("a tar header has a bad checksum")
    path = _entry_path(header)
    kind = header[156:157]
    if kind not in _REGULAR:
        shown = rust_debug(path.decode("utf-8", "replace"))
        raise ValueError(f"pack member {shown} is not a regular file (type {kind.decode('latin-1')!r})")
    sha256 = member_sha256(path)
    size = _number(header[124:136])
    if not MIN_MEMBER_BYTES <= size <= MAX_MEMBER_BYTES:
        raise ValueError(f"pack member {sha256} has {size} bytes, outside {MIN_MEMBER_BYTES}..={MAX_MEMBER_BYTES}")
    return sha256, size


class _Exact:
    """Reads up to ``n`` bytes from a stream, looping over short reads."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def read(self, n: int) -> bytes:
        parts: list[bytes] = []
        while n > 0:
            chunk = self._stream.read(n)
            if not chunk:
                break
            parts.append(chunk)
            n -= len(chunk)
        return b"".join(parts)


MemberVisitor = Callable[[str, int, Callable[[int], bytes]], None]


def for_each_member(path: Path, handle: BinaryIO, visit: MemberVisitor) -> tuple[int, Digest]:
    """Visit every member from the start of ``handle``, in archive order: ``visit(sha256, size, read)``.

    Returns the member count and the digest of every compressed byte, through
    the end of the file. Archive and member-shape errors are IntegrityError;
    errors from ``visit`` pass through unchanged.
    """
    handle.seek(0)
    hashing = _Hashing(handle)
    seen: set[str] = set()

    def failed(reason: str) -> IntegrityError:
        return verification(f"pack {path}: {reason}")

    try:
        tar = _Exact(zstandard.ZstdDecompressor().stream_reader(cast(BinaryIO, hashing), read_across_frames=True))
        while True:
            header = tar.read(_BLOCK)
            if len(header) < _BLOCK:
                raise failed("read tar entry: the archive ends inside a header")
            if not any(header):
                break  # end of archive
            sha256, size = _check_header(header)
            if len(seen) >= MAX_MEMBERS:
                raise failed(f"more than {MAX_MEMBERS} members")
            if sha256 in seen:
                raise failed(f"duplicate member for {sha256}")
            seen.add(sha256)
            remaining = size

            def read(n: int) -> bytes:
                nonlocal remaining
                data = tar.read(min(n, remaining))
                remaining -= len(data)
                return data

            visit(sha256, size, read)
            while remaining and read(_CHUNK):
                pass
            if remaining or len(tar.read(-size % _BLOCK)) != -size % _BLOCK:
                raise failed(f"pack member {sha256} is truncated")
        if not seen:
            raise failed("no arrows/ members")
        hashing.drain()  # whatever follows the archive is part of the file's checksum
    except zstandard.ZstdError as e:
        raise failed(f"read pack: not a valid zstd stream ({e})") from e
    except ValueError as e:
        raise failed(str(e)) from e
    except OSError as e:
        raise LocalIOError("read", path, e) from e
    return len(seen), hashing.digest()


@dataclass
class Member:
    """A member's bytes, in memory or mapped from a temp file, and their digest."""

    data: memoryview
    digest: Digest


@contextlib.contextmanager
def spill_member(sha256: str, size: int, read: Callable[[int], bytes]) -> Generator[Member, None, None]:
    """Copy a member out of the archive, checking its byte count; frees it on exit.

    Above IN_MEMORY_MAX the bytes go to a temp file mapped read-only. On exit
    the views and the mapping are released before the file closes, so Windows
    can delete the temp file; a view still referenced elsewhere only delays that.
    """
    digest = hashlib.sha256()
    spill = Path(tempfile.gettempdir()) / sha256  # for messages: the temp file has no name
    handle: BinaryIO | None = None
    mapped: mmap.mmap | None = None
    view: memoryview | None = None
    buffer = bytearray(size) if size <= IN_MEMORY_MAX else None  # filled in place: no copy, no doubled peak
    written = 0
    try:
        if buffer is None:
            with local_io("create a temporary file for", spill):
                handle = tempfile.TemporaryFile()  # noqa: SIM115 - closed in the finally below
        while chunk := read(_CHUNK):
            if written + len(chunk) > size:
                raise verification(f"pack member {sha256} is longer than its {size} bytes")
            if handle is not None:
                with local_io("write", spill):
                    handle.write(chunk)
            elif buffer is not None:
                buffer[written : written + len(chunk)] = chunk
            digest.update(chunk)
            written += len(chunk)
        if written != size:
            raise verification(f"pack member {sha256} is truncated ({written} of {size} bytes)")
        if handle is None:
            view = memoryview(buffer if buffer is not None else b"")
        else:
            with local_io("write", spill):
                handle.flush()
            mapped = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
            view = memoryview(mapped)
        yield Member(view, Digest(digest.hexdigest(), size))
    finally:
        if view is not None:
            with contextlib.suppress(BufferError):
                view.release()
        if mapped is not None:
            with contextlib.suppress(BufferError):
                mapped.close()
        if handle is not None:
            handle.close()
