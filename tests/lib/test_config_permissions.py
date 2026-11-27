"""config.json holds the account token, so only the current user may read it (#473)."""

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


@pytest.mark.parametrize("dir_exists", [False, True], ids=["new-dir", "existing-dir"])
def test_save_makes_config_private(config_dir, dir_exists):
    if dir_exists:
        config_dir.mkdir(mode=0o755)

    config_module.ConfigStore().set_string("hcli.credentials", "secret")

    config_file = config_dir / "config.json"
    assert _mode(config_dir) == 0o700
    assert _mode(config_file) == 0o600
    assert json.loads(config_file.read_text())["hcli.credentials"] == "secret"
    # no temporary file is left behind
    assert [p.name for p in config_dir.iterdir()] == ["config.json"]


def test_load_makes_existing_config_private(config_dir):
    # an older version wrote the config with umask-default permissions
    config_dir.mkdir(mode=0o755)
    config_file = config_dir / "config.json"
    # the current version is recorded, so loading does not trigger a migration write
    config_file.write_text(
        json.dumps({f"{ENV.HCLI_BINARY_NAME}.version": ENV.HCLI_VERSION, "hcli.credentials": "secret"})
    )
    before = config_file.stat().st_mtime_ns

    store = config_module.ConfigStore()

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
