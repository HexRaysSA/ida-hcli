"""config.json holds the account token, so it must be private to the user (#473)."""

import json
import os
import stat
from pathlib import Path

import pytest

import hcli.lib.config as config_module
from hcli.env import ENV

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "hcli"
    monkeypatch.setattr(config_module, "user_config_dir", lambda *args, **kwargs: str(path))
    old_umask = os.umask(0o022)
    try:
        yield path
    finally:
        os.umask(old_umask)


def _write_legacy_config(config_dir: Path, data: dict) -> Path:
    """Write a config as older versions did: umask-default 0755 dir and 0644 file."""
    config_dir.mkdir(parents=True)
    os.chmod(config_dir, 0o755)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(data))
    os.chmod(config_file, 0o644)
    return config_file


def test_new_config_is_private(config_dir):
    store = config_module.ConfigStore()
    store.set_string("hcli.credentials", "secret")

    config_file = config_dir / "config.json"
    assert _mode(config_dir) == 0o700
    assert _mode(config_file) == 0o600
    assert json.loads(config_file.read_text())["hcli.credentials"] == "secret"
    assert [p.name for p in config_dir.iterdir()] == ["config.json"]


def test_save_tightens_existing_config(config_dir, monkeypatch):
    config_file = _write_legacy_config(config_dir, {"other": "value"})
    store = config_module.ConfigStore()
    # undo the load-time fix to check that saving alone tightens the permissions
    os.chmod(config_dir, 0o755)
    os.chmod(config_file, 0o644)

    store.set_string("hcli.credentials", "secret")

    assert _mode(config_dir) == 0o700
    assert _mode(config_file) == 0o600
    data = json.loads(config_file.read_text())
    assert data["other"] == "value"
    assert data["hcli.credentials"] == "secret"


def test_load_tightens_existing_config(config_dir):
    # current version recorded, so loading does not trigger a migration write
    config_file = _write_legacy_config(
        config_dir, {f"{ENV.HCLI_BINARY_NAME}.version": ENV.HCLI_VERSION, "hcli.credentials": "secret"}
    )
    before = config_file.stat().st_mtime_ns

    store = config_module.ConfigStore()

    assert store.has("hcli.credentials")
    assert store.get_string("hcli.credentials") == "secret"
    assert config_file.stat().st_mtime_ns == before
    assert _mode(config_dir) == 0o700
    assert _mode(config_file) == 0o600


def test_save_keeps_symlinked_config(config_dir, tmp_path):
    real_file = tmp_path / "dotfiles" / "hcli-config.json"
    real_file.parent.mkdir()
    real_file.write_text("{}")
    config_dir.mkdir()
    (config_dir / "config.json").symlink_to(real_file)

    config_module.ConfigStore().set_string("hcli.credentials", "secret")

    assert (config_dir / "config.json").is_symlink()
    assert json.loads(real_file.read_text())["hcli.credentials"] == "secret"
    assert _mode(real_file) == 0o600
