"""Tests for the rename-aside binary replacement in update_asset()."""

import os
import stat
from pathlib import Path

import pytest

from hcli.lib.update.release import GitHubRepo, ReleaseAsset, update_asset


def _fake_download_asset(content: bytes):
    """Return a download_asset replacement that writes *content* to the expected path."""

    def _download(_repo, asset, out_dir, _block_size=2**20, _callback=lambda *_: None):
        out_path = Path(out_dir) / asset.name
        out_path.write_bytes(content)

    return _download


REPO = GitHubRepo(user="test", repo="test")
ASSET = ReleaseAsset(asset_id=1, name="hcli", size=7)


class TestUpdateAssetRenameAside:
    def test_replaces_binary_with_new_content(self, tmp_path, monkeypatch):
        binary = tmp_path / "hcli"
        binary.write_bytes(b"old-bin")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

        monkeypatch.setattr(
            "hcli.lib.update.release.download_asset",
            _fake_download_asset(b"new-bin"),
        )

        update_asset(REPO, ASSET, binary)

        assert binary.read_bytes() == b"new-bin"
        assert not binary.with_suffix(".old").exists()

    def test_preserves_file_mode(self, tmp_path, monkeypatch):
        binary = tmp_path / "hcli"
        binary.write_bytes(b"old-bin")
        binary.chmod(0o755)
        original_mode = binary.stat().st_mode

        monkeypatch.setattr(
            "hcli.lib.update.release.download_asset",
            _fake_download_asset(b"new-bin"),
        )

        update_asset(REPO, ASSET, binary)

        assert binary.stat().st_mode == original_mode

    def test_restores_backup_on_move_failure(self, tmp_path, monkeypatch):
        binary = tmp_path / "hcli"
        binary.write_bytes(b"old-bin")

        def _download_then_delete(_repo, asset, out_dir, _block_size=2**20, _callback=lambda *_: None):
            out_path = Path(out_dir) / asset.name
            out_path.write_bytes(b"new-bin")
            os.remove(out_path)

        monkeypatch.setattr(
            "hcli.lib.update.release.download_asset",
            _download_then_delete,
        )

        with pytest.raises(FileNotFoundError):
            update_asset(REPO, ASSET, binary)

        assert binary.read_bytes() == b"old-bin"

    def test_works_when_tmp_on_different_filesystem(self, tmp_path, monkeypatch):
        binary = tmp_path / "hcli"
        binary.write_bytes(b"old-bin")

        monkeypatch.setattr(
            "hcli.lib.update.release.download_asset",
            _fake_download_asset(b"new-bin"),
        )

        update_asset(REPO, ASSET, binary)

        assert binary.read_bytes() == b"new-bin"
        assert not binary.with_suffix(".old").exists()
