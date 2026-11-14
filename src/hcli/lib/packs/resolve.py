"""PACK address resolution: ``component/platform-group-arch``, globs allowed."""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase

from hcli.lib.packs.errors import UnknownPackError
from hcli.lib.packs.models import PackFormat, ReleasePack

_GLOB_CHARS = frozenset("*?[")


def is_glob(pattern: str) -> bool:
    return any(char in _GLOB_CHARS for char in pattern)


def match_packs(packs: Sequence[ReleasePack], pattern: str, fmt: PackFormat) -> list[ReleasePack]:
    """The packs a single address or glob selects, in Release order.

    Raises UnknownPackError listing what exists, with a did-you-mean for a
    near miss, when nothing matches.
    """
    if is_glob(pattern):
        matched = [pack for pack in packs if fnmatchcase(pack.address, pattern)]
    else:
        matched = [pack for pack in packs if pack.address == pattern]
    if matched:
        return matched

    addresses = [pack.address for pack in packs]
    available = ", ".join(addresses) if addresses else "none"
    raise UnknownPackError(
        f"No {fmt.value} pack matches {pattern!r} (available: {available}).",
        _did_you_mean(pattern, addresses, f"pick an address from `hcli packs {fmt.value} list`"),
    )


def _did_you_mean(value: str, candidates: Sequence[str], fallback: str) -> str:
    close = difflib.get_close_matches(value, candidates, n=1, cutoff=0.6)
    return f"did you mean {close[0]}?" if close else fallback


def resolve_packs(packs: Sequence[ReleasePack], patterns: Iterable[str], fmt: PackFormat) -> list[ReleasePack]:
    """Every pack selected by any of ``patterns``, deduplicated, in first-match order."""
    selected: dict[str, ReleasePack] = {}
    for pattern in patterns:
        for pack in match_packs(packs, pattern, fmt):
            selected.setdefault(pack.address, pack)
    return list(selected.values())


def filter_packs(
    packs: Sequence[ReleasePack], fmt: PackFormat, *, component: str | None = None, arch: str | None = None
) -> list[ReleasePack]:
    """The packs of ``component`` and ``arch`` (None: any), in Release order.

    A filter value no pack has (a typo) raises UnknownPackError naming the
    values that exist, as does a combination that matches nothing.
    """
    fallback = f"pick one from `hcli packs {fmt.value} list`"
    for name, value, values in (
        ("component", component, sorted({pack.component for pack in packs})),
        ("arch", arch, sorted({pack.arch for pack in packs})),
    ):
        if value is not None and value not in values:
            available = ", ".join(values) if values else "none"
            raise UnknownPackError(
                f"No {fmt.value} pack has {name} {value!r} (available: {available}).",
                _did_you_mean(value, values, fallback),
            )
    selected = [
        pack
        for pack in packs
        if (component is None or pack.component == component) and (arch is None or pack.arch == arch)
    ]
    if not selected and (component is not None or arch is not None):
        raise UnknownPackError(
            f"No {fmt.value} pack has component {component!r} and arch {arch!r}.",
            f"drop one of the filters; {fallback.removeprefix('pick one from ')} shows what exists",
        )
    return selected
