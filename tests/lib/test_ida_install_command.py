from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from hcli.commands.download import download
from hcli.lib.api.asset import Asset

install_module = importlib.import_module("hcli.commands.ida.install")
install = install_module.install


@pytest.mark.asyncio
async def test_download_callback_returns_downloaded_paths(monkeypatch):
    async def fake_get_file(_bucket: str, key: str) -> Asset:
        return Asset(filename=Path(key).name, key=key, url="https://example.invalid/ida.run")

    class FakeClient:
        async def download_file(self, *args, **kwargs) -> str:
            return "/tmp/ida.run"

    async def fake_get_api_client():
        return FakeClient()

    monkeypatch.setattr("hcli.commands.download.asset_api.get_file", fake_get_file)
    monkeypatch.setattr("hcli.commands.download.get_api_client", fake_get_api_client)

    result = await download.callback(key="release/9.2/ida-pro/ida-pro_92_x64linux.run")

    assert result == ["/tmp/ida.run"]


@pytest.mark.asyncio
async def test_install_uses_dedicated_temp_dir_and_downloaded_installer_path(monkeypatch, tmp_path):
    download_call: dict[str, str] = {}
    install_call: dict[str, Path] = {}

    class FakeAuthService:
        def init(self):
            return None

    async def fake_download_callback(output_dir: str, key: str):
        download_call["output_dir"] = output_dir
        download_call["key"] = key

        selected = Path(output_dir) / "ida-pro_92_x64linux.run"
        selected.write_text("installer", encoding="utf-8")

        decoy = Path(output_dir) / "older-decoy.run"
        decoy.write_text("decoy", encoding="utf-8")

        return [str(selected)]

    def fake_install_ida(installer_path: Path, install_dir_path: Path) -> None:
        install_call["installer_path"] = installer_path
        install_call["install_dir_path"] = install_dir_path

    monkeypatch.setattr(install_module, "get_auth_service", lambda: FakeAuthService())
    monkeypatch.setattr(install_module, "enforce_login", lambda: True)
    monkeypatch.setattr(install_module.download, "callback", fake_download_callback)
    monkeypatch.setattr(install_module, "install_ida", fake_install_ida)
    monkeypatch.setattr(
        install_module.IdaProduct,
        "from_installer_filename",
        lambda _name: SimpleNamespace(major=9, minor=2, product=None),
    )

    with click.Context(install).scope():
        await install.callback(
            install_dir=str(tmp_path / "ida-install"),
            eula=False,
            installer=None,
            download_id="ida-pro:latest",
            license_id=None,
            set_default=False,
            dry_run=False,
            auto_confirm=True,
        )

    assert download_call["key"] == "ida-pro:latest"
    assert download_call["output_dir"] != tempfile.gettempdir()
    assert Path(download_call["output_dir"]).name.startswith("hcli-ida-install-")
    assert install_call["installer_path"] == Path(download_call["output_dir"]) / "ida-pro_92_x64linux.run"
