from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin import (
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_plugin_archive,
    is_source_plugin_archive,
)


def test_source_plugin_archive():
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "plugin1")
    assert is_source_plugin_archive(buf, "plugin1")
    assert not is_binary_plugin_archive(buf, "plugin1")


def test_binary_plugin_archive():
    plugin_path = PLUGINS_DIR / "zydisinfo" / "zydisinfo-v1.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "zydisinfo")
    assert not is_source_plugin_archive(buf, "zydisinfo")
    assert is_binary_plugin_archive(buf, "zydisinfo")


def test_is_ida_version_compatible():
    # Test exact version matches
    assert is_ida_version_compatible("9.0", ["9.0"])
    assert is_ida_version_compatible("9.0", ["9.0", "9.1"])
    assert is_ida_version_compatible("9.1", ["9.0", "9.1", "9.2"])
    assert is_ida_version_compatible("9.0sp1", ["9.0sp1"])
    assert is_ida_version_compatible("9.0sp1", ["9.0", "9.0sp1", "9.1"])

    # Test version not in list
    assert not is_ida_version_compatible("9.2", ["9.0", "9.1"])
    assert not is_ida_version_compatible("8.5", ["9.0", "9.1"])
    assert not is_ida_version_compatible("9.0sp1", ["9.0", "9.1"])  # sp1 not in list
