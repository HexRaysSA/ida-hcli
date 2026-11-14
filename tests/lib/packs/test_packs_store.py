from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from conftest import META_RECORD, FakeBackend, contents_key, make_meta, make_pack, pack_key, sha256_hex

from hcli.lib.packs.errors import IntegrityError, LocalIOError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.store import (
    cached_meta_path,
    cached_pack_path,
    copy_atomic,
    is_cached,
    verify_file,
    write_verified,
)

PAYLOAD = b"pack archive bytes " * 10
META = make_meta([META_RECORD])
PACK = ReleasePack.model_validate(make_pack("arm64", PAYLOAD, META))
PACK_KEY = pack_key(make_pack("arm64", PAYLOAD, META))
META_KEY = contents_key(make_pack("arm64", PAYLOAD, META))


def _repo(objects: dict[str, bytes] | None = None) -> tuple[PackRepository, FakeBackend]:
    backend = FakeBackend({PACK_KEY: PAYLOAD, META_KEY: META} if objects is None else objects)
    return PackRepository(backend), backend


def test_write_verified_exposes_only_matching_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    progress: list[int] = []
    write_verified([b"ab", b"cd"], dest, size=4, sha256=sha256_hex(b"abcd"), label="x", on_progress=progress.append)
    assert dest.read_bytes() == b"abcd"
    assert progress == [2, 2]


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([b"abc"], "expected 4 bytes, got 3"),
        ([b"abce"], "expected sha256"),
    ],
)
def test_write_verified_rejects_mismatch_without_leaving_files(
    tmp_path: Path, chunks: list[bytes], message: str
) -> None:
    dest = tmp_path / "out.bin"
    with pytest.raises(IntegrityError, match=message) as info:
        write_verified(chunks, dest, size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert info.value.kind == "integrity"
    assert list(tmp_path.iterdir()) == []


def test_verify_file_reports_truncation_before_hashing(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    with pytest.raises(IntegrityError, match="expected 4 bytes, got 3") as info:
        verify_file(path, size=4, sha256=sha256_hex(b"abcd"), label="Cached x")
    assert info.value.hint is not None and "--force" in info.value.hint


def test_fetch_pack_downloads_then_reuses_cache(cache_root: Path) -> None:
    repo, backend = _repo()

    path, downloaded = asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert downloaded
    assert path == cache_root / f"{PACK.sha256}.tar.zst"
    assert path.read_bytes() == PAYLOAD
    assert is_cached(cached_pack_path(PACK), PACK.size)

    path_again, downloaded_again = asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert (path_again, downloaded_again) == (path, False)
    assert backend.reads == [PACK_KEY]

    _, forced = asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK, force=True))
    assert forced
    assert backend.reads == [PACK_KEY, PACK_KEY]


def test_fetch_pack_refuses_corrupt_cache_unless_forced(cache_root: Path) -> None:
    repo, _ = _repo()
    path, _ = asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    path.write_bytes(b"X" + PAYLOAD[1:])

    with pytest.raises(IntegrityError, match="does not match the Release"):
        asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))

    _, downloaded = asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK, force=True))
    assert downloaded
    assert path.read_bytes() == PAYLOAD


def test_fetch_pack_rejects_tampered_download(cache_root: Path) -> None:
    repo, _ = _repo({PACK_KEY: PAYLOAD[:-1] + b"!"})
    with pytest.raises(IntegrityError, match="expected sha256"):
        asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert not cached_pack_path(PACK).exists()


def test_read_meta_verifies_and_caches(cache_root: Path) -> None:
    repo, backend = _repo()
    records = asyncio.run(repo.read_meta(PackFormat.LUMINA, PACK))
    assert [record.toolset for record in records] == ["14.29"]
    assert asyncio.run(repo.read_meta(PackFormat.LUMINA, PACK)) == records
    assert backend.reads == [META_KEY]


def test_read_meta_rejects_mismatched_sidecar(cache_root: Path) -> None:
    repo, _ = _repo({META_KEY: make_meta([META_RECORD, META_RECORD])})
    with pytest.raises(IntegrityError):
        asyncio.run(repo.read_meta(PackFormat.LUMINA, PACK))


class _Stream:
    """A chunk generator that records whether it was closed, optionally failing midway."""

    def __init__(self, chunks: list[bytes], fail_after: int | None = None) -> None:
        self.chunks = chunks
        self.fail_after = fail_after
        self.closed = False

    def generate(self) -> Generator[bytes, None, None]:
        try:
            for index, chunk in enumerate(self.chunks):
                if index == self.fail_after:
                    raise ConnectionResetError(54, "Connection reset by peer")
                yield chunk
        finally:
            self.closed = True


def test_write_verified_stops_and_closes_on_overrun(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    stream = _Stream([b"ab", b"cd", b"ef", b"gh"])
    with pytest.raises(IntegrityError, match="expected 4 bytes, got more") as info:
        write_verified(stream.generate(), dest, size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert stream.closed
    assert info.value.hint is not None and "report it to Hex-Rays" in info.value.hint
    assert list(tmp_path.iterdir()) == []


def test_write_verified_mid_stream_failure_leaves_dest_untouched(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"previous")
    stream = _Stream([b"ab", b"cd"], fail_after=1)
    # Not a LocalIOError: a transport failure propagates for the caller to classify.
    with pytest.raises(ConnectionResetError):
        write_verified(stream.generate(), dest, size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert stream.closed
    assert dest.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [dest]


def test_fresh_download_mismatch_hint_suggests_a_retry(tmp_path: Path) -> None:
    with pytest.raises(IntegrityError) as info:
        write_verified([b"abce"], tmp_path / "x", size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert info.value.hint == (
        "re-run the command: the portal may have been updating; if it fails again, report it to Hex-Rays"
    )


def test_write_verified_reports_local_failures_as_local_io(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_bytes(b"")
    with pytest.raises(LocalIOError) as info:
        write_verified([b"abcd"], blocker / "out.bin", size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert info.value.kind == "local_io"
    assert str(tmp_path) in (info.value.hint or "")


def test_write_verified_rename_failure_is_local_io(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    dest.mkdir()  # os.replace cannot put a file over a directory
    with pytest.raises(LocalIOError, match="Could not rename the temporary file to"):
        write_verified([b"abcd"], dest, size=4, sha256=sha256_hex(b"abcd"), label="x")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out.bin"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_copy_atomic_copies_with_umask_mode(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    dest = tmp_path / "out" / "nested" / "copy.bin"
    old_mask = os.umask(0o027)
    try:
        copy_atomic(source, dest)
    finally:
        os.umask(old_mask)
    assert dest.read_bytes() == b"payload"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640
    assert [path.name for path in dest.parent.iterdir()] == ["copy.bin"]


def test_copy_atomic_overwrites_and_reports_missing_source(tmp_path: Path) -> None:
    dest = tmp_path / "copy.bin"
    dest.write_bytes(b"old")
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    copy_atomic(source, dest)
    assert dest.read_bytes() == b"new"

    with pytest.raises(LocalIOError):
        copy_atomic(tmp_path / "missing.bin", dest)
    assert dest.read_bytes() == b"new"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["copy.bin", "source.bin"]


def test_stale_partials_are_swept_fresh_ones_kept(tmp_path: Path) -> None:
    stale = tmp_path / ".copy.bin.0123456789abcdef.partial"
    fresh = tmp_path / ".copy.bin.fedcba9876543210.partial"
    unrelated = tmp_path / "notes.partial"
    for path in (stale, fresh, unrelated):
        path.write_bytes(b"")
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (two_days_ago, two_days_ago))
    source = tmp_path / "source.bin"
    source.write_bytes(b"x")

    copy_atomic(source, tmp_path / "copy.bin")

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_download_local_failure_is_not_a_repository_error(cache_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _ = _repo()

    def full_disk(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("hcli.lib.packs.store.os.replace", full_disk)
    with pytest.raises(LocalIOError, match="No space left on device") as info:
        asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert info.value.kind == "local_io"
    assert str(cache_root) in (info.value.hint or "")


def test_needs_download(cache_root: Path) -> None:
    repo, _ = _repo()
    assert repo.needs_download(PACK)
    asyncio.run(repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert not repo.needs_download(PACK)
    assert repo.needs_download(PACK, force=True)


def test_read_meta_refetches_corrupt_cached_sidecar(cache_root: Path) -> None:
    repo, backend = _repo()
    asyncio.run(repo.read_meta(PackFormat.LUMINA, PACK))
    cached = cached_meta_path(PACK.meta)
    cached.write_bytes(b"X" + META[1:])

    records = asyncio.run(repo.read_meta(PackFormat.LUMINA, PACK))

    assert [record.toolset for record in records] == ["14.29"]
    assert backend.reads == [META_KEY, META_KEY]
    assert cached.read_bytes() == META
