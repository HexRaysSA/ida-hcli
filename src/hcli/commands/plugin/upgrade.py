from __future__ import annotations

import dataclasses
import logging

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
from hcli.lib.ida.plugin import get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import apply_upgrade, find_installed_plugin, sweep_trash
from hcli.lib.ida.plugin.reference import normalize_plugin_host, parse_plugin_reference
from hcli.lib.ida.plugin.repo import BasePluginRepo
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

from .install import render_install_result

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

        if plugin_spec.endswith(".zip"):
            from pathlib import Path

            if Path(plugin_spec).exists():
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
        # An explicit repo prefix narrows resolution to one repository,
        # mirroring the @host check above.
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

        _, metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        from hcli.commands.plugin import resolve_bundle_install_context

        dep_repo = ctx.obj.get("plugin_repos") or plugin_repo

        with (
            resolve_bundle_install_context(plugin_repo, install_ctx, plugin_name, host=installed.host) as effective_ctx,
            rich.status.Status("upgrading plugin", console=stderr_console),
        ):
            result = apply_upgrade(
                zip_data=buf,
                plugin_name=plugin_name,
                metadata=metadata,
                ctx=effective_ctx,
                plugin_repo=dep_repo,
                old_deps=old_deps,
            )

        render_install_result(result, is_upgrade=True)

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
