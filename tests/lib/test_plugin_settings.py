import logging
import os
import subprocess
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace

import pytest
from fixtures import *
from fixtures import (
    PLUGINS_DIR,
    install_this_package_in_venv,
    run_hcli,
    temp_env_var,
)

from hcli.lib.ida.plugin import settings as plugin_settings

logger = logging.getLogger(__name__)


def test_get_current_plugin_from_editable_checkout_callback(tmp_path, monkeypatch):
    """Map a resolved source frame back through its editable install symlink."""
    plugins_dir = tmp_path / "plugins"
    source_dir = tmp_path / "plugin-checkout"
    plugins_dir.mkdir()
    source_dir.mkdir()
    plugin_link = plugins_dir / "plugin-directory"
    try:
        plugin_link.symlink_to(source_dir, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    metadata = SimpleNamespace(plugin=SimpleNamespace(name="canonical-plugin-name"))
    metadata_paths = []
    monkeypatch.setattr(plugin_settings, "get_plugins_directory", lambda: plugins_dir)

    def get_metadata(plugin_directory):
        metadata_paths.append(plugin_directory)
        return metadata

    monkeypatch.setattr(plugin_settings, "get_metadata_from_plugin_directory", get_metadata)

    # Editable plugins can resolve their import path before loading UI modules,
    # making a later Qt callback's co_filename point directly into the checkout.
    module_code = compile(
        "def callback():\n    return get_current_plugin()\n",
        str(source_dir / "plugin_ui.py"),
        "exec",
    )
    callback_code = next(const for const in module_code.co_consts if isinstance(const, CodeType))
    callback = FunctionType(callback_code, {"get_current_plugin": plugin_settings.get_current_plugin})

    assert callback() == "canonical-plugin-name"
    assert metadata_paths == [plugin_link]


def test_plugin_settings_integration(virtual_ida_environment_with_venv):
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    repo = f"plugin --repo {PLUGINS_DIR.absolute()}"

    with temp_env_var("TERM", "dumb"), temp_env_var("COLUMNS", "80"):
        p = run_hcli(f"{repo} install plugin1==4.0.0")
        assert "Installed plugin: plugin1==4.0.0\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "No settings defined for plugin1\n" == p.stdout

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} config plugin1 set foo bar")
        assert "Error: 'unknown setting: foo'\n" == e.value.stdout

        _ = run_hcli(f"{repo} uninstall plugin1")

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} install plugin1==5.0.0")
        assert (
            e.value.stdout
            == "Error: plugin requires configuration but console is not interactive. Please \nprovide settings via command line: --config key1=<value>\n"
        )

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} install plugin1==5.0.0 --config foo=bar")
        assert "Error: 'unknown setting: foo'\n" == e.value.stdout

        p = run_hcli(f"{repo} install plugin1==5.0.0 --config key1=bar")
        assert "Installed plugin: plugin1==5.0.0\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "Key" in p.stdout and "Value" in p.stdout and "Description" in p.stdout
        assert "key1" in p.stdout and "bar" in p.stdout and "the value for key 1" in p.stdout
        assert "key2" in p.stdout and "default-2 (default)" in p.stdout and "the value for key 2" in p.stdout

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} config plugin1 set key2 baz")
        assert "Error: failed to validate setting value: plugin1: key2: 'baz'" in e.value.stdout

        p = run_hcli(f"{repo} config plugin1 set key2 default-3")
        assert "Set plugin1.key2\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "key2" in p.stdout and "default-3" in p.stdout

        with pytest.raises(subprocess.CalledProcessError) as e:
            p = run_hcli(f"{repo} config plugin1 del key1")
        assert "Error: cannot delete required setting without default: plugin1: key1\n" == e.value.stdout

        p = run_hcli(f"{repo} config plugin1 del key2")
        assert "Deleted plugin1.key2\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "key1" in p.stdout and "bar" in p.stdout
        assert "key2" in p.stdout and "default-2 (default)" in p.stdout
        assert "key3" in p.stdout and "false (default)" in p.stdout and "the value for key 3" in p.stdout

        p = run_hcli(f"{repo} config plugin1 set key3 true")
        assert "Set plugin1.key3\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 get key3")
        assert "true\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 set key3 false")
        assert "Set plugin1.key3\n" == p.stdout

        # a stored `false` must survive round-tripping, not read back as unset
        p = run_hcli(f"{repo} config plugin1 get key3")
        assert "false\n" == p.stdout

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} config plugin1 set key3 invalid")
        assert "Error: mismatching settings types" in e.value.stdout

        p = run_hcli(f"{repo} config plugin1 del key3")
        assert "Deleted plugin1.key3\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "key3" in p.stdout and "false (default)" in p.stdout
        assert "key4" in p.stdout and "option-a (default)" in p.stdout and "the value for key 4" in p.stdout

        p = run_hcli(f"{repo} config plugin1 set key4 option-b")
        assert "Set plugin1.key4\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "key4" in p.stdout and "option-b" in p.stdout

        with pytest.raises(subprocess.CalledProcessError) as e:
            _ = run_hcli(f"{repo} config plugin1 set key4 invalid-option")
        assert "Error: failed to validate setting value: plugin1: key4: 'invalid-option'" in e.value.stdout
        assert "option-a, option-b, option-c" in e.value.stdout

        p = run_hcli(f"{repo} config plugin1 del key4")
        assert "Deleted plugin1.key4\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "key4" in p.stdout and "option-a (default)" in p.stdout
        assert "key5" in p.stdout and "hidden-default (default)" in p.stdout
        assert "key6" in p.stdout and "********" in p.stdout and "secret-default" not in p.stdout

        _ = run_hcli(f"{repo} uninstall plugin1")


def test_plugin_with_falsy_default_installs_noninteractive(virtual_ida_environment_with_venv):
    """A required boolean setting with default=false should not block non-interactive install."""
    idausr = Path(os.environ["HCLI_IDAUSR"])
    install_this_package_in_venv(idausr / "venv")

    repo = f"plugin --repo {PLUGINS_DIR.absolute()}"

    with temp_env_var("TERM", "dumb"), temp_env_var("COLUMNS", "80"):
        p = run_hcli(f"{repo} install plugin1==6.0.0")
        assert "Installed plugin: plugin1==6.0.0\n" == p.stdout

        p = run_hcli(f"{repo} config plugin1 list")
        assert "enabled" in p.stdout and "false (default)" in p.stdout

        _ = run_hcli(f"{repo} uninstall plugin1")
