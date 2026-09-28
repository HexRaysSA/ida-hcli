"""`hcli extension` is the plugin manager under a second name."""

from __future__ import annotations

import shutil
from pathlib import Path

from click.testing import CliRunner
from fixtures import *

from hcli.lib.ida.plugin.install import get_installed_plugin_records
from hcli.main import cli

TESTS_DIR = Path(__file__).parent.parent
PLUGIN1_V1 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v1.0.0.zip"


def test_extension_lists_plugin_subcommands():
    result = CliRunner().invoke(cli, ["extension", "--help"])
    assert result.exit_code == 0, result.output
    for subcommand in ("install", "uninstall", "upgrade", "search", "status", "repo"):
        assert subcommand in result.output


def test_extension_install_uses_plugin_manager(virtual_ida_environment, block_network, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(PLUGIN1_V1, repo / PLUGIN1_V1.name)

    result = CliRunner(mix_stderr=False).invoke(cli, ["extension", "--repo", str(repo), "install", "plugin1"])
    assert result.exit_code == 0, result.output
    assert ("plugin1", "1.0.0") in [(r.name, r.version) for r in get_installed_plugin_records()]

    result = CliRunner(mix_stderr=False).invoke(cli, ["plugin", "status"])
    assert result.exit_code == 0, result.output
    assert "plugin1" in result.output


def test_hcli_extensions_keeps_extension_management():
    result = CliRunner().invoke(cli, ["hcli-extensions", "--help"])
    assert result.exit_code == 0, result.output
    assert "create" in result.output
    assert "list" in result.output
