"""Tests for installed-plugin records and case-insensitive lookup."""

import pytest
from fixtures import *
from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_installed_plugin_records,
    install_plugin_archive,
    is_plugin_installed,
    resolve_installed_plugin_directory,
    uninstall_plugin,
)


def test_installed_plugin_records(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    records = get_installed_plugin_records()
    assert len(records) == 1
    record = records[0]
    assert record.name == "plugin1"
    assert record.version == "1.0.0"
    assert record.host == "https://github.com/HexRaysSA/ida-hcli"
    assert record.path.name == "plugin1"


def test_installed_plugin_lookup_is_case_insensitive(virtual_ida_environment):
    """the user may type a different case than the on-disk directory."""
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    expected_path = find_installed_plugin("plugin1").path
    for name in ("plugin1", "PLUGIN1", "Plugin1"):
        assert is_plugin_installed(name)
        assert find_installed_plugin(name).path == expected_path
        assert resolve_installed_plugin_directory(name) == expected_path

    uninstall_plugin("PLUGIN1")
    assert not is_plugin_installed("plugin1")


def test_find_installed_plugin_host_filter(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    # the host in the fixture metadata is https://github.com/HexRaysSA/ida-hcli
    assert find_installed_plugin("plugin1", host="https://github.com/HexRaysSA/ida-hcli").name == "plugin1"
    # case and trailing slash are normalized away
    assert find_installed_plugin("plugin1", host="https://github.com/hexrayssa/ida-hcli/").name == "plugin1"

    with pytest.raises(PluginNotInstalledError):
        find_installed_plugin("plugin1", host="https://github.com/other-org/other-repo")


def test_find_installed_plugin_not_installed(virtual_ida_environment):
    with pytest.raises(PluginNotInstalledError):
        find_installed_plugin("does-not-exist")
