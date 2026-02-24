"""
Integration tests for basic HCLI functionality.
These tests verify core CLI behavior without requiring authentication.
"""

import subprocess

import pytest

from hcli import __version__


@pytest.mark.integration
class TestCLIBasics:
    """Test basic CLI functionality."""

    def test_help_command(self):
        """Test the main help command."""
        result = subprocess.run(
            ["uv", "run", "hcli", "--help"], capture_output=True, text=True, timeout=10, check=False
        )
        assert result.returncode == 0, f"Help command failed: {result.stderr}"
        assert "HCLI" in result.stdout, "Help output should contain HCLI"

    def test_version_info(self):
        """Test version information."""
        result = subprocess.run(
            ["uv", "run", "hcli", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        assert result.returncode == 0, f"Version command failed: {result.stderr}"
        assert result.stdout.strip() == f"hcli, version {__version__}", "Version command should produce output"

    @pytest.mark.parametrize("subcommand", ["auth", "download", "ida", "license", "share", "update"])
    def test_subcommand_help(self, subcommand):
        """Test help for various subcommands."""
        result = subprocess.run(
            ["uv", "run", "hcli", subcommand, "--help"], capture_output=True, text=True, timeout=10, check=False
        )
        assert result.returncode == 0, f"{subcommand} help failed: {result.stderr}"
        assert result.stdout.strip(), f"{subcommand} help should produce output"
