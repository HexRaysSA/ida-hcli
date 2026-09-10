from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import rich.status
import rich_click as click
from pydantic import BaseModel
from rich.markup import escape
from rich.prompt import Confirm

from hcli.env import ENV
from hcli.lib.console import console, print_json, stderr_console
from hcli.lib.ida import get_ida_user_dir
from hcli.lib.ida.python.environment import get_recommended_venv_dir, get_system, render_set_env_var_command
from hcli.lib.ida.python.venv_create import (
    TargetInspection,
    VenvCreationError,
    append_to_shell_profile,
    create_virtual_environment,
    detect_shell,
    determine_target_python_version,
    find_python_on_path,
    find_uv,
    get_registered_python_exe,
    get_shell_profile_path,
    inspect_target,
    plan_virtual_environment,
    render_profile_line,
    set_windows_user_env_var,
)

logger = logging.getLogger(__name__)

ENV_VAR = "IDAPYTHON_VENV_EXECUTABLE"


class CreateEnvironmentResult(BaseModel):
    venv_path: str
    python_exe: str
    python_version: str
    python_version_source: str
    # False when a healthy venv already existed at the path
    created: bool
    tool: str | None
    # the command that makes IDA use the venv
    set_command: str
    # whether hcli persisted the variable (shell profile or setx)
    configured: bool
    configured_via: str | None


class CreateEnvironmentError(click.ClickException):
    """The environment can't be created as requested; the message says why and what to do."""


def _explain_existing_target(inspection: TargetInspection) -> str:
    target = inspection.path
    if inspection.kind == "wrong-version-venv":
        return (
            f"{target} {inspection.reason}.\n"
            f"A virtual environment cannot change its Python version. You must recreate it. "
            f"{ENV.HCLI_BINARY_NAME} never deletes it for you.\n"
            f"  1. Remove the directory: {target}\n"
            f"     This deletes the packages installed in it. Reinstall plugins afterwards with "
            f"`{ENV.HCLI_BINARY_NAME} plugin install`.\n"
            f"  2. Run `{ENV.HCLI_BINARY_NAME} ida python create-environment` again."
        )
    if inspection.kind == "broken-venv":
        return (
            f"{target} {inspection.reason}.\n"
            f"Remove the directory and run `{ENV.HCLI_BINARY_NAME} ida python create-environment` again. "
            f"Or pass --path to use a different location."
        )
    return (
        f"{target} {inspection.reason}.\n"
        f"{ENV.HCLI_BINARY_NAME} does not overwrite existing files. Move them away, or pass --path to create "
        f"the virtual environment somewhere else."
    )


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def configure_env_var(python_exe: Path, *, interactive: bool, quiet: bool) -> tuple[bool, str | None]:
    """Make `IDAPYTHON_VENV_EXECUTABLE` point at `python_exe`, with the user's consent.

    Prints the exact change before asking. Returns (configured, how).
    When not interactive, nothing is written and instructions are printed instead.
    """
    system = get_system()
    value = str(python_exe)
    set_command = render_set_env_var_command(ENV_VAR, value, system)
    out = stderr_console if quiet else console

    current = ENV.IDAPYTHON_VENV_EXECUTABLE
    if current and Path(current) != python_exe:
        out.print(f"[yellow]${ENV_VAR} is currently {escape(current)}. It must change.[/yellow]")

    if system == "windows":
        out.print(f"To make IDA use this environment, set {ENV_VAR} for your user account:")
        out.print(f"  {escape(set_command)}", highlight=False)
        if interactive and Confirm.ask("Run this setx command now?", default=False, console=console):
            set_windows_user_env_var(ENV_VAR, value)
            out.print(f"[green]Set {ENV_VAR} for your user account. Restart IDA and any open terminals.[/green]")
            return True, "setx"
        out.print("Then restart IDA and any open terminals.")
        return False, None

    shell = detect_shell(os.environ.get("SHELL"))
    profile = get_shell_profile_path(shell, Path.home())
    line = render_profile_line(ENV_VAR, value, shell)

    out.print(f"To make IDA use this environment, export {ENV_VAR} in your shell profile:")
    out.print(f"  {escape(line)}", highlight=False)

    consented = (
        profile is not None
        and interactive
        and Confirm.ask(f"Append this line to {profile}?", default=False, console=console)
    )
    if consented:
        assert profile is not None
        if append_to_shell_profile(profile, line):
            out.print(f"[green]Added to {escape(str(profile))}.[/green] Open a new terminal, then start IDA from it.")
        else:
            out.print(f"[green]{escape(str(profile))} already contains this line.[/green]")
        _print_mac_launch_note(out, system)
        return True, str(profile)

    if profile is not None:
        out.print(f"Add it to {escape(str(profile))}. Open a new terminal, then start IDA from it.")
    else:
        out.print("Add it to your shell's startup file. Open a new terminal, then start IDA from it.")
    _print_mac_launch_note(out, system)
    return False, None


def _print_mac_launch_note(out, system: str) -> None:
    if system == "mac":
        out.print(
            "[dim]Shell profiles do not apply to IDA started from Finder or the Dock. For that, run "
            f"`launchctl setenv {ENV_VAR} <path>`, or start IDA from a terminal.[/dim]"
        )


def run_create_environment(
    *,
    path: Path | None,
    python_version: str | None,
    configure: bool,
    interactive: bool,
    quiet: bool,
) -> CreateEnvironmentResult:
    """Create (or recognize) IDA's virtualenv and optionally configure IDA to use it.

    `quiet` sends progress to stderr so stdout can carry JSON.

    Raises:
        CreateEnvironmentError: when the target exists in an unusable form, or creation fails.
    """
    out = stderr_console if quiet else console

    with rich.status.Status("determining IDA's Python version", console=stderr_console):
        try:
            version = determine_target_python_version(python_version)
        except ValueError as e:
            raise CreateEnvironmentError(str(e)) from e
        except VenvCreationError as e:
            raise CreateEnvironmentError(str(e)) from e

    if path is None:
        try:
            idausr = get_ida_user_dir()
        except ValueError as e:
            raise CreateEnvironmentError(f"cannot determine $IDAUSR ({e}). Pass --path.") from e
        target = get_recommended_venv_dir(idausr)
    else:
        target = path.expanduser().absolute()

    out.print(f"Python version for the environment: {version.version} [dim](via {version.source})[/dim]")

    with rich.status.Status(f"inspecting {target}", console=stderr_console):
        inspection = inspect_target(target, version.version)

    system = get_system()

    if inspection.kind == "healthy-venv":
        assert inspection.python_exe is not None
        out.print(
            f"[green]{escape(str(target))} is already a Python {version.version} virtual environment with pip.[/green]"
        )
        python_exe = inspection.python_exe
        created = False
        tool = None
    elif inspection.kind == "missing":
        registered = get_registered_python_exe(version.probe)
        uv_exe = find_uv()
        path_python = None
        if registered is None and uv_exe is None:
            with rich.status.Status(f"looking for python{version.version} on PATH", console=stderr_console):
                path_python = find_python_on_path(version.version)
        try:
            plan = plan_virtual_environment(
                target, version.version, registered_python=registered, uv_exe=uv_exe, path_python=path_python
            )
        except VenvCreationError as e:
            raise CreateEnvironmentError(str(e)) from e

        out.print(f"Creating virtual environment: [dim]{escape(plan.render_command())}[/dim]", highlight=False)
        with rich.status.Status("creating virtual environment", console=stderr_console):
            try:
                python_exe = create_virtual_environment(plan, system)
            except VenvCreationError as e:
                raise CreateEnvironmentError(str(e)) from e
        out.print(f"[green]Created {escape(str(target))} with Python {version.version} and pip.[/green]")
        created = True
        tool = plan.tool
    else:
        raise CreateEnvironmentError(_explain_existing_target(inspection))

    set_command = render_set_env_var_command(ENV_VAR, str(python_exe), system)
    configured = False
    configured_via: str | None = None

    current = ENV.IDAPYTHON_VENV_EXECUTABLE
    already_configured = bool(current) and Path(current or "").resolve() == python_exe.resolve()
    if configure and already_configured:
        out.print(f"[green]${ENV_VAR} already points to this environment.[/green]")
    elif configure:
        configured, configured_via = configure_env_var(python_exe, interactive=interactive, quiet=quiet)
    else:
        out.print(f"Skipped configuring ${ENV_VAR}. To make IDA use this environment:")
        out.print(f"  {escape(set_command)}", highlight=False)

    return CreateEnvironmentResult(
        venv_path=str(target),
        python_exe=str(python_exe),
        python_version=version.version,
        python_version_source=version.source,
        created=created,
        tool=tool,
        set_command=set_command,
        configured=configured,
        configured_via=configured_via,
    )


@click.command()
@click.option(
    "--path",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to create the virtual environment (default: $IDAUSR/venv).",
)
@click.option(
    "--python-version",
    default=None,
    metavar="X.Y",
    help="Python version to use when idat is not available. IDA's own version wins when known.",
)
@click.option(
    "--no-configure",
    is_flag=True,
    help=f"Do not offer to set {ENV_VAR}. Only create the environment.",
)
@click.option("--json", "json_output", is_flag=True, help="Output the result as JSON.")
def create_environment(path: Path | None, python_version: str | None, no_configure: bool, json_output: bool) -> None:
    """Create a virtual environment for IDA's Python and configure IDA to use it.

    The environment is created at $IDAUSR/venv with the Python version that
    idapyswitch registered for IDA, and seeded with pip. Existing directories
    are never modified or replaced.

    Nothing outside the target directory changes without your consent. hcli
    shows the exact shell profile line (or setx command on Windows) first.
    You can decline and apply it yourself.
    """
    result = run_create_environment(
        path=path,
        python_version=python_version,
        configure=not no_configure,
        interactive=_is_interactive() and not json_output,
        quiet=json_output,
    )

    if json_output:
        print_json(result.model_dump(mode="json"))
