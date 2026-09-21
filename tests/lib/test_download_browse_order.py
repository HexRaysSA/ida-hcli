"""Tests for the ordering of the interactive `hcli download` browser."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hcli.commands import download
from hcli.lib.api.asset import Asset, TreeNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file(name: str, key: str = "") -> TreeNode:
    return TreeNode(name=name, type="file", asset=Asset(filename=name, key=key or name))


def _folder(name: str, children: list[TreeNode] | None = None) -> TreeNode:
    return TreeNode(
        name=name,
        type="folder",
        asset=Asset(filename="", key=""),
        children=children if children is not None else [_file(f"{name}.zip")],
    )


def _titles_shown(nodes: list[TreeNode], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run the traversal once and return the choice titles it would display."""
    captured: list[list[str]] = []

    def fake_select(_message: str, **kwargs: Any) -> object:
        captured.append([choice.title for choice in kwargs["choices"]])
        return object()

    async def fake_ask(_question: object, *_args: Any, **_kwargs: Any) -> None:
        return None  # user aborts right after the first prompt is built

    monkeypatch.setattr(download.questionary, "select", fake_select)
    monkeypatch.setattr(download, "safe_ask_async", fake_ask)

    asyncio.run(download.select_asset(nodes))

    assert captured, "select_asset never built a prompt"
    return captured[0]


# The order the API returns today: a plain lexicographic sort, so 3.1.10 lands
# before 3.1.7 and 3.2.10-3.2.12 land between 3.2.1 and 3.2.2.
API_ORDER = [
    "3.1.7",
    "3.1.8",
    "3.1.9",
    "3.1.10",
    "3.2.1",
    "3.2.2",
    "3.2.10",
    "3.2.12",
    "4.0.0",
    "4.0.2",
]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class TestBrowseOrder:
    def test_folders_listed_newest_version_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Shuffled into the lexicographic order the API actually hands us.
        api_order = ["3.1.10", "3.1.7", "3.1.8", "3.1.9", "3.2.1", "3.2.10", "3.2.12", "3.2.2", "4.0.0", "4.0.2"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == [f"📁 {name}" for name in reversed(API_ORDER)]

    def test_go_back_stays_pinned_at_the_top(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tree = [_folder("eap", children=[_folder("9.0"), _folder("9.1"), _folder("9.10")])]
        captured: list[list[str]] = []

        def fake_select(_message: str, **kwargs: Any) -> object:
            captured.append([choice.title for choice in kwargs["choices"]])
            return object()

        answers = iter([tree[0], None])  # descend into "eap", then abort

        async def fake_ask(_question: object, *_args: Any, **_kwargs: Any) -> Any:
            return next(answers)

        monkeypatch.setattr(download.questionary, "select", fake_select)
        monkeypatch.setattr(download, "safe_ask_async", fake_ask)

        asyncio.run(download.select_asset(tree))

        assert captured[1] == ["← Go back", "📁 9.10", "📁 9.1", "📁 9.0"]

    def test_files_sorted_newest_first_after_folders(self, monkeypatch: pytest.MonkeyPatch) -> None:
        nodes = [
            _file("idapro_9.1.run"),
            _folder("extras"),
            _file("idapro_9.10.run"),
            _folder("betas"),
            _file("idapro_9.2.run"),
        ]
        titles = _titles_shown(nodes, monkeypatch)

        assert titles == [
            "📁 betas",
            "📁 extras",
            "📄 idapro_9.10.run",
            "📄 idapro_9.2.run",
            "📄 idapro_9.1.run",
        ]

    def test_service_packs_sort_above_their_base_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api_order = ["9.0", "9.0sp1", "9.1", "9.10", "9.2"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 9.10", "📁 9.2", "📁 9.1", "📁 9.0sp1", "📁 9.0"]

    def test_named_folders_stay_alphabetical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Category levels hold plain names, not versions: reversing them would
        # read backwards for no benefit, so they keep normal A-Z order.
        api_order = ["teams", "sdk-and-utilities", "ida-pro", "installers"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == [
            "📁 ida-pro",
            "📁 installers",
            "📁 sdk-and-utilities",
            "📁 teams",
        ]

    def test_versioned_entries_precede_named_ones_when_a_level_mixes_both(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No level is known to mix the two today; this pins the fallback so the
        # ordering stays predictable if one ever does.
        titles = _titles_shown([_folder(name) for name in ["archive", "9.1", "9.2"]], monkeypatch)

        assert titles == ["📁 9.2", "📁 9.1", "📁 archive"]


# ---------------------------------------------------------------------------
# Prereleases
# ---------------------------------------------------------------------------


class TestPrereleaseOrder:
    def test_prerelease_listed_below_its_final_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # SemVer: 1.0.0-beta.10 *precedes* 1.0.0, so the final release is the
        # newer of the two and belongs on top.
        api_order = ["1.0.0", "1.0.0-beta.10", "1.0.1"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 1.0.1", "📁 1.0.0", "📁 1.0.0-beta.10"]

    def test_prerelease_tags_ordered_by_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # rc outranks beta outranks alpha, which is the reverse of alphabetical.
        api_order = ["1.0.0-alpha.1", "1.0.0-beta.2", "1.0.0-rc.1"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 1.0.0-rc.1", "📁 1.0.0-beta.2", "📁 1.0.0-alpha.1"]

    def test_numeric_prerelease_fields_compare_as_numbers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api_order = ["1.0.0-beta.10", "1.0.0-beta.2", "1.0.0-beta.9"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 1.0.0-beta.10", "📁 1.0.0-beta.9", "📁 1.0.0-beta.2"]

    def test_longer_prerelease_outranks_its_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # SemVer: a larger set of prerelease fields wins when the prefix ties.
        api_order = ["1.0.0-beta", "1.0.0-beta.1"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 1.0.0-beta.1", "📁 1.0.0-beta"]

    def test_service_pack_suffix_is_not_treated_as_a_prerelease(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Only a hyphen marks a prerelease. 9.0sp1 is a *later* release than
        # 9.0 and must keep sorting above it.
        api_order = ["9.0", "9.0sp1", "9.0-beta.1"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 9.0sp1", "📁 9.0", "📁 9.0-beta.1"]

    def test_hyphen_outside_a_version_is_not_a_prerelease(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A hyphen only marks a prerelease when it follows a version number, so
        # hyphenated category names keep plain A-Z order.
        api_order = ["sdk-and-utilities", "ida-pro", "ida"]
        titles = _titles_shown([_folder(name) for name in api_order], monkeypatch)

        assert titles == ["📁 ida", "📁 ida-pro", "📁 sdk-and-utilities"]
