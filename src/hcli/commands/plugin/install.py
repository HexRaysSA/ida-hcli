"""Plugin install command."""

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
from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    InstalledPluginNameConflictError,
    InstallExecutionError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.install import pack_plugin_directory_to_zip, plan_plugin_operation, sweep_trash
from hcli.lib.ida.plugin.reference import (
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    parse_plugin_reference,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.repo.github import fetch_github_release_zip_asset, parse_github_url
from hcli.lib.ida.plugin.resolve import ArchiveRoot, EditableRoot, InstalledRoot, RepositoryRoot, RootRequest
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

from ._install_flow import (
    collect_configuration,
    report_install_failure,
    report_install_result,
    report_interrupted_install,
)

logger = logging.getLogger(__name__)


def _build_editable_root(plugin_spec: str) -> EditableRoot:
    source_dir = Path(plugin_spec).expanduser()
    if not source_dir.exists():
        raise click.BadParameter(f"path does not exist: {plugin_spec}")
    if not source_dir.is_dir():
        raise click.BadParameter(f"--editable requires a directory containing ida-plugin.json, got: {plugin_spec}")
    if not (source_dir / "ida-plugin.json").is_file():
        raise click.BadParameter(f"no ida-plugin.json found in {plugin_spec}")
    return EditableRoot(source_dir.resolve())


def _fetch_archive_bytes(plugin_spec: str) -> bytes | None:
    """Bytes of the archive named by a local path or URL, or ``None`` for a repository reference.

    Raises:
        click.Abort: the network is unavailable.
    """
    local = Path(plugin_spec).expanduser()
    if local.is_dir() and (local / "ida-plugin.json").is_file():
        logger.info("installing from the local file system (directory)")
        return pack_plugin_directory_to_zip(local.resolve())

    if local.exists() and plugin_spec.endswith(".zip"):
        logger.info("installing from the local file system")
        return local.read_bytes()

    if plugin_spec.startswith("file://"):
        logger.info("installing from the local file system")
        return fetch_plugin_archive(plugin_spec)

    if is_github_direct_install_url(plugin_spec):
        logger.info("installing from GitHub repository")
        try:
            owner, repo, tag = parse_github_url(plugin_spec)
            tag_info = f"@{tag}" if tag else " (latest release)"
            with rich.status.Status(f"fetching plugin from GitHub: {owner}/{repo}{tag_info}", console=stderr_console):
                return fetch_github_release_zip_asset(owner, repo, tag)
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]Cannot connect to GitHub - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

    if plugin_spec.startswith("https://"):
        logger.info("installing from HTTP URL")
        try:
            with rich.status.Status("fetching plugin", console=stderr_console):
                return fetch_plugin_archive(plugin_spec)
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print(f"[red]Cannot connect to {plugin_spec} - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

    return None


def _build_root(ctx, plugin_spec: str, editable: bool, upgrade: bool) -> tuple[RootRequest, BasePluginRepo | None]:
    """The root request for ``plugin_spec`` and the repository its dependencies resolve in.

    A repository prefix on the reference scopes the root alone; dependencies
    always resolve across every configured repository (or the one given with --repo).
    """
    if editable:
        return _build_editable_root(plugin_spec), ctx.obj.get("plugin_repo")

    zip_data = _fetch_archive_bytes(plugin_spec)
    if zip_data is not None:
        return ArchiveRoot(zip_data, upgrade=upgrade), ctx.obj.get("plugin_repo")

    logger.info("finding plugin in repository")
    try:
        ref = parse_plugin_reference(plugin_spec)
    except ValueError as e:
        raise click.BadParameter(f"invalid plugin reference: {plugin_spec!r}: {e}")

    from hcli.commands.plugin import repo_for_reference

    root_repo = repo_for_reference(ctx, ref)
    return RepositoryRoot(ref, root_repo, repo_name=ref.repo, upgrade=upgrade), ctx.obj.get("plugin_repo")


def _report_ambiguous(error: AmbiguousPluginReferenceError) -> None:
    console.print(f"[red]Error[/red]: plugin name '{error.name}' is ambiguous")
    console.print("Choose one of:")
    for candidate_ref in error.candidate_refs:
        console.print(f"  {format_qualified_plugin_reference(candidate_ref)}")


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option(
    "-e",
    "--editable",
    is_flag=True,
    default=False,
    help="Install a local plugin directory by symlinking it into $IDAUSR/plugins/. "
    "Edits to the source tree take effect immediately on the next plugin reload.",
)
@click.option(
    "--config",
    multiple=True,
    help="Configuration setting in key=value format. "
    "For component settings, prefix with the component name: component.key=value. "
    "Use true/false for booleans.",
)
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
@click.option(
    "-U",
    "--upgrade",
    is_flag=True,
    default=False,
    help="Upgrade the plugin when it is already installed, instead of failing. "
    "Repairs missing dependencies when the installed version is already up to date.",
)
def install_plugin(
    ctx,
    plugin: str,
    editable: bool,
    config: tuple[str, ...],
    dependency_config: tuple[str, ...],
    no_build_isolation: bool,
    upgrade: bool,
) -> None:
    """Install a plugin from a repository, local directory, local .zip file, or URL."""
    pip_options: PipOptions = ctx.obj.get("pip_options", PIP_OPTIONS_DEFAULT)
    if no_build_isolation:
        pip_options = dataclasses.replace(pip_options, no_build_isolation=True)
    check_environment = not ctx.obj.get("no_python_environment_check", False)
    try:
        sweep_trash()

        with rich.status.Status("collecting environment", console=stderr_console):
            current_ida_platform = find_current_ida_platform()
            current_ida_version = find_current_ida_version()

        root, plugin_repo = _build_root(ctx, plugin, editable, upgrade)

        try:
            with rich.status.Status("resolving dependencies", console=stderr_console):
                try:
                    operation = plan_plugin_operation(
                        [root],
                        plugin_repo=plugin_repo,
                        current_platform=current_ida_platform,
                        current_version=current_ida_version,
                    )
                except PluginVersionDowngradeError as e:
                    if not upgrade:
                        raise
                    logger.info(
                        "%s is installed at %s, newer than %s; keeping it", e.name, e.current_version, e.new_version
                    )
                    operation = plan_plugin_operation(
                        [InstalledRoot(e.name)],
                        plugin_repo=plugin_repo,
                        current_platform=current_ida_platform,
                        current_version=current_ida_version,
                    )
        except AmbiguousPluginReferenceError as e:
            _report_ambiguous(e)
            raise click.Abort()
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print("[red]Cannot connect to plugin repository - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()

        collect_configuration(operation, config=config, dependency_config=dependency_config)

        try:
            with rich.status.Status("installing plugin", console=stderr_console):
                result = operation.execute(pip_options=pip_options, check_environment=check_environment)
        except InstallExecutionError as e:
            logger.debug("error: %s", e, exc_info=True)
            report_install_failure(e)
            raise click.Abort()
        except KeyboardInterrupt as e:
            report_interrupted_install(e)
            raise click.Abort()

        report_install_result(result)

    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except FailedToDetectIDAVersion:
        explain_failed_to_detect_ida_version(console)
        raise click.Abort()

    except InstalledPluginNameConflictError as e:
        console.print(
            f"[red]Error[/red]: cannot install plugin "
            f"'{e.requested_name}@{e.requested_host}' because "
            f"'{e.installed_name}@{e.installed_host}' is already installed at {e.installed_path}"
        )
        console.print(f"Only one plugin with the bare name '{e.requested_name}' can be installed at a time.")
        console.print("Uninstall the existing plugin first, then install the other qualified plugin.")
        raise click.Abort()

    except click.Abort:
        raise

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
