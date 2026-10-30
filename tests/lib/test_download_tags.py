"""Tests for tag resolution in `hcli download` and `hcli ida install --download-id`."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from hcli.commands import download
from hcli.lib.api.asset import Tag

# `hcli.commands.ida` re-exports the `install` command, which shadows the module of that name.
install_module = importlib.import_module("hcli.commands.ida.install")


def _tag(tag: str, key: str) -> Tag:
    return Tag(
        tag=tag, description=tag, bucket="installers", key=key, category="ida-pro", channel="release", version="9.5"
    )


def _patch_get_tags(monkeypatch: pytest.MonkeyPatch, tags: list[Tag] | Exception) -> list[int]:
    """Make the tags API return `tags` (or raise it); return a list counting the calls."""
    calls: list[int] = []

    async def fake_get_tags() -> list[Tag]:
        calls.append(1)
        if isinstance(tags, Exception):
            raise tags
        return tags

    monkeypatch.setattr(download.asset_api, "get_tags", fake_get_tags)
    return calls


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if anything tries to fetch an asset after tag resolution."""

    async def boom(*_args, **_kwargs):
        raise AssertionError("must not fetch an asset for an unknown tag")

    monkeypatch.setattr(download.asset_api, "get_file", boom)
    monkeypatch.setattr(download, "get_api_client", boom)


async def _download(key: str, tmp_path: Path) -> list[str]:
    # Awaited from inside a running loop, the async_command wrapper hands back the coroutine.
    return await download.download.callback(output_dir=str(tmp_path), key=key)


# ---------------------------------------------------------------------------
# resolve_tag
# ---------------------------------------------------------------------------


def test_resolve_tag_prefers_exact_match_over_case_insensitive():
    tags = [_tag("IDA-PRO:9.5:armmac", "upper"), _tag("ida-pro:9.5:armmac", "lower")]

    assert download.resolve_tag(tags, "ida-pro:9.5:armmac") == "lower"
    assert download.resolve_tag(tags, "IDA-PRO:9.5:armmac") == "upper"


def test_resolve_tag_rejects_partial_and_prefix_matches():
    tags = [_tag("ida-pro:9.5:armmac", "k")]

    assert download.resolve_tag(tags, "ida-pro:9.5") is None
    assert download.resolve_tag(tags, "ida-pro:9.5:arm") is None
    assert download.resolve_tag(tags, "ida-pro:9.5:armmac:extra") is None
    assert download.resolve_tag(tags, "ida-pro:9:armmac") is None
    assert download.resolve_tag([], "ida-pro:9.5:armmac") is None


# ---------------------------------------------------------------------------
# hcli download <tag>
# ---------------------------------------------------------------------------


def test_unknown_tag_raises_instead_of_returning_empty(monkeypatch, tmp_path):
    _patch_get_tags(monkeypatch, [_tag("ida-free:9.5:armmac", "k")])
    _no_network(monkeypatch)

    with pytest.raises(ValueError, match=r"tag 'ida-essential:9\.5:armmac' not found"):
        asyncio.run(_download("ida-essential:9.5:armmac", tmp_path))


def test_unknown_tag_raises_even_when_no_tags_are_available(monkeypatch, tmp_path):
    _patch_get_tags(monkeypatch, [])
    _no_network(monkeypatch)

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(_download("ida-essential:9.5:armmac", tmp_path))


def test_unknown_tag_fetches_the_tag_list_only_once(monkeypatch, tmp_path):
    calls = _patch_get_tags(monkeypatch, [_tag(f"ida-free:9.{i}:armmac", f"k{i}") for i in range(30)])
    _no_network(monkeypatch)

    with pytest.raises(ValueError):
        asyncio.run(_download("ida-essential:9.5:armmac", tmp_path))

    assert len(calls) == 1


def test_tags_api_failure_is_not_reported_as_unknown_tag(monkeypatch, tmp_path):
    _patch_get_tags(monkeypatch, RuntimeError("503 Service Unavailable"))
    _no_network(monkeypatch)

    with pytest.raises(RuntimeError, match="503") as excinfo:
        asyncio.run(_download("ida-essential:9.5:armmac", tmp_path))

    assert "not found" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# hcli ida install --download-id <tag>
# ---------------------------------------------------------------------------


def test_install_with_unknown_tag_reports_the_tag_not_a_missing_file(monkeypatch, tmp_path):
    """EA-865: the user saw 'No installer file found … after download' for an unknown tag."""
    _patch_get_tags(monkeypatch, [_tag("ida-free:9.5:armmac", "k")])
    _no_network(monkeypatch)

    class _Auth:
        def init(self) -> None:
            pass

    monkeypatch.setattr(install_module, "get_auth_service", _Auth)
    monkeypatch.setattr(install_module, "enforce_login", lambda: True)

    result = CliRunner().invoke(
        install_module.install,
        ["--download-id", "ida-essential:9.5:armmac", "--install-dir", str(tmp_path / "ida"), "--dry-run", "--yes"],
    )

    assert result.exit_code != 0
    assert "No installer file found" not in result.output
    assert "No installer file found" not in str(result.exception)
    assert "ida-essential:9.5:armmac" in result.output
    assert not (tmp_path / "ida").exists()
