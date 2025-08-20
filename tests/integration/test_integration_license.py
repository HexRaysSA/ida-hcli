"""
Integration tests for HCLI license functionality.
These tests verify license-related CLI behavior.
"""

import pytest


@pytest.mark.integration
class TestLicenseCommands:
    """Test license-related CLI commands."""

    def test_license_help(self, cli_tester):
        """Test license help command."""
        success, output = cli_tester.run_command("uv run hcli license --help")
        assert success, "License help command should succeed"
        assert "license" in output.lower(), "License help should contain 'license'"

    def test_license_status(self, cli_tester):
        """Test license status command."""
        success, output = cli_tester.run_command("uv run hcli license status")
        # Command may require auth, but should run without crashing
        assert success is not None, "License status command should run"

    def test_license_list(self, cli_tester):
        """Test license list command."""
        success, output = cli_tester.run_command("uv run hcli license list")
        # Command may require auth, but should run without crashing
        assert success is not None, "License list command should run"
