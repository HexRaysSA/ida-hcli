from __future__ import annotations

import json

import pytest
from conftest import META_RECORD, make_meta, make_pack, make_release

from hcli.lib.packs.errors import InvalidReleaseError
from hcli.lib.packs.models import PackFormat, ReleasePack, is_safe_segment, parse_meta, parse_release


def _encode(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def test_release_parses_and_ignores_unknown_fields() -> None:
    pack = make_pack("arm64", b"pack", b"meta", future_field={"nested": True})
    release = parse_release(_encode(make_release([pack], extra="x")), PackFormat.LUMINA, "lumina/release")

    entry = release.packs[0]
    assert release.snapshot.startswith("7d96eefa")
    assert entry.address == "msvc/windows-all-arm64"
    assert entry.filename == "windows-all-arm64-r2.tar.zst"
    assert entry.rows == 42
    assert entry.meta.size == len(b"meta")


def test_flirt_release_has_null_rows() -> None:
    pack = make_pack("x86", b"pack", b"meta", fmt="flirt")
    release = parse_release(_encode(make_release([pack], fmt="flirt")), PackFormat.FLIRT, "flirt Release")
    assert release.packs[0].rows is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not json", "not valid JSON"),
        (b"[]", "not a JSON object"),
        (_encode(make_release([], schema_version=2)), "schema_version 2"),
        (_encode(make_release([], fmt="flirt")), "belongs to repository 'flirt'"),
        (_encode(make_release([make_pack("arm64", b"p", b"m", path="../../etc/passwd")])), "malformed"),
        (_encode(make_release([make_pack("arm64", b"p", b"m", path="/abs/x.tar.zst")])), "malformed"),
        (_encode(make_release([make_pack("arm64", b"p", b"m", sha256="ABC")])), "malformed"),
    ],
)
def test_release_rejects_bad_documents(payload: bytes, message: str) -> None:
    with pytest.raises(InvalidReleaseError, match=message) as info:
        parse_release(payload, PackFormat.LUMINA, "Release")
    assert info.value.kind == "invalid_release"


def test_meta_parses_records_and_defaults_missing_fields() -> None:
    old_style = {key: value for key, value in META_RECORD.items() if key not in ("toolset", "ida_version", "license")}
    old_style["rows"] = {"input": 3, "eligible": 2, "duplicates": 0, "retained": 2}
    flirt = {**META_RECORD, "rows": None, "unknown": 1}

    records = parse_meta(make_meta([META_RECORD, old_style, flirt]), "meta")

    assert [record.toolset for record in records] == ["14.29", None, "14.29"]
    assert records[0].rows is not None and records[0].rows.retained == 1045
    assert records[1].rows is not None and records[1].rows.conflicts == 0
    assert records[2].rows is None


def test_meta_rejects_garbage() -> None:
    with pytest.raises(InvalidReleaseError, match="not a valid zstd"):
        parse_meta(b"definitely not zstd", "meta")


@pytest.mark.parametrize(
    "path",
    [
        "packs/msvc/windows-all-arm64-r2.tar.zst",
        "packs/msvc/windows-all-x86_64-r2.tar.zst",
        f"meta/msvc/{'0' * 64}.jsonl.zst",
        "packs/gcc+arm/a_b-c.1",
    ],
)
def test_real_repository_paths_are_accepted(path: str) -> None:
    pack = ReleasePack.model_validate(make_pack("arm64", b"p", b"m", path=path))
    assert pack.path == path


@pytest.mark.parametrize(
    "path",
    [
        "packs/msvc/C:evil.tar.zst",
        "packs/msvc/CON",
        "packs/msvc/nul.txt",
        "packs/msvc/Com1.tar.zst",
        "packs/msvc/a.",
        "packs/msvc/a ",
        "packs/msvc/.hidden",
        "packs/msvc/../x",
        "packs\\msvc\\x",
        "packs//x",
        "packs/msvc/x\n",
        "",
    ],
)
def test_unportable_paths_are_rejected(path: str) -> None:
    with pytest.raises(InvalidReleaseError, match="unsafe repository path"):
        parse_release(_encode(make_release([make_pack("arm64", b"p", b"m", path=path)])), PackFormat.LUMINA, "R")


@pytest.mark.parametrize("segment", ["C:x", "CON", "con", "nul.txt", "NUL.tar.zst", "LPT9", "a.", "a ", "..", ""])
def test_unsafe_segments(segment: str) -> None:
    assert not is_safe_segment(segment)


@pytest.mark.parametrize("segment", ["console", "nullable.txt", "com10", "msvc", "x86_64"])
def test_safe_segments(segment: str) -> None:
    assert is_safe_segment(segment)


@pytest.mark.parametrize("component", ["C:", "..", "a/b", "AUX"])
def test_unsafe_component_is_rejected(component: str) -> None:
    with pytest.raises(InvalidReleaseError, match="unsafe path segment"):
        parse_release(
            _encode(make_release([make_pack("arm64", b"p", b"m", component=component)])), PackFormat.LUMINA, "R"
        )


def test_output_path_separates_formats_and_components() -> None:
    pack = ReleasePack.model_validate(make_pack("arm64", b"p", b"m"))
    assert str(pack.output_path(PackFormat.LUMINA)) == "lumina/msvc/windows-all-arm64-r2.tar.zst"
    assert str(pack.output_path(PackFormat.FLIRT)) == "flirt/msvc/windows-all-arm64-r2.tar.zst"
