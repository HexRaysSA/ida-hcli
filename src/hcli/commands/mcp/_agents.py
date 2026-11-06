"""Detect AI coding agents and drive their CLIs to manage the IDA MCP integration."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Literal

import rich_click as click

from hcli.lib.console import console

PLUGIN_ID = "ida-mcp@HexRaysSA"


Scope = Literal["local", "global"]


@dataclass(frozen=True)
class Agent:
    command: str
    name: str
    executable: str
    supports_local: bool


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    supports_local: bool


_AGENT_DEFINITIONS = {
    "claude": AgentDefinition(name="Claude Code", supports_local=True),
    "codex": AgentDefinition(name="Codex CLI", supports_local=False),
    "copilot": AgentDefinition(name="GitHub Copilot CLI", supports_local=False),
    "pi": AgentDefinition(name="Pi", supports_local=True),
    "omp": AgentDefinition(name="Oh My Pi", supports_local=False),
}


def _find_command(command: str) -> str | None:
    """Resolve regular executables and Windows command shims."""
    return shutil.which(command) or shutil.which(f"{command}.cmd")


def find_agents() -> list[Agent]:
    agents: list[Agent] = []
    for command, definition in _AGENT_DEFINITIONS.items():
        executable = _find_command(command)
        if executable is not None:
            agents.append(
                Agent(
                    command=command,
                    name=definition.name,
                    executable=executable,
                    supports_local=definition.supports_local,
                )
            )
    return agents


def _run(agent: Agent, args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [agent.executable, *args],
            check=False,
            capture_output=capture,
            text=True,
            encoding="utf-8" if capture else None,
            errors="replace" if capture else None,
        )
    except OSError as exc:
        raise click.ClickException(f"could not start {agent.command}: {exc}") from exc


def _run_checked(agent: Agent, args: list[str]) -> None:
    console.print(f"[dim]> {agent.command} {' '.join(args)}[/dim]")
    result = _run(agent, args)
    if result.returncode != 0:
        raise click.ClickException(f"{agent.command} exited with status {result.returncode}")


def _query_json(agent: Agent, args: list[str]) -> Any | None:
    result = _run(agent, args, capture=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None


def _query_text(agent: Agent, args: list[str]) -> str | None:
    result = _run(agent, args, capture=True)
    return result.stdout if result.returncode == 0 else None


def _has_named_line(value: str | None, name: str) -> bool:
    """Find an exact item name in a human-readable CLI listing."""
    if value is None:
        return False
    expected = name.casefold()
    for line in value.splitlines():
        normalized = line.strip().lstrip("•◆*-").strip()
        listed_name = normalized.split(maxsplit=1)[0] if normalized else ""
        if listed_name.casefold() == expected:
            return True
    return False


def _has_named_item(value: Any, name: str) -> bool:
    """Find an exact name/id in a JSON result without depending on CLI schema details."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "name", "pluginId"} and isinstance(item, str) and item.casefold() == name.casefold():
                return True
            if _has_named_item(item, name):
                return True
    elif isinstance(value, list):
        return any(_has_named_item(item, name) for item in value)
    return False


def _is_claude_installed(agent: Agent, scope: Scope) -> bool:
    value = _query_json(agent, ["plugin", "list", "--json"])
    expected_scope = "project" if scope == "local" else "user"
    if not isinstance(value, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("id", "")).casefold() == PLUGIN_ID.casefold()
        and item.get("scope") == expected_scope
        for item in value
    )


def _has_claude_marketplace(agent: Agent) -> bool:
    value = _query_json(agent, ["plugin", "marketplace", "list", "--json"])
    return _has_named_item(value, "HexRaysSA")


def _install_claude(agent: Agent, scope: Scope) -> None:
    cli_scope = "project" if scope == "local" else "user"
    if _is_claude_installed(agent, scope):
        _run_checked(agent, ["plugin", "update", PLUGIN_ID, "--scope", cli_scope, "--yes"])
        return
    if not _has_claude_marketplace(agent):
        _run_checked(agent, ["plugin", "marketplace", "add", "HexRaysSA/claude-marketplace", "--scope", cli_scope])
    _run_checked(agent, ["plugin", "install", PLUGIN_ID, "--scope", cli_scope, "--yes"])


def _is_codex_installed(agent: Agent) -> bool:
    value = _query_json(agent, ["plugin", "list", "--json"])
    # The listing also has an "available" key with plugins that marketplaces offer but are not installed.
    if isinstance(value, dict) and "installed" in value:
        value = value["installed"]
    return _has_named_item(value, PLUGIN_ID)


def _has_codex_marketplace(agent: Agent) -> bool:
    value = _query_json(agent, ["plugin", "marketplace", "list", "--json"])
    return _has_named_item(value, "HexRaysSA")


def _install_codex(agent: Agent) -> None:
    if _is_codex_installed(agent):
        _run_checked(agent, ["plugin", "marketplace", "upgrade", "HexRaysSA"])
        return
    if _has_codex_marketplace(agent):
        _run_checked(agent, ["plugin", "marketplace", "upgrade", "HexRaysSA"])
    else:
        _run_checked(agent, ["plugin", "marketplace", "add", "HexRaysSA/codex-marketplace"])
    _run_checked(agent, ["plugin", "add", PLUGIN_ID])


def _is_copilot_installed(agent: Agent) -> bool:
    return _has_named_line(_query_text(agent, ["plugin", "list"]), "ida-mcp")


def _has_copilot_marketplace(agent: Agent) -> bool:
    return _has_named_line(
        _query_text(agent, ["plugin", "marketplace", "list"]),
        "HexRaysSA",
    )


def _install_copilot(agent: Agent) -> None:
    if _is_copilot_installed(agent):
        _run_checked(agent, ["plugin", "marketplace", "update", "HexRaysSA"])
        _run_checked(agent, ["plugin", "update", "ida-mcp"])
        return
    if _has_copilot_marketplace(agent):
        _run_checked(agent, ["plugin", "marketplace", "update", "HexRaysSA"])
    else:
        _run_checked(agent, ["plugin", "marketplace", "add", "HexRaysSA/copilot-marketplace"])
    _run_checked(agent, ["plugin", "install", PLUGIN_ID])


def _is_pi_installed(agent: Agent, scope: Scope) -> bool:
    result = _run(agent, ["list", "--no-approve"], capture=True)
    if result.returncode != 0:
        return False

    current_scope: Scope | None = None
    for line in result.stdout.splitlines():
        heading = line.strip().casefold()
        if heading == "user packages:":
            current_scope = "global"
        elif heading in {"project packages:", "local packages:"}:
            current_scope = "local"
        elif current_scope == scope and "hexrayssa/ida-mcp" in heading:
            return True
    return False


def _install_pi(agent: Agent, scope: Scope) -> None:
    pi_source = "git:github.com/HexRaysSA/ida-mcp@latest"
    if _is_pi_installed(agent, scope):
        _run_checked(agent, ["update", "--extension", pi_source])
        return
    args = ["install", pi_source]
    if scope == "local":
        args.append("--local")
    _run_checked(agent, args)


def _is_omp_installed(agent: Agent) -> bool:
    value = _query_json(agent, ["plugin", "list", "--json"])
    return _has_named_item(value, "ida-mcp")


def _install_omp(agent: Agent) -> None:
    # omp installs a git source as an npm package for the current user and ignores --scope for it.
    if _is_omp_installed(agent):
        _run_checked(agent, ["plugin", "upgrade", "ida-mcp"])
        return
    _run_checked(agent, ["plugin", "install", "github:HexRaysSA/ida-mcp#latest"])


def install_agent(agent: Agent, scope: Scope) -> None:
    if agent.command == "claude":
        _install_claude(agent, scope)
    elif agent.command == "codex":
        _install_codex(agent)
    elif agent.command == "copilot":
        _install_copilot(agent)
    elif agent.command == "pi":
        _install_pi(agent, scope)
    elif agent.command == "omp":
        _install_omp(agent)
    else:  # pragma: no cover - Agent instances are built from the fixed definitions above.
        raise click.ClickException(f"unsupported agent: {agent.name}")
