from __future__ import annotations

import pytest
from conftest import make_pack

from hcli.lib.packs.errors import UnknownPackError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.resolve import match_packs, resolve_packs

PACKS = [ReleasePack.model_validate(make_pack(arch, b"p", b"m")) for arch in ("arm64", "x86", "x86_64")]


def _addresses(packs: list[ReleasePack]) -> list[str]:
    return [pack.address for pack in packs]


def test_exact_address() -> None:
    assert _addresses(match_packs(PACKS, "msvc/windows-all-x86", PackFormat.LUMINA)) == ["msvc/windows-all-x86"]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("msvc/*-arm64", ["msvc/windows-all-arm64"]),
        ("msvc/*x86*", ["msvc/windows-all-x86", "msvc/windows-all-x86_64"]),
        ("msvc/*", ["msvc/windows-all-arm64", "msvc/windows-all-x86", "msvc/windows-all-x86_64"]),
        ("msvc/windows-all-x86_6?", ["msvc/windows-all-x86_64"]),
    ],
)
def test_globs(pattern: str, expected: list[str]) -> None:
    assert _addresses(match_packs(PACKS, pattern, PackFormat.LUMINA)) == expected


def test_glob_matching_is_case_sensitive() -> None:
    with pytest.raises(UnknownPackError):
        match_packs(PACKS, "MSVC/*", PackFormat.LUMINA)


def test_unknown_pack_suggests_near_miss_and_lists_available() -> None:
    with pytest.raises(UnknownPackError) as info:
        match_packs(PACKS, "msvc/windows-all-arm46", PackFormat.FLIRT)
    error = info.value
    assert error.kind == "unknown_pack"
    assert "No flirt pack matches 'msvc/windows-all-arm46'" in error.message
    assert "msvc/windows-all-x86_64" in error.message
    assert error.hint == "did you mean msvc/windows-all-arm64?"


def test_unmatched_glob_has_generic_hint() -> None:
    with pytest.raises(UnknownPackError) as info:
        match_packs(PACKS, "gcc/*", PackFormat.LUMINA)
    assert info.value.hint == "pick an address from `hcli packs lumina list`"


def test_resolve_dedupes_in_first_match_order() -> None:
    selected = resolve_packs(PACKS, ["msvc/windows-all-x86_64", "msvc/*"], PackFormat.LUMINA)
    assert _addresses(selected) == ["msvc/windows-all-x86_64", "msvc/windows-all-arm64", "msvc/windows-all-x86"]


def test_resolve_fails_if_any_pattern_misses() -> None:
    with pytest.raises(UnknownPackError):
        resolve_packs(PACKS, ["msvc/*", "nope"], PackFormat.LUMINA)
