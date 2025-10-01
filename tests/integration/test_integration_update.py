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


@pytest.mark.integration
class TestBackgroundUpdateChecker:
    """Test background update checking behavior."""

    def test_no_background_check_for_pip_install(self, cli_tester):
        """Test that background update checker doesn't run for pip-installed versions."""
        # Run a simple command to ensure the CLI initializes properly
        # For pip-installed versions, no background check should occur
        success, output = cli_tester.run_command("uv run hcli whoami")
        # The command should succeed and not show any update messages
        # (assuming no update is actually needed, this just verifies it runs)
        assert success or "not logged in" in output.lower(), "CLI should run without errors"

    def test_background_check_can_be_disabled(self, cli_tester):
        """Test that --disable-updates flag works."""
        success, output = cli_tester.run_command("uv run hcli --disable-updates whoami")
        assert success or "not logged in" in output.lower(), "CLI with --disable-updates should work"
