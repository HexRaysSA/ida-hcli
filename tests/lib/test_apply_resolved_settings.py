"""Tests for the library-level apply_resolved_settings function."""

from fixtures import *
from fixtures import PLUGINS_DIR, make_test_install_context

from hcli.lib.ida.plugin import get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.install import install_plugin_archive
from hcli.lib.ida.plugin.settings import apply_resolved_settings, get_plugin_setting


def test_apply_resolved_settings_writes_values(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1", ctx)
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    ok = apply_resolved_settings("plugin1", metadata, {"key1": "hello"})
    assert ok is True
    assert get_plugin_setting("plugin1", "key1") == "hello"


def test_apply_resolved_settings_skips_defaults(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1", ctx)
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    ok = apply_resolved_settings("plugin1", metadata, {"key2": "default-2"})
    assert ok is True


def test_apply_resolved_settings_returns_false_on_unknown_key(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1", ctx)
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    ok = apply_resolved_settings("plugin1", metadata, {"nonexistent": "val"})
    assert ok is False


def test_apply_resolved_settings_empty_dict(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    install_plugin_archive(buf, "plugin1", ctx)
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    ok = apply_resolved_settings("plugin1", metadata, {})
    assert ok is True
