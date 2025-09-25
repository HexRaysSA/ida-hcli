import tempfile
import textwrap
from pathlib import Path

from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_python_dependencies_from_plugin_archive,
    get_python_dependencies_from_plugin_directory,
    is_plugin_archive,
    is_source_plugin_archive,
    parse_pep723_metadata,
)
from hcli.lib.ida.plugin.install import (
    get_metadata_from_plugin_directory,
)


def test_parse_pep723_metadata():
    """Test parsing of PEP 723 inline metadata from Python file content."""

    # Test valid PEP 723 metadata
    python_content = textwrap.dedent("""
        # /// script
        # dependencies = [
        #   "packaging>=25.0",
        #   "rich>=13.0.0",
        # ]
        # ///

        import ida_idaapi

        def PLUGIN_ENTRY():
            return None
    """).strip()

    dependencies = parse_pep723_metadata(python_content)
    assert dependencies == ["packaging>=25.0", "rich>=13.0.0"]


def test_source_plugin_archive_v4_inline_dependencies():
    """Test that plugin v4 with inline dependencies is recognized as source plugin."""
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v4.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "plugin1")
    assert is_source_plugin_archive(buf, "plugin1")


def test_get_python_dependencies_from_plugin_archive_inline():
    """Test getting inline dependencies from plugin archive."""
    plugin_path = PLUGINS_DIR / "plugin1" / "plugin1-v4.0.0.zip"
    buf = plugin_path.read_bytes()

    metadata = get_metadata_from_plugin_archive(buf, "plugin1")
    assert metadata.plugin.python_dependencies == "inline"

    dependencies = get_python_dependencies_from_plugin_archive(buf, metadata)
    assert dependencies == ["packaging>=25.0", "rich>=13.0.0"]


def test_get_python_dependencies_from_plugin_directory_inline():
    """Test getting inline dependencies from plugin directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "plugin1"
        plugin_dir.mkdir()

        metadata_content = textwrap.dedent("""{
            "IDAMetadataDescriptorVersion": 1,
            "plugin": {
              "name": "plugin1",
              "version": "4.0.0",
              "entryPoint": "plugin1.py",
              "description": "test plugin",
              "pythonDependencies": "inline",
              "authors": [{"name": "Willi Ballenthin", "email": "wballenthin@hex-rays.com"}],
              "urls": {"repository": "https://github.com/HexRaysSA/ida-hcli"}
            }
        }""")
        (plugin_dir / "ida-plugin.json").write_text(metadata_content)

        python_content = textwrap.dedent("""
            # /// script
            # dependencies = [
            #   "requests>=2.28.0",
            #   "pydantic>=2.0.0",
            # ]
            # ///

            import ida_idaapi

            def PLUGIN_ENTRY():
                return None
        """).strip()
        (plugin_dir / "plugin1.py").write_text(python_content)

        metadata = get_metadata_from_plugin_directory(plugin_dir)
        assert metadata.plugin.python_dependencies == "inline"

        dependencies = get_python_dependencies_from_plugin_directory(plugin_dir, metadata)
        assert dependencies == ["requests>=2.28.0", "pydantic>=2.0.0"]
