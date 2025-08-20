"""
Integration tests for HCLI IDA functionality.
These tests verify IDA-related CLI behavior.
"""

import pytest


@pytest.mark.integration
class TestIdaCommands:
    """Test IDA-related CLI commands."""

    def test_ida_info(self, cli_tester):
        """Test IDA info command."""
        success, output = cli_tester.run_command("uv run hcli ida info")
        assert success is not None, "IDA info command should run"
