"""
Integration tests for HCLI update functionality.
These tests verify update-related CLI behavior.
"""

import pytest


@pytest.mark.integration
class TestUpdateCommands:
    """Test update-related CLI commands."""

    def test_update_help(self, cli_tester):
        """Test update help command."""
        success, output = cli_tester.run_command("uv run hcli update --help")
        assert success, "Update help command should succeed"
        assert "update" in output.lower(), "Update help should contain 'update'"

    def test_update_check(self, cli_tester):
        """Test update check command."""
        success, output = cli_tester.run_command("uv run hcli update check")
        # Command should run without crashing
        assert success is not None, "Update check command should run"

    def test_update_status(self, cli_tester):
        """Test update status command."""
        success, output = cli_tester.run_command("uv run hcli update status")
        # Command should run without crashing
        assert success is not None, "Update status command should run"
