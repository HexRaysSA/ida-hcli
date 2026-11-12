"""Reading packs and checking them against the Release."""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
import zstandard
from conftest import FIXTURE_PACK, FIXTURE_PRODUCER, LUMINA_FIXTURES, producer

from hcli.lib.packs.errors import IntegrityError
from hcli.lib.packs.lumina import pack as pack_module
from hcli.lib.packs.lumina.pack import MAX_MEMBER_BYTES, Digest, for_each_member, member_sha256, spill_member

ARROW = (LUMINA_FIXTURES / "archive-bundle-sample.arrow").read_bytes()
PRODUCER = FIXTURE_PRODUCER
sha = producer


def tar_bytes(entries: list[tuple[tarfile.TarInfo, bytes | None]], fmt: int = tarfile.USTAR_FORMAT) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=fmt) as archive:
        for info, data in entries:
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return out.getvalue()


def member(name: str, data: bytes = ARROW, kind: bytes = tarfile.REGTYPE) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind == tarfile.REGTYPE:
        info.size = len(data)
        return info, data
    info.linkname = "arrows/x" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
    return info, None


def write_pack(tmp_path: Path, archive: bytes, name: str = "windows-all-x86-r1.tar.zst", trailing: bytes = b"") -> Path:
    path = tmp_path / name
    path.write_bytes(zstandard.ZstdCompressor().compress(archive) + trailing)
    return path


def scan(
    path: Path, visit: Callable[[str, int, Callable[[int], bytes]], None] | None = None
) -> tuple[int, Digest, list]:
    seen: list = []

    def collect(sha256: str, size: int, read: Callable[[int], bytes]) -> None:
        with spill_member(sha256, size, read) as m:
            seen.append((sha256, size, bytes(m.data[:6]), m.digest))

    with path.open("rb") as handle:
        count, digest = for_each_member(path, handle, visit or collect)
    return count, digest, seen


# -- member names --------------------------------------------------------------------


def test_only_arrows_hex_dot_arrow_names_a_member() -> None:
    assert member_sha256(f"arrows/{sha(1)}.arrow".encode()) == sha(1)
    for bad in (
        f"arrows/{sha(0xAB).upper()}.arrow",
        f"arrows/{sha(1)[:-1]}.arrow",
        f"arrows/{sha(1)}0.arrow",
        f"./arrows/{sha(1)}.arrow",
        f"/arrows/{sha(1)}.arrow",
        f"arrows/../{sha(1)}.arrow",
        f"arrows/{sha(1)}.arrow/",
        f"Arrows/{sha(1)}.arrow",
        f"arrows/{sha(1)}.parquet",
        "arrows/.arrow",
    ):
        with pytest.raises(ValueError):
            member_sha256(bad.encode())


# -- scanning ------------------------------------------------------------------------


def test_the_real_fixture_pack_has_its_one_member() -> None:
    count, digest, seen = scan(FIXTURE_PACK)
    assert count == 1
    assert seen[0][0] == PRODUCER and seen[0][2] == b"ARROW1"
    assert digest == Digest(hashlib.sha256(FIXTURE_PACK.read_bytes()).hexdigest(), FIXTURE_PACK.stat().st_size)


def test_members_come_in_archive_order_with_their_digests(tmp_path: Path) -> None:
    datas = [ARROW, ARROW + b"x" * 7, ARROW[:100] + b"\0" * 900]
    path = write_pack(tmp_path, tar_bytes([member(f"arrows/{sha(9 - i)}.arrow", d) for i, d in enumerate(datas)]))
    count, _, seen = scan(path)
    assert count == 3
    assert [s[0] for s in seen] == [sha(9), sha(8), sha(7)]
    assert [s[3] for s in seen] == [Digest(hashlib.sha256(d).hexdigest(), len(d)) for d in datas]


@pytest.mark.parametrize(
    ("entries", "words"),
    [
        ([member("arrows/../evil.arrow")], "is not named by a lowercase hex"),
        ([member(f"/arrows/{sha(1)}.arrow")], "unexpected pack member"),
        ([member(f"arrows/{sha(0xAB).upper()}.arrow")], "lowercase hex"),
        ([member("README")], "unexpected pack member"),
        ([member("arrows/", kind=tarfile.DIRTYPE)], "not a regular file"),
        ([member(f"arrows/{sha(1)}.arrow", kind=tarfile.SYMTYPE)], "not a regular file"),
        ([member(f"arrows/{sha(1)}.arrow", kind=tarfile.LNKTYPE)], "not a regular file"),
        ([member(f"arrows/{sha(1)}.arrow", kind=tarfile.FIFOTYPE)], "not a regular file"),
        ([member(f"arrows/{sha(1)}.arrow"), member(f"arrows/{sha(1)}.arrow")], "duplicate member"),
        ([member(f"arrows/{sha(1)}.arrow", b"ARROW1" + b"\0" * 5)], "outside 18..="),
        ([], "no arrows/ members"),
    ],
)
def test_anything_but_plain_arrow_members_is_refused(tmp_path: Path, entries: list, words: str) -> None:
    with pytest.raises(IntegrityError, match=words):
        scan(write_pack(tmp_path, tar_bytes(entries)))


@pytest.mark.parametrize("fmt", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_extension_headers_are_refused(tmp_path: Path, fmt: int) -> None:
    # A name over 100 bytes needs a PAX header or a GNU long-name entry: refused, not applied.
    long_name = f"arrows/{'a' * 60}/../{sha(1)}.arrow"
    info, data = member(long_name)
    with pytest.raises(IntegrityError, match="not a regular file"):
        scan(write_pack(tmp_path, tar_bytes([(info, data)], fmt=fmt)))


def _raw_header(name: bytes, size: int, kind: bytes = b"0") -> bytes:
    header = bytearray(512)
    header[: len(name)] = name
    header[100:108] = b"0000644\0"
    header[108:116] = b"0000000\0"
    header[116:124] = b"0000000\0"
    if size < 8**11:
        header[124:136] = f"{size:011o}\0".encode()
    else:  # GNU base-256
        header[124:136] = bytes([0x80]) + size.to_bytes(11, "big")
    header[136:148] = b"00000000000\0"
    header[156:157] = kind
    header[257:265] = b"ustar\x0000"
    header[148:156] = b" " * 8
    header[148:156] = f"{sum(header):06o}\0 ".encode()
    return bytes(header)


def test_an_extension_header_is_refused_without_reading_its_huge_body(tmp_path: Path) -> None:
    # A PAX header declaring a terabyte body, with no body at all: refused from the header.
    archive = _raw_header(b"././@PaxHeader", 1 << 40, kind=b"x") + b"\0" * 1024
    with pytest.raises(IntegrityError, match="not a regular file"):
        scan(write_pack(tmp_path, archive))


@pytest.mark.parametrize("size", [MAX_MEMBER_BYTES + 1, 8**11 + 5])
def test_members_over_two_gib_are_refused_from_the_header(tmp_path: Path, size: int) -> None:
    archive = _raw_header(f"arrows/{sha(1)}.arrow".encode(), size) + b"\0" * 1024
    with pytest.raises(IntegrityError, match=r"outside 18\.\.="):
        scan(write_pack(tmp_path, archive))


def test_a_header_with_a_bad_checksum_is_refused(tmp_path: Path) -> None:
    archive = bytearray(tar_bytes([member(f"arrows/{sha(1)}.arrow")]))
    archive[0] ^= 1  # the name changes, the stored checksum does not
    with pytest.raises(IntegrityError, match="bad checksum"):
        scan(write_pack(tmp_path, bytes(archive)))


def test_a_truncated_member_is_refused(tmp_path: Path) -> None:
    archive = tar_bytes([member(f"arrows/{sha(1)}.arrow")])
    with pytest.raises(IntegrityError, match=r"truncated|ends inside"):
        scan(write_pack(tmp_path, archive[: 512 + len(ARROW) // 2]))


def test_non_zstd_input_is_a_verification_failure(tmp_path: Path) -> None:
    path = tmp_path / "x.tar.zst"
    path.write_bytes(tar_bytes([member(f"arrows/{sha(1)}.arrow")]))
    with pytest.raises(IntegrityError, match="zstd"):
        scan(path)


def test_the_scan_digest_covers_the_whole_file_including_trailing_bytes(tmp_path: Path) -> None:
    archive = tar_bytes([member(f"arrows/{sha(1)}.arrow")])
    plain = write_pack(tmp_path, archive, "a.tar.zst")
    padded = write_pack(tmp_path, archive, "b.tar.zst", trailing=zstandard.ZstdCompressor().compress(b"\0" * 4096))
    _, plain_digest, _ = scan(plain)
    _, padded_digest, _ = scan(padded)
    assert plain_digest == Digest(hashlib.sha256(plain.read_bytes()).hexdigest(), plain.stat().st_size)
    assert padded_digest == Digest(hashlib.sha256(padded.read_bytes()).hexdigest(), padded.stat().st_size)


def test_visitor_errors_pass_through_unchanged(tmp_path: Path) -> None:
    path = write_pack(tmp_path, tar_bytes([member(f"arrows/{sha(1)}.arrow")]))

    class Stop(Exception):
        pass

    def visit(*_args: object) -> None:
        raise Stop

    with pytest.raises(Stop):
        scan(path, visit)


def test_a_visitor_that_reads_nothing_still_moves_to_the_next_member(tmp_path: Path) -> None:
    path = write_pack(tmp_path, tar_bytes([member(f"arrows/{sha(1)}.arrow"), member(f"arrows/{sha(2)}.arrow")]))
    seen: list[str] = []
    count, _, _ = scan(path, lambda sha256, _size, _read: seen.append(sha256))
    assert (count, seen) == (2, [sha(1), sha(2)])


# -- spilling members -----------------------------------------------------------------


def test_members_read_the_same_in_memory_and_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    in_memory = scan(FIXTURE_PACK)
    monkeypatch.setattr(pack_module, "IN_MEMORY_MAX", 0)
    mapped = scan(FIXTURE_PACK)
    assert mapped == in_memory
    assert in_memory[2][0][2] == b"ARROW1"


def test_a_mapped_member_is_freed_even_while_a_view_is_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows cannot delete a mapped temp file: a leftover view must not make exit fail.
    monkeypatch.setattr(pack_module, "IN_MEMORY_MAX", 0)
    data = io.BytesIO(ARROW)
    with spill_member(sha(1), len(ARROW), data.read) as m:
        leftover = m.data[:10]
    assert bytes(leftover) == ARROW[:10]


def test_a_short_member_is_refused() -> None:
    data = io.BytesIO(ARROW[:-1])
    with pytest.raises(IntegrityError, match="truncated"), spill_member(sha(1), len(ARROW), data.read):
        pass
