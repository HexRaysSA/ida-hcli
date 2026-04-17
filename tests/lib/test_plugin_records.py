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


def test_find_installed_plugin_case_insensitive(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    record_lower = find_installed_plugin("plugin1")
    record_upper = find_installed_plugin("PLUGIN1")
    record_mixed = find_installed_plugin("Plugin1")

    assert record_lower.path == record_upper.path == record_mixed.path
    assert record_lower.name == "plugin1"


def test_find_installed_plugin_with_matching_host(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    # the host in the fixture metadata is https://github.com/HexRaysSA/ida-hcli
    record = find_installed_plugin("plugin1", host="https://github.com/HexRaysSA/ida-hcli")
    assert record.name == "plugin1"

    # case-insensitive via normalization
    record = find_installed_plugin("plugin1", host="https://github.com/hexrayssa/ida-hcli/")
    assert record.name == "plugin1"


def test_find_installed_plugin_host_mismatch(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    with pytest.raises(PluginNotInstalledError):
        find_installed_plugin("plugin1", host="https://github.com/other-org/other-repo")


def test_find_installed_plugin_not_installed(virtual_ida_environment):
    with pytest.raises(PluginNotInstalledError):
        find_installed_plugin("does-not-exist")


def test_resolve_installed_plugin_directory_case_insensitive(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    path_lower = resolve_installed_plugin_directory("plugin1")
    path_upper = resolve_installed_plugin_directory("PLUGIN1")
    assert path_lower == path_upper


def test_case_insensitive_uninstall(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")
    assert is_plugin_installed("plugin1")

    # uninstall with uppercase name should still work
    uninstall_plugin("PLUGIN1")
    assert not is_plugin_installed("plugin1")


def test_is_plugin_installed_case_insensitive(virtual_ida_environment):
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1")

    assert is_plugin_installed("plugin1")
    assert is_plugin_installed("PLUGIN1")
    assert is_plugin_installed("Plugin1")
