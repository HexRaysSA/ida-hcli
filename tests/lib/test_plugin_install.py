import json
import logging
import os
import subprocess
from pathlib import Path

import pytest
from fixtures import *  # noqa
from fixtures import (
    PLUGINS_DIR,
    install_this_package_in_venv,
    run_hcli,
    temp_env_var,
)

from hcli.lib.ida.plugin.install import (
    get_installed_plugins,
    get_plugin_directory,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.python import pip_freeze

logger = logging.getLogger(__name__)


def test_install_source_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    plugin_directory = get_plugin_directory("plugin1")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "plugin1.py").exists()

    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_install_binary_plugin_archive(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "zydisinfo")

    plugin_directory = get_plugin_directory("zydisinfo")
    assert plugin_directory.exists()
    assert (plugin_directory / "ida-plugin.json").exists()
    assert (plugin_directory / "zydisinfo.dll").exists()
    assert (plugin_directory / "zydisinfo.so").exists()
    assert (plugin_directory / "zydisinfo.dylib").exists()

    assert ("zydisinfo", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("zydisinfo")


def test_uninstall(virtual_ida_environment):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    uninstall_plugin("plugin1")
    assert ("plugin1", "1.0.0") not in get_installed_plugins()
    assert not is_plugin_installed("zydisinfo")


def test_upgrade(virtual_ida_environment):
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()

    install_plugin_archive(v1, "plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    upgrade_plugin_archive(v2, "plugin1")
    assert ("plugin1", "2.0.0") in get_installed_plugins()
    assert is_plugin_installed("plugin1")

    uninstall_plugin("plugin1")

    install_plugin_archive(v2, "plugin1")
    with pytest.raises(ValueError):
        # this is a downgrade
        upgrade_plugin_archive(v1, "plugin1")


def test_plugin_python_dependencies(virtual_ida_environment_with_venv):
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()

    install_plugin_archive(buf, "plugin1")

    freeze = pip_freeze(Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"]))
    assert "packaging==25.0" in freeze


def test_plugin_all(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    with temp_env_var("TERM", "dumb"):
        with temp_env_var("COLUMNS", "240"):
            p = run_hcli("--help")
            assert "Usage: python -m hcli.main [OPTIONS] COMMAND [ARGS]..." in p.stdout

            p = run_hcli("plugin --help")
            assert "Usage: python -m hcli.main plugin [OPTIONS] COMMAND [ARGS]..." in p.stdout

            p = run_hcli(f"plugin --repo {PLUGINS_DIR.absolute()} repo snapshot")
            assert "plugin1" in p.stdout
            assert "zydisinfo" in p.stdout
            assert "1.0.0" in p.stdout
            assert "4.0.0" in p.stdout
            # ensure it looks like json
            _ = json.loads(p.stdout)

            repo_path = idausr / "repo.json"
            repo_path.write_text(p.stdout)

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert "No plugins found\n" == p.stdout

            # current platform: macos-aarch64
            # current version: 9.1
            #
            # plugin1    4.0.0    https://github.com/HexRaysSA/ida-hcli
            # zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli
            p = run_hcli(f"plugin --repo {repo_path.absolute()} search")
            assert "plugin1    5.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout
            assert "zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydis")
            assert "zydisinfo  1.0.0    https://github.com/HexRaysSA/ida-hcli" in p.stdout
            assert "plugin1    5.0.0    https://github.com/HexRaysSA/ida-hcli" not in p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo")
            assert "name: zydisinfo" in p.stdout
            assert "available versions:\n 1.0.0" in p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} search zydisinfo==1.0.0")
            assert "name: zydisinfo" in p.stdout
            assert "download locations:\n IDA: 9.0-9.2  platforms: all  URL: file://" in p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} install zydisinfo")
            assert "Installed plugin: zydisinfo==1.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert " zydisinfo  1.0.0   \n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall zydisinfo")
            assert "Uninstalled plugin: zydisinfo\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert "No plugins found\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} install plugin1==1.0.0")
            assert "Installed plugin: plugin1==1.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert " plugin1  1.0.0  upgradable to 5.0.0 \n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==2.0.0")
            assert "Installed plugin: plugin1==2.0.0\n" == p.stdout

            # downgrade not supported
            with pytest.raises(subprocess.CalledProcessError) as e:
                p = run_hcli(f"plugin --repo {repo_path.absolute()} upgrade plugin1==1.0.0")
                assert (
                    e.value.stdout
                    == "Error: Cannot upgrade plugin plugin1: new version 1.0.0 is not greater than existing version 2.0.0\n"
                )

            # TODO: upgrade all

            p = run_hcli(f"plugin --repo {repo_path.absolute()} status")
            assert " plugin1  2.0.0  upgradable to 5.0.0 \n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
            assert "Uninstalled plugin: plugin1\n" == p.stdout

            p = run_hcli(
                f"plugin --repo {repo_path.absolute()} install {(PLUGINS_DIR / 'plugin1' / 'plugin1-v3.0.0.zip').absolute()}"
            )
            assert "Installed plugin: plugin1==3.0.0\n" == p.stdout

            p = run_hcli(f"plugin --repo {repo_path.absolute()} uninstall plugin1")
            assert "Uninstalled plugin: plugin1\n" == p.stdout

            # install from file:// path URI
            p = run_hcli(
                f"plugin --repo {repo_path.absolute()} install {(PLUGINS_DIR / 'plugin1' / 'plugin1-v4.0.0.zip').absolute().as_uri()}"
            )
            assert "Installed plugin: plugin1==4.0.0\n" == p.stdout

            # TODO: install by URL
            # which will require a plugin archive with a single plugin

            # work with the default index
            # if `hint-calls` becomes unmaintained, this plugin name can be changed.
            # the point is just to show the default index works.
            p = run_hcli("plugin search hint-ca")
            assert " hint-calls  " in p.stdout

            p = run_hcli("plugin install hint-calls")
            assert "Installed plugin: hint-calls==" in p.stdout
