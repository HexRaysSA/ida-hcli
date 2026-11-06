"""Interactive IDA MCP installer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import questionary
import rich_click as click

from hcli.commands.mcp._agents import Scope, find_agents, get_scope_description, install_agent
from hcli.commands.plugin.install import install_plugin
from hcli.lib.console import console, print_json, stderr_console
from hcli.lib.constants import cli
from hcli.lib.ida.python.environment import get_system


def _require_uv() -> None:
    """Every integration runs the server through uv, so a missing uv fails later and less clearly."""
    if shutil.which("uv") and shutil.which("uvx"):
        return
    install_command = {
        "windows": 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
        "mac": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh",
    }[get_system()]
    raise click.ClickException(
        "uv is required to run the IDA MCP server, but it is not on PATH.\n\n"
        f"    {install_command}\n\n"
        f"Documentation: https://docs.astral.sh/uv/#installation"
    )


def _is_git_available() -> bool:
    """Probe with `git --version` rather than PATH alone.

    macOS ships a `/usr/bin/git` stub that exists whether or not the Command Line
    Tools are installed, so `shutil.which` alone reports a git that cannot run.
    Invoking the stub opens an installer dialog and waits on it, hence the timeout.
    """
    if shutil.which("git") is None:
        return False
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _require_git() -> None:
    """The agent CLIs clone their marketplaces and extensions; the mcp.json path does not."""
    if _is_git_available():
        return
    install_instructions = {
        "mac": (
            "Option 1 - Xcode Command Line Tools (no extra software needed):\n"
            "    xcode-select --install\n"
            "  A dialog appears; click Install and wait for it to finish.\n"
            "\n"
            "Option 2 - Homebrew (newer git, easier to update):\n"
            "    brew install git"
        ),
        "linux": (
            "Install it with your package manager:\n"
            "    Debian / Ubuntu / Mint    sudo apt update && sudo apt install git\n"
            "    Fedora / RHEL / Rocky     sudo dnf install git\n"
            "    Arch / Manjaro            sudo pacman -S git\n"
            "    openSUSE                  sudo zypper install git\n"
            "    Alpine                    sudo apk add git"
        ),
        "windows": (
            "Option 1 - winget (built into Windows 10 1709+ and Windows 11):\n"
            "    winget install --id Git.Git -e --source winget\n"
            "\n"
            "Option 2 - the installer at https://git-scm.com/download/win\n"
            '  On the PATH screen, keep "Git from the command line and also from 3rd-party software".\n'
            "\n"
            "Other package managers:\n"
            "    choco install git\n"
            "    scoop install git\n"
            "\n"
            "Close and reopen your terminal afterwards - PATH changes do not reach shells that are already running."
        ),
    }[get_system()]
    raise click.ClickException(
        "git is required to install agent integrations, but it is not available.\n\n"
        f"{install_instructions}\n\n"
        f"Then verify with `git --version`. See https://git-scm.com for more."
    )


def _print_mcp_json() -> None:
    """Print the configuration to stdout, keeping the guidance around it on stderr."""
    stderr_console.print("Add the following to your agent's MCP configuration, then restart it:")
    print_json(
        {
            "mcpServers": {
                "ida": {
                    "command": "uvx",
                    "args": [
                        "--exclude-newer=1s",
                        "ida-mcp",
                        "stdio",
                    ],
                }
            }
        }
    )


def _install_ida_plugin(ctx: click.Context) -> None:
    # `plugin@url` references resolve through the repository state that the
    # `plugin` group callback builds, and `mcp` bypasses that group. Run it
    # here, exactly as `hcli plugin install` would, so the two stay in sync.
    from hcli.commands.plugin import plugin as plugin_group

    with click.Context(plugin_group, info_name="plugin", parent=ctx, obj={}) as plugin_ctx:
        plugin_ctx.invoked_subcommand = "install"
        plugin_ctx.invoke(plugin_group)
        # `upgrade=True`: this command is the entry point users re-run to (re)wire
        # an agent, so an already-installed ida-mcp must upgrade in place rather
        # than abort before the agent integration below ever runs.
        plugin_ctx.invoke(
            install_plugin,
            plugin="ida-mcp@https://github.com/HexRaysSA/ida-mcp",
            editable=False,
            config=(),
            no_build_isolation=False,
            upgrade=True,
        )


@click.command()
@click.pass_context
def install(ctx: click.Context) -> None:
    """Install IDA MCP for Claude, Codex, Copilot, Pi, or Oh My Pi."""
    _install_ida_plugin(ctx)
    _require_uv()

    agents = find_agents()
    if not agents:
        # The IDA-side plugin is installed either way, so an agent we cannot
        # drive is not a failure: hand over the configuration to apply by hand.
        stderr_console.print(
            "[yellow]No supported agent command found on PATH (claude, codex, copilot, pi, or omp).[/yellow]"
        )
        _print_mcp_json()
        return

    choices = [questionary.Choice(f"{agent.name} ({Path(agent.executable).name})", value=agent) for agent in agents]
    choices.append(questionary.Choice("Other agent (mcp.json)", value="manual"))
    selected = questionary.select(
        "Select an agent:",
        choices=choices,
        style=cli.SELECT_STYLE,
    ).ask()
    if selected is None:
        raise click.Abort()

    if selected == "manual":
        _print_mcp_json()
        return

    # Only the agent integrations need git, and only once an agent is chosen:
    # asking for a scope first would strand the user behind a fixable error.
    _require_git()

    scope: Scope = "global"
    if selected.supports_local:
        chosen_scope = questionary.select(
            "Select installation scope:",
            choices=[
                questionary.Choice("Local (current repository)", value="local"),
                questionary.Choice("Global (current user)", value="global"),
            ],
            default="global",
            style=cli.SELECT_STYLE,
        ).ask()
        if chosen_scope is None:
            raise click.Abort()
        scope = chosen_scope

    install_agent(selected, scope)
    console.print(f"[green]Installed[/green] IDA MCP for [blue]{selected.name}[/blue] {get_scope_description(scope)}.")
