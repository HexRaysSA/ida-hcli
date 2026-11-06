from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import temp_env_var
from test_mcp_install import make_fake_agent, read_commands

from hcli.commands.mcp._agents import get_installed_scopes, uninstall_agent
from hcli.commands.mcp.uninstall import uninstall
from hcli.lib.ida.plugin.install import get_plugin_directory, is_plugin_installed

CLAUDE_BOTH_SCOPES = {
    ("plugin", "list", "--json"): json.dumps(
        [
            {"id": "ida-mcp@HexRaysSA", "scope": "project"},
            {"id": "ida-mcp@HexRaysSA", "scope": "user"},
        ]
    )
}

PI_LISTING = (
    "User packages:\n"
    "  npm:@example/other-extension\n"
    "    /home/user/.pi/agent/npm/node_modules/@example/other-extension\n"
    "\n"
    "Project packages:\n"
    "  git:github.com/HexRaysSA/ida-mcp@v1.2.3\n"
    "    /home/user/project/.pi/git/github.com/HexRaysSA/ida-mcp\n"
)


def test_claude_removes_each_scope(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(tmp_path, "claude", CLAUDE_BOTH_SCOPES)

    assert get_installed_scopes(agent) == ["local", "global"]
    uninstall_agent(agent, "local")

    assert read_commands(log_path)[-1] == ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "project"]


def test_codex_ignores_plugin_that_is_only_available(tmp_path: Path) -> None:
    agent, _ = make_fake_agent(
        tmp_path,
        "codex",
        {
            ("plugin", "list", "--json"): json.dumps(
                {"installed": [], "available": [{"pluginId": "ida-mcp@HexRaysSA", "installed": False}]}
            )
        },
    )

    assert get_installed_scopes(agent) == []


def test_codex_removes_installed_plugin(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "codex",
        {("plugin", "list", "--json"): json.dumps({"installed": [{"pluginId": "ida-mcp@HexRaysSA"}], "available": []})},
    )

    assert get_installed_scopes(agent) == ["global"]
    uninstall_agent(agent, "global")

    assert read_commands(log_path)[-1] == ["plugin", "remove", "ida-mcp@HexRaysSA"]


def test_copilot_removes_listed_plugin(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "copilot",
        {("plugin", "list"): "Installed plugins:\n  • ida-mcp (v0.8.1)\n"},
    )

    assert get_installed_scopes(agent) == ["global"]
    uninstall_agent(agent, "global")

    assert read_commands(log_path)[-1] == ["plugin", "uninstall", "ida-mcp"]


def test_pi_removes_the_listed_project_source(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(tmp_path, "pi", {("list", "--approve"): PI_LISTING})

    assert get_installed_scopes(agent) == ["local"]
    uninstall_agent(agent, "local")

    assert read_commands(log_path)[-1] == ["remove", "git:github.com/HexRaysSA/ida-mcp@v1.2.3", "--local", "--approve"]


def test_pi_removes_a_user_source_without_trusting_the_project(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "pi",
        {("list", "--approve"): "User packages:\n  git:github.com/HexRaysSA/ida-mcp@latest (filtered)\n"},
    )

    assert get_installed_scopes(agent) == ["global"]
    uninstall_agent(agent, "global")

    assert read_commands(log_path)[-1] == ["remove", "git:github.com/HexRaysSA/ida-mcp@latest", "--no-approve"]


def test_omp_removes_plugin(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "omp",
        {("plugin", "list", "--json"): json.dumps({"npm": [{"name": "ida-mcp"}], "marketplace": []})},
    )

    assert get_installed_scopes(agent) == ["global"]
    uninstall_agent(agent, "global")

    assert read_commands(log_path)[-1] == ["plugin", "uninstall", "ida-mcp"]


def install_fake_ida_mcp_plugin() -> Path:
    plugin_dir = get_plugin_directory("ida-mcp")
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "ida_mcp.py").write_text("", encoding="utf-8")
    (plugin_dir / "ida-plugin.json").write_text(
        json.dumps(
            {
                "IDAMetadataDescriptorVersion": 1,
                "plugin": {
                    "name": "ida-mcp",
                    "version": "1.0.0",
                    "entryPoint": "ida_mcp.py",
                    "urls": {"repository": "https://github.com/HexRaysSA/ida-mcp"},
                    "authors": [{"name": "Hex-Rays", "email": "support@hex-rays.com"}],
                },
            }
        ),
        encoding="utf-8",
    )
    assert is_plugin_installed("ida-mcp")
    return plugin_dir


@pytest.fixture
def agents_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """An empty directory that is the only entry on PATH."""
    path = tmp_path / "bin"
    path.mkdir()
    with temp_env_var("PATH", str(path)):
        yield path


def test_uninstall_removes_agents_and_ida_plugin(virtual_ida_environment, agents_dir: Path) -> None:
    _, claude_log = make_fake_agent(agents_dir, "claude", CLAUDE_BOTH_SCOPES)
    plugin_dir = install_fake_ida_mcp_plugin()

    result = CliRunner().invoke(uninstall, ["--yes"])

    assert result.exit_code == 0, result.output
    assert [args for args in read_commands(claude_log) if args[1] == "uninstall"] == [
        ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "project"],
        ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "user"],
    ]
    assert not plugin_dir.exists()


def test_uninstall_without_tty_requires_yes(virtual_ida_environment, agents_dir: Path) -> None:
    _, claude_log = make_fake_agent(agents_dir, "claude", CLAUDE_BOTH_SCOPES)
    install_fake_ida_mcp_plugin()

    result = CliRunner().invoke(uninstall, [])

    assert result.exit_code != 0
    assert all(args[1] != "uninstall" for args in read_commands(claude_log))
    assert is_plugin_installed("ida-mcp")


def test_uninstall_when_nothing_is_installed(virtual_ida_environment, agents_dir: Path) -> None:
    make_fake_agent(agents_dir, "claude", {("plugin", "list", "--json"): "[]"})

    result = CliRunner().invoke(uninstall, [])

    assert result.exit_code == 0, result.output
    assert "IDA MCP is not installed" in result.output


def test_uninstall_continues_after_an_agent_fails(virtual_ida_environment, agents_dir: Path) -> None:
    make_fake_agent(
        agents_dir,
        "claude",
        CLAUDE_BOTH_SCOPES,
        exit_codes={("plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "project"): 1},
    )
    _, omp_log = make_fake_agent(
        agents_dir,
        "omp",
        {("plugin", "list", "--json"): json.dumps({"npm": [{"name": "ida-mcp"}], "marketplace": []})},
    )
    plugin_dir = install_fake_ida_mcp_plugin()

    result = CliRunner().invoke(uninstall, ["--yes"])

    assert result.exit_code == 1
    assert read_commands(omp_log)[-1] == ["plugin", "uninstall", "ida-mcp"]
    assert not plugin_dir.exists()


def test_uninstall_lists_what_it_removes(virtual_ida_environment, agents_dir: Path) -> None:
    make_fake_agent(agents_dir, "pi", {("list", "--approve"): PI_LISTING})

    result = CliRunner().invoke(uninstall, ["--yes"])

    assert result.exit_code == 0, result.output
    assert "Pi in this repository" in result.output
    assert "IDA plugin" not in result.output
