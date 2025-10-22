"""Tests for IDA install command path resolution."""

import os
import tempfile
from pathlib import Path


def test_relative_path_resolution():
    """Test that Path.resolve() correctly handles relative paths."""
    # Create a temporary directory and file to simulate an installer
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake installer file
        installer_name = "ida-pro_92_x64linux.run"
        installer_path = Path(tmpdir) / installer_name
        installer_path.touch()

        # Change to the temp directory so we can use a relative path
        original_cwd = os.getcwd()
        try:
            os.chdir(tmpdir)

            # Test with relative path (./installer_name)
            relative_path = Path(f"./{installer_name}")
            resolved_path = relative_path.resolve()

            # Verify that resolve() converts to absolute path
            assert not relative_path.is_absolute(), "Original path should be relative"
            assert resolved_path.is_absolute(), f"Resolved path should be absolute: {resolved_path}"
            assert resolved_path.name == installer_name
            assert resolved_path.exists()

            # Verify that the resolved path points to the same file
            assert resolved_path == installer_path.absolute()

        finally:
            os.chdir(original_cwd)


def test_absolute_path_resolution():
    """Test that Path.resolve() handles absolute paths correctly."""
    # Create a temporary directory and file to simulate an installer
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake installer file
        installer_name = "ida-pro_92_x64linux.run"
        installer_path = Path(tmpdir) / installer_name
        installer_path.touch()

        # Test with absolute path
        absolute_path = installer_path.absolute()
        resolved_path = absolute_path.resolve()

        # Verify that resolve() preserves absolute paths
        assert absolute_path.is_absolute(), "Original path should be absolute"
        assert resolved_path.is_absolute(), "Resolved path should be absolute"
        assert resolved_path == absolute_path
        assert resolved_path.exists()


def test_relative_path_with_parent_directory():
    """Test that Path.resolve() handles relative paths with parent directory references."""
    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = Path(tmpdir) / "subdir"
        subdir.mkdir()

        # Create a fake installer file in the parent directory
        installer_name = "ida-pro_92_x64linux.run"
        installer_path = Path(tmpdir) / installer_name
        installer_path.touch()

        # Change to subdirectory
        original_cwd = os.getcwd()
        try:
            os.chdir(subdir)

            # Test with relative path using parent directory reference (../installer_name)
            relative_path = Path(f"../{installer_name}")
            resolved_path = relative_path.resolve()

            # Verify that resolve() converts to absolute path
            assert not relative_path.is_absolute(), "Original path should be relative"
            assert resolved_path.is_absolute(), f"Resolved path should be absolute: {resolved_path}"
            assert resolved_path.name == installer_name
            assert resolved_path.exists()
            assert resolved_path == installer_path.absolute()

        finally:
            os.chdir(original_cwd)
