"""Tests for plugin dependency migration during `create-environment`.

These tests exercise the full flow: real plugins on disk, real venvs,
real pip installs. No mocks or monkeypatch.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures import *
from fixtures import PLUGINS_DIR

from hcli.commands.ida.python.create_environment import run_create_environment
from hcli.lib.ida.plugin.install import (
    PluginDependencyInfo,
    collect_plugin_dependencies,
    get_plugin_directory,
    install_plugin_archive,
    install_single_plugin_dependencies,
)

THIS_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"


def _place_plugin(name: str, metadata: dict, entry_point: str = "plugin.py") -> Path:
    """Write a plugin directory directly into $IDAUSR/plugins/."""
    plugin_dir = get_plugin_directory(name)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "ida-plugin.json").write_text(json.dumps(metadata))
    (plugin_dir / entry_point).write_text("# placeholder\n")
    return plugin_dir


def _make_metadata(name: str, version: str = "1.0.0", deps: list[str] | None = None) -> dict:
    return {
        "IDAMetadataDescriptorVersion": 1,
        "plugin": {
            "name": name,
            "version": version,
            "entryPoint": "plugin.py",
            "description": f"test plugin {name}",
            "pythonDependencies": deps or [],
            "urls": {"repository": "https://github.com/HexRaysSA/ida-hcli"},
            "authors": [{"name": "Test", "email": "test@example.com"}],
        },
    }


def _pip_freeze(python_exe: Path) -> str:
    result = subprocess.run(
        [str(python_exe), "-m", "pip", "freeze"],
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="replace")


def _create_env(reinstall_plugins: bool = True):
    return run_create_environment(
        path=None,
        python_version=THIS_VERSION,
        configure=False,
        reinstall_plugins=reinstall_plugins,
        interactive=False,
        quiet=True,
    )


# ---------------------------------------------------------------------------
# collect_plugin_dependencies
# ---------------------------------------------------------------------------


def test_collect_no_plugins(virtual_ida_environment):
    assert collect_plugin_dependencies() == []


def test_collect_skips_plugins_without_deps(virtual_ida_environment):
    _place_plugin("nodeps", _make_metadata("nodeps"))
    assert collect_plugin_dependencies() == []


def test_collect_finds_plugins_with_deps(virtual_ida_environment):
    _place_plugin("withdeps", _make_metadata("withdeps", deps=["packaging>=25.0"]))
    _place_plugin("nodeps", _make_metadata("nodeps"))

    result = collect_plugin_dependencies()
    assert len(result) == 1
    assert result[0].name == "withdeps"
    assert result[0].dependencies == ["packaging>=25.0"]


def test_collect_from_real_plugin_archive(virtual_ida_environment_with_venv):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    result = collect_plugin_dependencies()
    assert len(result) == 1
    assert result[0].name == "plugin1"
    assert "packaging==25.0" in result[0].dependencies


# ---------------------------------------------------------------------------
# install_single_plugin_dependencies
# ---------------------------------------------------------------------------


def test_install_single_plugin_deps_success(virtual_ida_environment_with_venv):
    python_exe = Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"])
    info = PluginDependencyInfo(name="test-plugin", dependencies=["packaging==25.0"])
    result = install_single_plugin_dependencies(python_exe, info)

    assert result.success is True
    assert result.error is None
    assert "packaging==25.0" in _pip_freeze(python_exe)


def test_install_single_plugin_deps_failure(virtual_ida_environment_with_venv):
    python_exe = Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"])
    info = PluginDependencyInfo(
        name="bad-plugin",
        dependencies=["nonexistent-package-that-does-not-exist-xyz==99.99.99"],
    )
    result = install_single_plugin_dependencies(python_exe, info)

    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# Full create-environment migration flow
# ---------------------------------------------------------------------------


@pytest.fixture
def no_ida(tmp_path: Path):
    """An IDA install directory with no idat, so probing IDA's Python fails fast.

    Uses os.environ directly (no monkeypatch) so it works alongside
    virtual_ida_environment's context-manager-based env vars.
    """
    from hcli.env import ENV

    fake = tmp_path / "ida"
    fake.mkdir()
    old_install_dir = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR")
    old_venv_exe = os.environ.get("IDAPYTHON_VENV_EXECUTABLE")
    old_env_install_dir = getattr(ENV, "HCLI_CURRENT_IDA_INSTALL_DIR", None)
    old_env_venv_exe = getattr(ENV, "IDAPYTHON_VENV_EXECUTABLE", None)

    os.environ["HCLI_CURRENT_IDA_INSTALL_DIR"] = str(fake)
    os.environ.pop("IDAPYTHON_VENV_EXECUTABLE", None)
    ENV.HCLI_CURRENT_IDA_INSTALL_DIR = str(fake)
    ENV.IDAPYTHON_VENV_EXECUTABLE = None

    yield

    if old_install_dir is not None:
        os.environ["HCLI_CURRENT_IDA_INSTALL_DIR"] = old_install_dir
    else:
        os.environ.pop("HCLI_CURRENT_IDA_INSTALL_DIR", None)
    if old_venv_exe is not None:
        os.environ["IDAPYTHON_VENV_EXECUTABLE"] = old_venv_exe
    else:
        os.environ.pop("IDAPYTHON_VENV_EXECUTABLE", None)
    ENV.HCLI_CURRENT_IDA_INSTALL_DIR = old_env_install_dir
    ENV.IDAPYTHON_VENV_EXECUTABLE = old_env_venv_exe


def test_create_environment_no_migration_needed(virtual_ida_environment, no_ida):
    result = _create_env()

    assert result.created is True
    assert result.plugin_migrations == []
    assert result.plugins_skipped is False


def test_create_environment_migrates_plugin_deps(virtual_ida_environment, no_ida):
    _place_plugin("withdeps", _make_metadata("withdeps", deps=["packaging==25.0"]))

    result = _create_env()

    assert result.created is True
    assert len(result.plugin_migrations) == 1
    assert result.plugin_migrations[0].name == "withdeps"
    assert result.plugin_migrations[0].success is True
    assert result.plugins_skipped is False

    freeze = _pip_freeze(Path(result.python_exe))
    assert "packaging==25.0" in freeze


def test_create_environment_migration_reports_failure(virtual_ida_environment, no_ida):
    _place_plugin("good", _make_metadata("good", deps=["packaging==25.0"]))
    _place_plugin(
        "bad",
        _make_metadata("bad", deps=["nonexistent-package-that-does-not-exist-xyz==99.99.99"]),
    )

    result = _create_env()

    assert result.created is True
    assert len(result.plugin_migrations) == 2
    assert result.plugins_skipped is False

    by_name = {m.name: m for m in result.plugin_migrations}
    assert by_name["good"].success is True
    assert by_name["bad"].success is False
    assert by_name["bad"].error is not None

    freeze = _pip_freeze(Path(result.python_exe))
    assert "packaging==25.0" in freeze


def test_create_environment_no_reinstall_plugins_skips(virtual_ida_environment, no_ida):
    _place_plugin("withdeps", _make_metadata("withdeps", deps=["markupsafe>=2.0"]))

    result = _create_env(reinstall_plugins=False)

    assert result.created is True
    assert result.plugin_migrations == []
    assert result.plugins_skipped is True

    freeze = _pip_freeze(Path(result.python_exe))
    assert "markupsafe" not in freeze.lower()


def test_create_environment_skips_plugins_without_deps(virtual_ida_environment, no_ida):
    _place_plugin("nodeps", _make_metadata("nodeps"))

    result = _create_env()

    assert result.created is True
    assert result.plugin_migrations == []
    assert result.plugins_skipped is False
