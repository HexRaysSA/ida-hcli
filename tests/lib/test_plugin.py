from hcli.lib.ida.plugin import (
    is_plugin_archive,
    is_binary_plugin_archive,
    is_source_plugin_archive,
)

from fixtures import PLUGIN_DATA


def test_source_plugin_archive():
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf)
    assert is_source_plugin_archive(buf)
    assert not is_binary_plugin_archive(buf)


def test_binary_plugin_archive():
    plugin_path = PLUGIN_DATA / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf)
    assert not is_source_plugin_archive(buf)
    assert is_binary_plugin_archive(buf)
