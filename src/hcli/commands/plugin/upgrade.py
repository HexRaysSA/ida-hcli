from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import httpx
import rich.status
import rich_click as click

from hcli.lib.console import console, stderr_console
from hcli.lib.ida import (
    FailedToDetectIDAVersion,
    MissingCurrentInstallationDirectory,
    explain_failed_to_detect_ida_version,
    explain_missing_current_installation_directory,
    find_current_ida_platform,
    find_current_ida_version,
)
from hcli.lib.ida.plugin import IDAMetadataDescriptor, iter_dependency_specs
from hcli.lib.ida.plugin.exceptions import InstallExecutionError, PluginNotInstalledError
from hcli.lib.ida.plugin.install import find_installed_plugin, plan_plugin_operation, sweep_trash
from hcli.lib.ida.plugin.reference import normalize_plugin_host, parse_dependency_spec, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo
from hcli.lib.ida.plugin.resolve import RepositoryRoot
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

from ._install_flow import collect_configuration, report_install_failure, report_install_result, validate_bundle_target

logger = logging.getLogger(__name__)


def _dependency_names(metadata: IDAMetadataDescriptor) -> set[str]:
    return {parse_dependency_spec(spec.plugin).name for _, spec in iter_dependency_specs(metadata)}


def _report_dropped_dependencies(old: IDAMetadataDescriptor, new: IDAMetadataDescriptor) -> None:
    dropped = _dependency_names(old) - _dependency_names(new)
    if not dropped:
        return
    console.print(f"[yellow]Note[/yellow]: these dependencies were removed from [blue]{new.plugin.name}[/blue]:")
    for name in sorted(dropped):
        console.print(f"  {name}")
    console.print("They remain installed; remove them manually if no longer needed.")


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option(
    "--dependency-config",
    multiple=True,
    help="Configuration setting for a dependency in plugin.key=value format.",
)
@click.option(
    "--no-build-isolation",
    is_flag=True,
    default=False,
    help="Disable pip build isolation when installing Python dependencies",
)
def upgrade_plugin(ctx, plugin: str, dependency_config: tuple[str, ...], no_build_isolation: bool) -> None:
    """Upgrade an installed plugin to the latest compatible version."""
    pip_options: PipOptions = ctx.obj.get("pip_options", PIP_OPTIONS_DEFAULT)
    if no_build_isolation:
        pip_options = dataclasses.replace(pip_options, no_build_isolation=True)
    check_environment = not ctx.obj.get("no_python_environment_check", False)
    plugin_spec = plugin
    try:
        sweep_trash()

        current_ida_platform = find_current_ida_platform()
        current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("file://"):
            raise ValueError("cannot upgrade using local file; uninstall/reinstall instead")

        if plugin_spec.startswith("https://"):
            raise ValueError("cannot upgrade using URL; uninstall/reinstall instead")

        try:
            ref = parse_plugin_reference(plugin_spec)
        except ValueError as e:
            raise click.BadParameter(f"invalid plugin reference: {plugin_spec!r}: {e}")

        try:
            installed = find_installed_plugin(ref.name)
        except PluginNotInstalledError:
            console.print(f"[red]Error[/red]: plugin '{ref.name}' is not installed")
            raise click.Abort()

        if ref.host is not None and normalize_plugin_host(installed.host) != normalize_plugin_host(ref.host):
            console.print(
                f"[red]Error[/red]: installed plugin '{installed.name}' comes from {installed.host}, not {ref.host}"
            )
            console.print(
                "Upgrade cannot switch repositories. Uninstall first, then install the other qualified plugin."
            )
            raise click.Abort()

        if ref.repo:
            from hcli.commands.plugin import repo_for_reference

            plugin_repo: BasePluginRepo = repo_for_reference(ctx, ref)
        else:
            plugin_repo = ctx.obj["plugin_repo"]

        logger.info("finding plugin in repository")
        try:
            with rich.status.Status("resolving dependencies", console=stderr_console):
                operation = plan_plugin_operation(
                    [RepositoryRoot(ref, plugin_repo, repo_name=ref.repo, upgrade=True)],
                    plugin_repo=plugin_repo,
                    current_platform=current_ida_platform,
                    current_version=current_ida_version,
                )
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

        validate_bundle_target(plugin_repo, operation.plan, pip_options, current_ida_platform)
        collect_configuration(operation, dependency_config=dependency_config)

        try:
            with rich.status.Status("upgrading plugin", console=stderr_console):
                result = operation.execute(pip_options=pip_options, check_environment=check_environment)
        except InstallExecutionError as e:
            logger.debug("error: %s", e, exc_info=True)
            report_install_failure(e)
            raise click.Abort()

        report_install_result(result, present_label="Already up to date")
        _report_dropped_dependencies(installed.metadata, operation.root.metadata)

    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except KeyError as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        aggregate = ctx.obj.get("plugin_repos")
        if aggregate is not None:
            for note in aggregate.notes():
                console.print(f"[yellow]Warning:[/yellow] repository {note}")
        raise click.Abort()

    except click.Abort:
        raise

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
