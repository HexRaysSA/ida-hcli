"""
Integration tests for HCLI plugin functionality.
These tests verify plugin-related CLI behavior.
"""

import pytest


@pytest.mark.integration
class TestPluginCommands:
    def test_plugin_list(self, cli_tester):
        success, _output = cli_tester.run_command("uv run hcli plugin list")
        assert success is not None, "plugin list command should run"

    def test_plugin_status(self, cli_tester):
        success, _output = cli_tester.run_command("uv run hcli plugin status")
        assert success is not None, "plugin status command should run (backwards compatible)"

    def test_plugin_search_empty(self, cli_tester):
        success, _output = cli_tester.run_command("uv run hcli plugin search")
        assert success is not None, "`plugin search` command should run"

    def test_plugin_search_term(self, cli_tester):
        success, _output = cli_tester.run_command("uv run hcli plugin search a")
        assert success is not None, "`plugin search a` command should run"
