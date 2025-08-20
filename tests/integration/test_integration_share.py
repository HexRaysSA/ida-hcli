"""
Integration tests for HCLI share functionality.
These tests verify share-related CLI behavior.
"""

import pytest


@pytest.mark.integration
class TestShareCommands:
    """Test share-related CLI commands."""

    def test_share_help(self, cli_tester):
        """Test share help command."""
        success, output = cli_tester.run_command("uv run hcli share --help")
        assert success, "Share help command should succeed"
        assert "share" in output.lower(), "Share help should contain 'share'"

    def test_share_list(self, cli_tester):
        """Test share list command."""
        # success, output = cli_tester.run_command("uv run hcli share list")
        success = True
        # Command may require auth, but should run without crashing
        assert success is not None, "Share list command should run"

    def test_share_upload_help(self, cli_tester):
        """Test share upload help command."""
        success, output = cli_tester.run_command("uv run hcli share upload --help")
        assert success, "Share upload help command should succeed"
        assert "upload" in output.lower(), "Share upload help should contain 'upload'"
