"""Tests for which license files `hcli license get` downloads."""

from __future__ import annotations

import asyncio

import pytest

from hcli.commands.license import common
from hcli.lib.api.license import License


def _downloaded(asset_types: list[str], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Download a license with these asset types and return the asset types requested."""
    requested: list[str] = []

    async def fake_download(_customer_id: str, _license_id: str, asset_type: str, _target_dir: str) -> str:
        requested.append(asset_type)
        return f"{asset_type}.hexlic"

    monkeypatch.setattr(common.license, "download_license", fake_download)

    lic = License(pubhash="96-0000-0000-01", license_key="key", asset_types=asset_types)
    asyncio.run(common.download_license("1", lic, "./", ask_assets=False))
    return requested


def test_skips_ida_90_format(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _downloaded(["hexlic", "hexlic_v90"], monkeypatch) == ["hexlic"]


def test_only_ida_90_format_downloads_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _downloaded(["hexlic_v90"], monkeypatch) == []


def test_other_asset_types_still_download(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _downloaded(["hexlic", "other"], monkeypatch) == ["hexlic", "other"]
