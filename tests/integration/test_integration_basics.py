"""
Integration tests for basic HCLI functionality.
These tests verify core CLI behavior without requiring authentication.
"""

import subprocess

import pytest

# `hcli --help` is not just click rendering: the top-level help text is built from
# live local state (stored credentials, registered IDA installs, extensions), and
# every command module is imported to register it. A crash in any of that shows up
# here.
TOP_LEVEL_COMMANDS = ("auth", "download", "extension", "ida", "license", "plugin", "share", "update", "whoami")


@pytest.mark.integration
def test_help_lists_top_level_commands():
    result = subprocess.run(["uv", "run", "hcli", "--help"], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, f"Help command failed: {result.stderr}"
    missing = [command for command in TOP_LEVEL_COMMANDS if command not in result.stdout]
    assert not missing, f"commands missing from help output: {missing}"
