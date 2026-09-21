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
)
from hcli.lib.ida.plugin import IDAMetadataDescriptor, get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.plugin.dependencies import install_dependencies
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import find_installed_plugin, sweep_trash, upgrade_plugin_archive
from hcli.lib.ida.plugin.reference import normalize_plugin_host, parse_dependency_spec, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option(
    "--no-build-isolation",
    is_flag=True,
    default=False,
    help="Disable pip build isolation when installing Python dependencies",
)
def upgrade_plugin(ctx, plugin: str, no_build_isolation: bool) -> None:
    """Upgrade an installed plugin to the latest compatible version."""
    pip_options: PipOptions = ctx.obj.get("pip_options", PIP_OPTIONS_DEFAULT)
    if no_build_isolation:
        pip_options = dataclasses.replace(pip_options, no_build_isolation=True)
    check_environment = not ctx.obj.get("no_python_environment_check", False)
    plugin_spec = plugin
    try:
        sweep_trash()

        ida_env = IDAEnvironment.from_current()
        install_opts = InstallOptions(pip_options=pip_options, check_environment=check_environment)
        install_ctx = InstallContext(env=ida_env, options=install_opts)

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

        # Resolve the installed plugin first so we can anchor the upgrade to
        # the repository the user currently has installed. This avoids
        # switching repositories implicitly and also resolves the host for
        # bare-name upgrades even when the repository has a colliding name.
        try:
            installed = find_installed_plugin(ref.name)
        except PluginNotInstalledError:
            console.print(f"[red]Error[/red]: plugin '{ref.name}' is not installed")
            raise click.Abort()

        old_deps = list(installed.metadata.plugin.dependencies)

        if ref.host is not None and normalize_plugin_host(installed.host) != normalize_plugin_host(ref.host):
            console.print(
                f"[red]Error[/red]: installed plugin '{installed.name}' comes from {installed.host}, not {ref.host}"
            )
            console.print(
                "Upgrade cannot switch repositories. Uninstall first, then install the other qualified plugin."
            )
            raise click.Abort()

        # Anchor the lookup to the installed host regardless of whether the
        # user supplied it. This is what makes bare-name upgrades work even
        # when the repository has a colliding name.
        bare_spec = ref.name + ref.version_spec
        logger.info("finding plugin in repository")
        # An upgrade is anchored to the installed name@host, so it may resolve
        # across every configured repository -- the plugin's identity, not the
        # default scope, decides which one answers. An explicit prefix narrows
        # that to one repository, mirroring the @host check above.
        if ref.repo:
            from hcli.commands.plugin import repo_for_reference

            plugin_repo: BasePluginRepo = repo_for_reference(ctx, ref)
        else:
            plugin_repo = ctx.obj["plugin_repo"]
        try:
            plugin_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
                bare_spec, ida_env.platform, ida_env.ida_version, host=installed.host
            )
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

        from hcli.commands.plugin import resolve_bundle_install_context

        with resolve_bundle_install_context(
            plugin_repo, install_ctx, plugin_name, host=installed.host
        ) as effective_ctx:
            upgrade_plugin_archive(buf, plugin_name, effective_ctx)

        _, metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")

        try:
            # Resolve deps across all configured repos, not just the source repo.
            dep_repo = ctx.obj.get("plugin_repos") or plugin_repo
            _handle_upgrade_dependencies(
                old_deps=old_deps,
                new_metadata=metadata,
                plugin_repo=dep_repo,
                install_ctx=install_ctx,
            )
        except Exception as dep_err:
            logger.debug("dependency handling failed: %s", dep_err, exc_info=True)
            console.print(f"[yellow]Warning[/yellow]: failed to process dependencies: {dep_err}")
    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except KeyError as e:
        # get_plugins() drops repositories it could not consult, so a miss
        # here may mean "your session expired", not "no such plugin".
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


def _handle_upgrade_dependencies(
    *,
    old_deps: list[str],
    new_metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo,
    install_ctx: InstallContext,
) -> None:
    new_deps = list(new_metadata.plugin.dependencies)
    if not old_deps and not new_deps:
        return

    old_names = {parse_dependency_spec(s).name for s in old_deps}
    new_names = {parse_dependency_spec(s).name for s in new_deps}

    dropped = old_names - new_names
    if dropped:
        console.print(
            f"[yellow]Note[/yellow]: these dependencies were removed from [blue]{new_metadata.plugin.name}[/blue]:"
        )
        for name in sorted(dropped):
            console.print(f"  {name}")
        console.print("They remain installed; remove them manually if no longer needed.")

    if new_deps:
        console.print(f"Checking dependencies for [blue]{new_metadata.plugin.name}[/blue]...")
        with rich.status.Status("checking dependencies", console=stderr_console):
            result = install_dependencies(
                metadata=new_metadata,
                plugin_repo=plugin_repo,
                ctx=install_ctx,
            )

        for name in result.installed:
            console.print(f"  [green]Installed[/green] dependency: [blue]{name}[/blue]")
        for name in result.upgraded:
            console.print(f"  [green]Upgraded[/green] dependency: [blue]{name}[/blue]")
        for name in result.skipped:
            console.print(f"  [dim]Skipped[/dim] dependency: {name} (already installed)")
        for name, error in result.failed:
            console.print(f"  [red]Failed[/red] dependency: {name}: {error}")
