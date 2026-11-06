from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_mcp_install import make_fake_agent, read_commands

from hcli.commands.mcp.install import Agent, _is_codex_installed, _is_copilot_installed, _is_pi_installed
from hcli.commands.mcp.uninstall import uninstall
from hcli.lib.ida.plugin.exceptions import PluginInUseError, PluginNotInstalledError

# `hcli.commands.mcp.uninstall` the attribute is the click command; get the module.
uninstall_module = sys.modules["hcli.commands.mcp.uninstall"]

CLAUDE_BOTH_SCOPES = {
    ("plugin", "list", "--json"): json.dumps(
        [
            {"id": "ida-mcp@HexRaysSA", "scope": "user"},
            {"id": "ida-mcp@HexRaysSA", "scope": "project"},
        ]
    )
}


class FakeIdaPlugin:
    def __init__(self, *, installed: bool = True, error: Exception | None = None) -> None:
        self.installed = installed
        self.error = error
        self.uninstalled: list[str] = []

    def find(self, name: str) -> object:
        if not self.installed:
            raise PluginNotInstalledError(name)
        return object()

    def uninstall(self, name: str) -> None:
        if self.error is not None:
            raise self.error
        self.uninstalled.append(name)


@pytest.fixture
def ida_plugin(monkeypatch: pytest.MonkeyPatch) -> FakeIdaPlugin:
    fake = FakeIdaPlugin()
    monkeypatch.setattr(uninstall_module, "find_installed_plugin", fake.find)
    monkeypatch.setattr(uninstall_module, "uninstall_plugin_impl", fake.uninstall)
    monkeypatch.setattr(uninstall_module, "sweep_trash", lambda: None)
    return fake


def fail_on(tmp_path: Path, agent: Agent, args: tuple[str, ...]) -> None:
    """Make the fake agent exit with status 1 for `args`, after logging them."""
    for script in {Path(agent.executable), tmp_path / f"{agent.command}_fake.py"}:
        if script.suffix == ".cmd":
            continue
        script.write_text(
            script.read_text(encoding="utf-8").replace(
                "sys.stdout.write(", f"sys.exit(1) if args == {args!r} else sys.stdout.write("
            ),
            encoding="utf-8",
        )


def use_agents(monkeypatch: pytest.MonkeyPatch, *agents: Agent) -> None:
    monkeypatch.setattr(uninstall_module, "_find_agents", lambda: list(agents))


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(uninstall, list(args))
    # Rich wraps output to the terminal width and boxes errors, so compare
    # against the words alone.
    return result.exit_code, " ".join(re.sub("[\u2500-\u257f]", " ", result.output).split())


def test_removes_every_installed_scope_then_the_ida_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin
) -> None:
    claude, log_path = make_fake_agent(tmp_path, "claude", CLAUDE_BOTH_SCOPES)
    use_agents(monkeypatch, claude)

    exit_code, output = run("--yes")

    assert exit_code == 0, output
    assert read_commands(log_path) == [
        ["plugin", "list", "--json"],
        ["plugin", "list", "--json"],
        ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "user"],
        ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "project"],
    ]
    assert ida_plugin.uninstalled == ["ida-mcp"]


def test_skips_agents_without_ida_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin
) -> None:
    codex, codex_log = make_fake_agent(
        tmp_path,
        "codex",
        {("plugin", "list", "--json"): json.dumps({"installed": [{"pluginId": "ida-mcp@HexRaysSA"}]})},
    )
    omp, omp_log = make_fake_agent(tmp_path, "omp", {})
    use_agents(monkeypatch, codex, omp)

    exit_code, output = run("--yes", "--keep-plugin")

    assert exit_code == 0, output
    assert read_commands(codex_log) == [["plugin", "list", "--json"], ["plugin", "remove", "ida-mcp@HexRaysSA"]]
    assert ["plugin", "uninstall", "ida-mcp", "--scope", "user"] not in read_commands(omp_log)
    assert ida_plugin.uninstalled == []


@pytest.mark.parametrize(
    ("command", "responses", "expected"),
    [
        (
            "copilot",
            {("plugin", "list"): "Installed plugins:\n  • ida-mcp@HexRaysSA (v20261028.0.3)\n"},
            [
                ["plugin", "uninstall", "ida-mcp"],
                ["plugin", "marketplace", "remove", "HexRaysSA"],
            ],
        ),
        (
            "codex",
            {("plugin", "list", "--json"): json.dumps({"installed": [{"pluginId": "ida-mcp@HexRaysSA"}]})},
            [
                ["plugin", "remove", "ida-mcp@HexRaysSA"],
                ["plugin", "marketplace", "remove", "HexRaysSA"],
            ],
        ),
        (
            "claude",
            {("plugin", "list", "--json"): json.dumps([{"id": "ida-mcp@HexRaysSA", "scope": "user"}])},
            [
                ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "user"],
                ["plugin", "marketplace", "remove", "HexRaysSA", "--scope", "user"],
            ],
        ),
    ],
)
def test_remove_marketplace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ida_plugin: FakeIdaPlugin,
    command: str,
    responses: dict[tuple[str, ...], str],
    expected: list[list[str]],
) -> None:
    agent, log_path = make_fake_agent(tmp_path, command, responses)
    use_agents(monkeypatch, agent)

    exit_code, output = run("--yes", "--keep-plugin", "--scope", "global", "--remove-marketplace")

    assert exit_code == 0, output
    assert read_commands(log_path)[-2:] == expected


@pytest.mark.parametrize(
    ("scope", "listing", "expected"),
    [
        ("local", "Project packages:\n  git:github.com/HexRaysSA/ida-mcp@latest\n", ["--local"]),
        ("global", "User packages:\n  git:github.com/HexRaysSA/ida-mcp@latest\n", []),
    ],
)
def test_pi_removes_the_source_from_the_installed_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ida_plugin: FakeIdaPlugin,
    scope: str,
    listing: str,
    expected: list[str],
) -> None:
    pi, log_path = make_fake_agent(tmp_path, "pi", {("list", "--no-approve"): listing})
    use_agents(monkeypatch, pi)

    exit_code, output = run("--yes", "--keep-plugin", "--scope", scope)

    assert exit_code == 0, output
    assert read_commands(log_path)[-1] == ["remove", "git:github.com/HexRaysSA/ida-mcp", *expected]


def test_agent_and_scope_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin) -> None:
    claude, claude_log = make_fake_agent(tmp_path, "claude", CLAUDE_BOTH_SCOPES)
    codex, codex_log = make_fake_agent(tmp_path, "codex", {})
    use_agents(monkeypatch, claude, codex)

    exit_code, output = run("--yes", "--agent", "claude", "--scope", "local")

    assert exit_code == 0, output
    assert read_commands(claude_log) == [
        ["plugin", "list", "--json"],
        ["plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "project"],
    ]
    assert not codex_log.exists()


def test_dry_run_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin) -> None:
    claude, log_path = make_fake_agent(tmp_path, "claude", CLAUDE_BOTH_SCOPES)
    use_agents(monkeypatch, claude)

    exit_code, output = run("--dry-run")

    assert exit_code == 0, output
    assert "claude plugin uninstall ida-mcp@HexRaysSA --scope project" in output
    assert "hcli plugin uninstall ida-mcp" in output
    assert all(command == ["plugin", "list", "--json"] for command in read_commands(log_path))
    assert ida_plugin.uninstalled == []


def test_nothing_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin) -> None:
    ida_plugin.installed = False
    claude, _ = make_fake_agent(tmp_path, "claude", {("plugin", "list", "--json"): "[]"})
    use_agents(monkeypatch, claude)

    exit_code, output = run()

    assert exit_code == 0, output
    assert "Nothing to do" in output


def test_requires_yes_without_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin
) -> None:
    use_agents(monkeypatch)

    exit_code, output = run()

    assert exit_code != 0
    assert "--yes" in output
    assert ida_plugin.uninstalled == []


def test_continues_after_a_failing_agent_and_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin
) -> None:
    claude, _ = make_fake_agent(tmp_path, "claude", CLAUDE_BOTH_SCOPES)
    codex, codex_log = make_fake_agent(
        tmp_path,
        "codex",
        {("plugin", "list", "--json"): json.dumps({"installed": [{"pluginId": "ida-mcp@HexRaysSA"}]})},
    )
    fail_on(tmp_path, claude, ("plugin", "uninstall", "ida-mcp@HexRaysSA", "--scope", "user"))
    use_agents(monkeypatch, claude, codex)

    exit_code, output = run("--yes")

    assert exit_code == 1
    assert "Claude Code (current user): claude exited with status 1" in output
    assert ["plugin", "remove", "ida-mcp@HexRaysSA"] in read_commands(codex_log)
    assert ida_plugin.uninstalled == ["ida-mcp"]


def test_ida_plugin_in_use_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ida_plugin: FakeIdaPlugin
) -> None:
    ida_plugin.error = PluginInUseError("ida-mcp", Path("ida-mcp"))
    use_agents(monkeypatch)

    exit_code, output = run("--yes")

    assert exit_code == 1
    assert "Close IDA" in output


def test_codex_probe_ignores_available_plugins(tmp_path: Path) -> None:
    agent, _ = make_fake_agent(
        tmp_path,
        "codex",
        {("plugin", "list", "--json"): json.dumps({"installed": [], "available": [{"pluginId": "ida-mcp@HexRaysSA"}]})},
    )

    assert not _is_codex_installed(agent)


def test_copilot_probe_accepts_marketplace_qualified_name(tmp_path: Path) -> None:
    agent, _ = make_fake_agent(
        tmp_path, "copilot", {("plugin", "list"): "Installed plugins:\n  • ida-mcp@HexRaysSA (v20261028.0.3)\n"}
    )

    assert _is_copilot_installed(agent)


def test_pi_probe_falls_back_when_list_rejects_no_approve(tmp_path: Path) -> None:
    # Pi releases without `--no-approve` exit with an error for it.
    agent, log_path = make_fake_agent(
        tmp_path, "pi", {("list",): "User packages:\n  git:github.com/HexRaysSA/ida-mcp@latest\n"}
    )
    fail_on(tmp_path, agent, ("list", "--no-approve"))

    assert _is_pi_installed(agent, "global")
    assert read_commands(log_path) == [["list", "--no-approve"], ["list"]]
