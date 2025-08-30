import contextlib
import os
import tempfile
from pathlib import Path

import pytest
from fixtures import PLUGIN_DATA
from pytest_mock import MockerFixture

from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_python_dependencies_from_plugin_archive,
    is_plugin_archive,
    is_source_plugin_archive,
    parse_pep723_metadata,
)
from hcli.lib.ida.plugin.install import (
    get_metadata_from_plugin_directory,
    get_python_dependencies_from_plugin_directory,
)


@contextlib.contextmanager
def temp_env_var(key: str, value: str):
    _orig = os.environ.get(key, "")
    os.environ[key] = value
    try:
        yield
    finally:
        if _orig:
            os.environ[key] = _orig
        else:
            del os.environ[key]


def test_parse_pep723_metadata():
    """Test parsing of PEP 723 inline metadata from Python file content."""

    # Test valid PEP 723 metadata
    python_content = """# /// script
# dependencies = [
#   "packaging>=25.0",
#   "rich>=13.0.0",
# ]
# ///

import ida_idaapi

def PLUGIN_ENTRY():
    return None
"""

    dependencies = parse_pep723_metadata(python_content)
    assert dependencies == ["packaging>=25.0", "rich>=13.0.0"]


def test_parse_pep723_metadata_no_metadata():
    """Test parsing when no PEP 723 metadata is present."""

    python_content = """import ida_idaapi

def PLUGIN_ENTRY():
    return None
"""

    dependencies = parse_pep723_metadata(python_content)
    assert dependencies == []


def test_parse_pep723_metadata_invalid_toml():
    """Test parsing when PEP 723 metadata contains invalid TOML."""

    python_content = """# /// script
# dependencies = [
#   "invalid toml
# ]
# ///

import ida_idaapi

def PLUGIN_ENTRY():
    return None
"""

    with pytest.raises(ValueError, match="Failed to parse PEP 723 TOML metadata"):
        parse_pep723_metadata(python_content)


def test_source_plugin_archive_v4_inline_dependencies():
    """Test that plugin v4 with inline dependencies is recognized as source plugin."""
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v4.0.0.zip"
    buf = plugin_path.read_bytes()

    assert is_plugin_archive(buf, "plugin1")
    assert is_source_plugin_archive(buf, "plugin1")


def test_get_python_dependencies_from_plugin_archive_inline():
    """Test getting inline dependencies from plugin archive."""
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v4.0.0.zip"
    buf = plugin_path.read_bytes()

    metadata = get_metadata_from_plugin_archive(buf, "plugin1")
    assert metadata.python_dependencies == "inline"

    dependencies = get_python_dependencies_from_plugin_archive(buf, metadata)
    assert dependencies == ["packaging>=25.0", "rich>=13.0.0"]


def test_get_python_dependencies_from_plugin_archive_list():
    """Test getting dependencies from traditional list format."""
    plugin_path = PLUGIN_DATA / "plugin1" / "plugin1-v3.0.0.zip"
    buf = plugin_path.read_bytes()

    metadata = get_metadata_from_plugin_archive(buf, "plugin1")
    assert isinstance(metadata.python_dependencies, list)

    dependencies = get_python_dependencies_from_plugin_archive(buf, metadata)
    assert dependencies == ["packaging==25.0"]


def test_get_python_dependencies_from_plugin_directory_inline():
    """Test getting inline dependencies from plugin directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "plugin1"
        plugin_dir.mkdir()

        # Create ida-plugin.json with inline dependencies
        metadata_content = """{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "plugin1", 
    "version": "v4.0.0",
    "entryPoint": "plugin1.py",
    "description": "test plugin",
    "pythonDependencies": "inline"
  }
}"""
        (plugin_dir / "ida-plugin.json").write_text(metadata_content)

        # Create Python file with PEP 723 metadata
        python_content = """# /// script
# dependencies = [
#   "requests>=2.28.0",
#   "pydantic>=2.0.0",
# ]
# ///

import ida_idaapi

def PLUGIN_ENTRY():
    return None
"""
        (plugin_dir / "plugin1.py").write_text(python_content)

        # Test functionality
        metadata = get_metadata_from_plugin_directory(plugin_dir)
        assert metadata.python_dependencies == "inline"

        dependencies = get_python_dependencies_from_plugin_directory(plugin_dir, metadata)
        assert dependencies == ["requests>=2.28.0", "pydantic>=2.0.0"]


def test_get_python_dependencies_from_plugin_directory_list():
    """Test getting dependencies from traditional list format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = Path(tmpdir) / "plugin1"
        plugin_dir.mkdir()

        # Create ida-plugin.json with traditional list dependencies
        metadata_content = """{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "plugin1", 
    "version": "v3.0.0",
    "entryPoint": "plugin1.py",
    "description": "test plugin",
    "pythonDependencies": ["requests>=2.28.0", "pydantic>=2.0.0"]
  }
}"""
        (plugin_dir / "ida-plugin.json").write_text(metadata_content)

        # Create Python file (content doesn't matter for this test)
        python_content = """import ida_idaapi

def PLUGIN_ENTRY():
    return None
"""
        (plugin_dir / "plugin1.py").write_text(python_content)

        # Test functionality
        metadata = get_metadata_from_plugin_directory(plugin_dir)
        assert isinstance(metadata.python_dependencies, list)

        dependencies = get_python_dependencies_from_plugin_directory(plugin_dir, metadata)
        assert dependencies == ["requests>=2.28.0", "pydantic>=2.0.0"]
