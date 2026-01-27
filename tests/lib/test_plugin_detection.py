import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fixtures import temp_env_var

logger = logging.getLogger(__name__)


def create_mock_plugin(plugins_dir: Path, plugin_name: str, version: str = "1.0.0"):
    """Create a minimal plugin directory structure for testing."""
    plugin_dir = plugins_dir / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "IDAMetadataDescriptorVersion": 1,
        "plugin": {
            "name": plugin_name,
            "version": version,
            "description": f"Test plugin {plugin_name}",
            "entryPoint": "main.py",
            "urls": {"repository": "https://github.com/test/test-plugin"},
            "authors": [{"name": "Test Author", "email": "test@example.com"}],
        },
    }
    (plugin_dir / "ida-plugin.json").write_text(json.dumps(metadata))
    (plugin_dir / "main.py").write_text(f'"""Plugin entrypoint for {plugin_name}"""\n')

    return plugin_dir


class TestGetCurrentPlugin:
    """Test plugin detection via file system path.

    Plugin detection works by walking the call stack and checking if each frame's
    code file is within $IDAUSR/plugins/. When found, it reads the plugin's
    ida-plugin.json to get the canonical plugin name.
    """

    def test_plugin_detection_with_dashes(self):
        """Test that plugins with dashes in names are detected correctly."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "ida-chat")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = str(plugin_dir / "main.py")
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    result = get_current_plugin()
                    assert result == "ida-chat"

    def test_plugin_detection_with_underscores(self):
        """Test that plugins with underscores in names are detected correctly."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "my_plugin")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = str(plugin_dir / "main.py")
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    result = get_current_plugin()
                    assert result == "my_plugin"

    def test_plugin_detection_with_multiple_dashes(self):
        """Test plugins with multiple special characters in names."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "my-cool-plugin")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = str(plugin_dir / "main.py")
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    result = get_current_plugin()
                    assert result == "my-cool-plugin"

    def test_plugin_detection_from_subdirectory(self):
        """Test detection works for files in plugin subdirectories."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "ida-chat")
            subdir = plugin_dir / "lib"
            subdir.mkdir()
            (subdir / "helper.py").write_text("# helper module\n")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = str(subdir / "helper.py")
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    result = get_current_plugin()
                    assert result == "ida-chat"

    def test_plugin_detection_walks_stack(self):
        """Test that detection walks up the call stack to find plugin."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "ida-chat")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                plugin_frame = MagicMock()
                plugin_frame.f_code.co_filename = str(plugin_dir / "main.py")
                plugin_frame.f_back = None

                non_plugin_frame = MagicMock()
                non_plugin_frame.f_code.co_filename = "/some/library/code.py"
                non_plugin_frame.f_back = plugin_frame

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=non_plugin_frame)

                    result = get_current_plugin()
                    assert result == "ida-chat"


class TestGetCurrentPluginErrors:
    """Test error handling in get_current_plugin."""

    def test_no_frame_raises_runtime_error(self):
        """Test that missing frame raises RuntimeError."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with patch("inspect.currentframe", return_value=None):
            with pytest.raises(RuntimeError, match="failed to get current frame"):
                get_current_plugin()

    def test_no_plugin_in_stack_raises_runtime_error(self):
        """Test that no plugin in stack raises RuntimeError."""
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = "/some/random/path.py"
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    with pytest.raises(RuntimeError, match="must be called from within a plugin module"):
                        get_current_plugin()

    def test_code_outside_plugins_dir_raises_error(self):
        """Test that code not in plugins directory raises RuntimeError.

        This verifies that we only use path-based detection and don't fall back
        to module name detection (which would be lossy for dashed names).
        """
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = "/usr/lib/python/site-packages/some_lib.py"
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)

                    with pytest.raises(RuntimeError, match="must be called from within a plugin module"):
                        get_current_plugin()


class TestPluginSettingsIntegration:
    """Test that get_current_plugin integrates correctly with settings functions."""

    def test_detected_name_works_with_get_plugin_directory(self):
        """Detected plugin name can be used with get_plugin_directory."""
        from hcli.lib.ida.plugin.install import get_plugin_directory
        from hcli.lib.ida.plugin.settings import get_current_plugin

        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / "plugins"
            plugins_dir.mkdir()

            plugin_dir = create_mock_plugin(plugins_dir, "ida-chat")

            with temp_env_var("HCLI_IDAUSR", tmpdir):
                mock_frame = MagicMock()
                mock_frame.f_code.co_filename = str(plugin_dir / "main.py")
                mock_frame.f_back = None

                with patch("inspect.currentframe") as mock_currentframe:
                    mock_currentframe.return_value = MagicMock(f_back=mock_frame)
                    detected_name = get_current_plugin()

                assert detected_name == "ida-chat"

                plugin_path = get_plugin_directory(detected_name)
                assert plugin_path.exists()
                assert (plugin_path / "ida-plugin.json").exists()
