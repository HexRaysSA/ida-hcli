from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from hcli.commands.mcp.install import Agent, Scope, _find_agents, _install_agent, _install_ida_plugin


def make_fake_agent(
    tmp_path: Path,
    command: str,
    responses: dict[tuple[str, ...], str],
    *,
    command_shim: bool = False,
) -> tuple[Agent, Path]:
    log_path = tmp_path / f"{command}.log"
    script_path = tmp_path / f"{command}_fake.py"
    script_path.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "from pathlib import Path",
                f"responses = {responses!r}",
                "args = tuple(sys.argv[1:])",
                f"with Path({str(log_path)!r}).open('a', encoding='utf-8') as f:",
                "    f.write(json.dumps(args) + '\\n')",
                "sys.stdout.write(responses.get(args, ''))",
            ]
        ),
        encoding="utf-8",
    )

    if os.name == "nt":
        executable = tmp_path / f"{command}.cmd"
        executable.write_text(f'@echo off\n"{sys.executable}" "{script_path}" %*\n', encoding="utf-8")
    else:
        suffix = ".cmd" if command_shim else ""
        executable = tmp_path / f"{command}{suffix}"
        executable.write_text(f"#!{sys.executable}\n{script_path.read_text(encoding='utf-8')}", encoding="utf-8")
        executable.chmod(0o755)

    names = {
        "claude": ("Claude Code", True),
        "codex": ("Codex CLI", False),
        "copilot": ("GitHub Copilot CLI", False),
        "pi": ("Pi", True),
        "omp": ("Oh My Pi", True),
    }
    name, supports_local = names[command]
    return Agent(command, name, str(executable), supports_local), log_path


def read_commands(log_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_find_agents_detects_windows_command_shims(tmp_path: Path) -> None:
    claude, _ = make_fake_agent(tmp_path, "claude", {}, command_shim=True)
    pi, _ = make_fake_agent(tmp_path, "pi", {})
    previous_path = os.environ.get("PATH")
    os.environ["PATH"] = str(tmp_path)
    try:
        found = _find_agents()
    finally:
        if previous_path is None:
            del os.environ["PATH"]
        else:
            os.environ["PATH"] = previous_path

    assert [item.command for item in found] == ["claude", "pi"]
    assert Path(found[0].executable).samefile(claude.executable)
    assert Path(found[1].executable).samefile(pi.executable)


def test_claude_updates_existing_scoped_install(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "claude",
        {("plugin", "list", "--json"): json.dumps([{"id": "ida-mcp@HexRaysSA", "scope": "project"}])},
        command_shim=True,
    )

    _install_agent(agent, "local")

    assert read_commands(log_path) == [
        ["plugin", "list", "--json"],
        ["plugin", "update", "ida-mcp@HexRaysSA", "--scope", "project", "--yes"],
    ]


def test_codex_adds_marketplace_then_installs(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "codex",
        {
            ("plugin", "list", "--json"): "{}",
            ("plugin", "marketplace", "list", "--json"): "{}",
        },
    )

    _install_agent(agent, "global")

    assert read_commands(log_path) == [
        ["plugin", "list", "--json"],
        ["plugin", "marketplace", "list", "--json"],
        ["plugin", "marketplace", "add", "HexRaysSA/codex-marketplace"],
        ["plugin", "add", "ida-mcp@HexRaysSA"],
    ]


def test_copilot_adds_marketplace_then_installs(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "copilot",
        {
            ("plugin", "list"): "No plugins installed.\n",
            ("plugin", "marketplace", "list"): (
                "Included with GitHub Copilot:\n  ◆ copilot-plugins (GitHub: github/copilot-plugins)\n"
            ),
        },
    )

    _install_agent(agent, "global")

    assert read_commands(log_path) == [
        ["plugin", "list"],
        ["plugin", "marketplace", "list"],
        ["plugin", "marketplace", "add", "HexRaysSA/copilot-marketplace"],
        ["plugin", "install", "ida-mcp@HexRaysSA"],
    ]


def test_copilot_updates_existing_marketplace_then_installs(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "copilot",
        {
            ("plugin", "list"): "No plugins installed.\n",
            ("plugin", "marketplace", "list"): (
                "Included with GitHub Copilot:\n  ◆ HexRaysSA (GitHub: HexRaysSA/copilot-marketplace)\n"
            ),
        },
    )

    _install_agent(agent, "global")

    assert read_commands(log_path) == [
        ["plugin", "list"],
        ["plugin", "marketplace", "list"],
        ["plugin", "marketplace", "update", "HexRaysSA"],
        ["plugin", "install", "ida-mcp@HexRaysSA"],
    ]


def test_copilot_updates_existing_plugin_and_marketplace(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "copilot",
        {
            ("plugin", "list"): "Installed plugins:\n  • ida-mcp (v0.8.1)\n",
        },
    )

    _install_agent(agent, "global")

    assert read_commands(log_path) == [
        ["plugin", "list"],
        ["plugin", "marketplace", "update", "HexRaysSA"],
        ["plugin", "update", "ida-mcp"],
    ]


@pytest.mark.parametrize(
    ("scope", "listing", "expected"),
    [
        (
            "local",
            "Project packages:\n  git:github.com/HexRaysSA/ida-mcp@latest\n",
            ["update", "--extension", "git:github.com/HexRaysSA/ida-mcp@latest"],
        ),
        (
            "global",
            "User packages:\n",
            ["install", "git:github.com/HexRaysSA/ida-mcp@latest"],
        ),
        (
            "local",
            "User packages:\n",
            ["install", "git:github.com/HexRaysSA/ida-mcp@latest", "--local"],
        ),
    ],
)
def test_pi_installs_or_updates_selected_scope(
    tmp_path: Path,
    scope: Scope,
    listing: str,
    expected: list[str],
) -> None:
    agent, log_path = make_fake_agent(tmp_path, "pi", {("list", "--no-approve"): listing})

    _install_agent(agent, scope)

    assert read_commands(log_path) == [["list", "--no-approve"], expected]


def test_omp_updates_existing_local_plugin(tmp_path: Path) -> None:
    agent, log_path = make_fake_agent(
        tmp_path,
        "omp",
        {("plugin", "list", "--json", "--scope", "project"): json.dumps({"npm": [{"name": "ida-mcp"}]})},
    )

    _install_agent(agent, "local")

    assert read_commands(log_path) == [
        ["plugin", "list", "--json", "--scope", "project"],
        ["plugin", "upgrade", "ida-mcp", "--scope", "project"],
    ]


def test_ida_plugin_install_upgrades_when_already_installed() -> None:
    """Re-running `hcli mcp install` must upgrade the plugin, not abort on it."""
    calls: list[dict[str, object]] = []

    class FakeContext:
        def invoke(self, command: object, **kwargs: object) -> None:
            calls.append(kwargs)

    _install_ida_plugin(FakeContext())  # type: ignore[arg-type]

    assert len(calls) == 1
    assert calls[0]["upgrade"] is True
