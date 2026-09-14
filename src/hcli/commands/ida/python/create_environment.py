from __future__ import annotations

import logging
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
from hcli.lib.ida.plugin.install import (
    PluginDependencyInfo,
    collect_plugin_dependencies,
    install_single_plugin_dependencies,
)
from hcli.lib.ida.python.environment import get_recommended_venv_dir, get_system, render_set_env_var_command
from hcli.lib.ida.python.platform_env import (
    build_configuration_plan,
    execute_configuration_plan,
    verify_env_var_in_subprocess,
)
from hcli.lib.ida.python.venv_create import (
    TargetInspection,
    VenvCreationError,
    create_virtual_environment,
    determine_target_python_version,
    find_python_on_path,
    find_uv,
    get_registered_python_exe,
    inspect_target,
    plan_virtual_environment,
)

logger = logging.getLogger(__name__)

ENV_VAR = "IDAPYTHON_VENV_EXECUTABLE"


class PluginMigrationResult(BaseModel):
    name: str
    dependencies: list[str]
    success: bool
    error: str | None = None


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
    # whether HCLI persisted the variable (shell profile or setx)
    configured: bool
    configured_via: str | None
    plugin_migrations: list[PluginMigrationResult] = []
    plugins_skipped: bool = False


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

    Builds a platform-specific configuration plan, shows it to the user,
    and executes it if they agree.  Returns (configured, description).
    When not interactive, prints manual instructions instead.
    """
    value = str(python_exe)
    out = stderr_console if quiet else console

    current = ENV.IDAPYTHON_VENV_EXECUTABLE
    if current and Path(current) != python_exe:
        out.print(f"[yellow]${ENV_VAR} is currently {escape(current)}. It must change.[/yellow]")

    plan = build_configuration_plan(ENV_VAR, value)

    out.print(f"\nTo make IDA use this environment, {ENV_VAR} must be set in your login session.")
    out.print("HCLI will:", highlight=False)
    for i, step in enumerate(plan.steps, 1):
        out.print(f"  {i}. {escape(step.description)}", highlight=False)
        if step.file_path is not None:
            out.print(f"     [dim]{escape(str(step.file_path))}[/dim]", highlight=False)

    if not interactive:
        out.print(f"\n{escape(plan.manual_instructions)}", highlight=False)
        return False, None

    consented = Confirm.ask("\nApply these changes?", default=True, console=console)
    if not consented:
        out.print(f"\n{escape(plan.manual_instructions)}", highlight=False)
        return False, None

    results = execute_configuration_plan(plan)
    configured_via_parts: list[str] = []
    any_failed = False
    for result in results:
        if result.skipped:
            out.print(f"  [dim]{escape(result.message)}[/dim]")
        elif result.success:
            out.print(f"  [green]{escape(result.message)}[/green]")
            configured_via_parts.append(result.step.kind)
        else:
            out.print(f"  [red]{escape(result.message)}[/red]")
            any_failed = True

    if any_failed:
        out.print("\n[yellow]Some steps failed. Review the output above.[/yellow]")
        out.print(f"Manual instructions:\n{escape(plan.manual_instructions)}", highlight=False)
        return False, None

    verified = verify_env_var_in_subprocess(ENV_VAR, value)
    if verified is True:
        out.print(f"\n[green]{ENV_VAR} is set and verified.[/green]")
    elif verified is False:
        out.print(f"\n[yellow]{ENV_VAR} was written but could not be verified in a subprocess.[/yellow]")

    if plan.needs_logout:
        out.print("[dim]Log out and back in for all changes to take effect.[/dim]")
    else:
        out.print("[dim]Restart IDA and any open terminals for the change to take effect.[/dim]")

    return True, ", ".join(configured_via_parts) if configured_via_parts else None


def _print_migration_plan(
    out,
    plugins: list[PluginDependencyInfo],
) -> None:
    total_deps = sum(len(p.dependencies) for p in plugins)
    out.print(
        f"\n{len(plugins)} installed plugin(s) have {total_deps} Python "
        f"dependenc{'y' if total_deps == 1 else 'ies'} to install in the new environment:"
    )
    for plugin in plugins:
        deps_str = ", ".join(plugin.dependencies)
        out.print(f"  [blue]{plugin.name}[/blue]: {deps_str}")
    out.print()


def _run_migration(
    out,
    python_exe: Path,
    plugins: list[PluginDependencyInfo],
) -> list[PluginMigrationResult]:
    results: list[PluginMigrationResult] = []
    for plugin in plugins:
        with rich.status.Status(f"installing dependencies for {plugin.name}", console=stderr_console):
            result = install_single_plugin_dependencies(python_exe, plugin)
        mr = PluginMigrationResult(
            name=result.name,
            dependencies=result.dependencies,
            success=result.success,
            error=result.error,
        )
        if result.success:
            out.print(f"  [green]Installed[/green] dependencies for [blue]{plugin.name}[/blue]")
        else:
            out.print(f"  [red]Failed[/red] dependencies for [blue]{plugin.name}[/blue]")
        results.append(mr)
    return results


def _print_failure_summary(out, failed: list[PluginMigrationResult]) -> None:
    out.print()
    out.print(f"[yellow]Warning:[/yellow] {len(failed)} plugin(s) could not have their dependencies installed:")
    for result in failed:
        out.print(f"  [blue]{result.name}[/blue]: {result.error}")
    out.print()
    out.print(
        "Reinstall these plugins from their original source "
        f"(`{ENV.HCLI_BINARY_NAME} plugin install <name>`) so their "
        "dependencies are available in the new environment."
    )


def migrate_plugin_dependencies(
    *,
    python_exe: Path,
    reinstall_plugins: bool,
    interactive: bool,
    quiet: bool,
) -> tuple[list[PluginMigrationResult], bool]:
    """Reinstall Python dependencies for existing plugins into a new venv.

    Returns:
        (results, skipped) where skipped is True when migration
        was not attempted (user declined or --no-reinstall-plugins).
    """
    out = stderr_console if quiet else console

    with rich.status.Status("checking installed plugins for Python dependencies", console=stderr_console):
        plugins = collect_plugin_dependencies()

    if not plugins:
        logger.info("no installed plugins require Python dependencies")
        return [], False

    if not reinstall_plugins:
        logger.warning(
            "%d plugin(s) have Python dependencies that were not installed: %s",
            len(plugins),
            ", ".join(p.name for p in plugins),
        )
        out.print(
            f"[yellow]Warning:[/yellow] {len(plugins)} plugin(s) have Python dependencies "
            f"that were not installed (--no-reinstall-plugins)."
        )
        for plugin in plugins:
            deps_str = ", ".join(plugin.dependencies)
            out.print(f"  [blue]{plugin.name}[/blue]: {deps_str}")
        out.print(
            f"Reinstall these plugins with `{ENV.HCLI_BINARY_NAME} plugin install <name>` "
            "to restore their dependencies."
        )
        return [], True

    _print_migration_plan(out, plugins)

    if interactive and not Confirm.ask("Install these dependencies?", default=True, console=console):
        out.print("Skipped plugin dependency installation.")
        return [], True

    results = _run_migration(out, python_exe, plugins)

    failed = [r for r in results if not r.success]
    if failed:
        _print_failure_summary(out, failed)
        logger.warning(
            "%d plugin(s) could not have their dependencies installed: %s",
            len(failed),
            ", ".join(f.name for f in failed),
        )

    return results, False


def run_create_environment(
    *,
    path: Path | None,
    python_version: str | None,
    configure: bool,
    reinstall_plugins: bool,
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

    plugin_migrations: list[PluginMigrationResult] = []
    plugins_skipped = False

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

        plugin_migrations, plugins_skipped = migrate_plugin_dependencies(
            python_exe=python_exe,
            reinstall_plugins=reinstall_plugins,
            interactive=interactive,
            quiet=quiet,
        )
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
        plugin_migrations=plugin_migrations,
        plugins_skipped=plugins_skipped,
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
@click.option(
    "--no-reinstall-plugins",
    is_flag=True,
    help=(
        "Skip reinstalling Python dependencies for existing plugins into the new "
        "environment. Use this for offline setups or when you plan to reinstall "
        "plugins manually."
    ),
)
@click.option("--json", "json_output", is_flag=True, help="Output the result as JSON.")
def create_environment(
    path: Path | None,
    python_version: str | None,
    no_configure: bool,
    no_reinstall_plugins: bool,
    json_output: bool,
) -> None:
    """Create a virtual environment for IDA's Python and configure IDA to use it.

    The environment is created at $IDAUSR/venv with the Python version that
    idapyswitch registered for IDA, and seeded with pip. Existing directories
    are never modified or replaced.

    When plugins with Python dependencies are already installed, their
    dependencies are reinstalled into the new environment. Pass
    --no-reinstall-plugins to skip this step.

    Nothing outside the target directory changes without your consent. HCLI
    shows the exact shell profile line (or setx command on Windows) first.
    You can decline and apply it yourself.
    """
    result = run_create_environment(
        path=path,
        python_version=python_version,
        configure=not no_configure,
        reinstall_plugins=not no_reinstall_plugins,
        interactive=_is_interactive() and not json_output,
        quiet=json_output,
    )

    if json_output:
        print_json(result.model_dump(mode="json"))
