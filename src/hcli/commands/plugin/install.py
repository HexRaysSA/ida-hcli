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
    get_ida_config,
)
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadata_from_plugin_archive,
    parse_plugin_version,
)
from hcli.lib.ida.plugin.components import find_root_manifest_in_archive, validate_components_for_install
from hcli.lib.ida.plugin.context import IDAEnvironment, InstallContext, InstallOptions
from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    InstalledPluginNameConflictError,
    PluginNotInstalledError,
)
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_metadata_from_plugin_directory,
    orchestrate_install,
    orchestrate_upgrade,
    pack_plugin_directory_to_zip,
    sweep_trash,
)
from hcli.lib.ida.plugin.reference import (
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    normalize_plugin_host,
    parse_plugin_reference,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.repo.github import fetch_github_release_zip_asset, parse_github_url
from hcli.lib.ida.plugin.result import InstallResult, InstallStatus
from hcli.lib.ida.plugin.settings import has_setting_in_config, parse_setting_value
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

from ._prompt import prompt_plugin_settings

logger = logging.getLogger(__name__)


def _partition_config_items(
    config: tuple[str, ...],
    component_metadatas: dict[str, IDAMetadataDescriptor],
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Split --config items into root vs. component buckets.

    Returns (root_config, component_configs) where:
      root_config = {"key": "raw_value_str", ...}
      component_configs = {"component-name": {"key": "raw_value_str", ...}, ...}
    """
    root_config: dict[str, str] = {}
    component_configs: dict[str, dict[str, str]] = {}

    for item in config:
        if "=" not in item:
            raise ValueError(f"invalid config format: {item}, expected key=value")
        raw_key, value_str = item.split("=", 1)

        if "." in raw_key:
            prefix, suffix = raw_key.split(".", 1)
            if prefix in component_metadatas:
                component_configs.setdefault(prefix, {})[suffix] = value_str
                continue

        root_config[raw_key] = value_str

    return root_config, component_configs


def _resolve_plugin_name_from_archive(buf: bytes) -> str:
    _, meta = find_root_manifest_in_archive(buf)
    return meta.plugin.name


def _resolve_interactive_settings(
    metadata: IDAMetadataDescriptor,
    plugin_name: str,
    cli_config: dict[str, str],
) -> dict[str, str]:
    """Resolve settings for a plugin, prompting interactively if needed.

    Returns a dict of key -> raw-string-value ready for the orchestrator.
    """
    if cli_config:
        for key, value_str in cli_config.items():
            descr = metadata.plugin.get_setting(key)
            parsed_value = parse_setting_value(descr, value_str)
            descr.validate_value(parsed_value)
        return cli_config

    if not metadata.plugin.settings:
        return {}

    needed_settings = [
        s
        for s in metadata.plugin.settings
        if not has_setting_in_config(plugin_name, s.key) and s.required and s.default is None
    ]

    if needed_settings and not console.is_interactive:
        setting_names = ", ".join(f"--config {s.key}=<value>" for s in needed_settings)
        raise ValueError(
            f"plugin requires configuration but console is not interactive. "
            f"Please provide settings via command line: {setting_names}"
        )

    if console.is_interactive:
        existing_config = get_ida_config()
        existing_values: dict[str, str | bool] = {}
        if plugin_name in existing_config.plugins:
            existing_values = dict(existing_config.plugins[plugin_name].settings)

        answers = prompt_plugin_settings(metadata.plugin.settings, existing_values)
        if answers is None:
            raise click.Abort()

        result: dict[str, str] = {}
        for key, answer in answers.items():
            result[key] = str(answer).lower() if isinstance(answer, bool) else str(answer)
        return result

    return {}


def _resolve_interactive_component_settings(
    comp_metadata: IDAMetadataDescriptor,
    comp_name: str,
    cli_config: dict[str, str],
) -> dict[str, str]:
    """Resolve settings for a component, prompting interactively if needed."""
    if cli_config:
        for key, value_str in cli_config.items():
            descr = comp_metadata.plugin.get_setting(key)
            parsed_value = parse_setting_value(descr, value_str)
            descr.validate_value(parsed_value)
        return cli_config

    if not comp_metadata.plugin.settings:
        return {}

    needed_settings = [
        s
        for s in comp_metadata.plugin.settings
        if not has_setting_in_config(comp_name, s.key) and s.required and s.default is None
    ]

    if needed_settings and not console.is_interactive:
        setting_names = ", ".join(f"--config {comp_name}.{s.key}=<value>" for s in needed_settings)
        raise ValueError(
            f"component '{comp_name}' requires configuration but console is not interactive. "
            f"Please provide settings via command line: {setting_names}"
        )

    if console.is_interactive:
        console.print(f"\nconfigure component [blue]{comp_name}[/blue]:")
        existing_config = get_ida_config()
        existing_values: dict[str, str | bool] = {}
        if comp_name in existing_config.plugins:
            existing_values = dict(existing_config.plugins[comp_name].settings)

        answers = prompt_plugin_settings(comp_metadata.plugin.settings, existing_values)
        if answers is None:
            raise click.Abort()

        result: dict[str, str] = {}
        for key, answer in answers.items():
            result[key] = str(answer).lower() if isinstance(answer, bool) else str(answer)
        return result

    return {}


def render_install_result(result: InstallResult, *, editable: bool = False, is_upgrade: bool = False) -> None:
    """Walk an InstallResult tree and print status lines to the console."""
    if result.status == InstallStatus.SUCCESS:
        suffix = " [yellow](editable)[/yellow]" if editable else ""
        verb = "Upgraded" if is_upgrade else "Installed"
        console.print(f"[green]{verb}[/green] plugin: [blue]{result.plugin}[/blue]=={result.version}{suffix}")
    elif result.status == InstallStatus.ALREADY_INSTALLED:
        console.print(f"[green]Already installed[/green] plugin: [blue]{result.plugin}[/blue]=={result.version}")
    elif result.status == InstallStatus.FAILED:
        console.print(f"[red]Error[/red]: {result.reason}")
        raise click.Abort()

    for dep in result.dependencies:
        _render_dependency_result(dep)


def _render_dependency_result(dep: InstallResult) -> None:
    if dep.status == InstallStatus.SUCCESS:
        if dep.reason == "upgraded":
            console.print(f"  [green]Upgraded[/green] dependency: [blue]{dep.plugin}[/blue]")
        else:
            console.print(f"  [green]Installed[/green] dependency: [blue]{dep.plugin}[/blue]")
    elif dep.status == InstallStatus.ALREADY_INSTALLED:
        if dep.reason and "dropped" in dep.reason:
            console.print(f"  [yellow]Note[/yellow]: {dep.plugin}: {dep.reason}")
        else:
            console.print(f"  [dim]Skipped[/dim] dependency: {dep.plugin} (already installed)")
    elif dep.status == InstallStatus.FAILED:
        if dep.reason and "cannot auto-install" in dep.reason:
            console.print(f"  [yellow]Warning[/yellow]: {dep.plugin}: {dep.reason}")
        else:
            console.print(f"  [red]Failed[/red] dependency: {dep.plugin}: {dep.reason}")


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
    "Does nothing when the installed version is already up to date.",
)
def install_plugin(
    ctx, plugin: str, editable: bool, config: tuple[str, ...], no_build_isolation: bool, upgrade: bool
) -> None:
    """Install a plugin from a repository, local directory, local .zip file, or URL."""
    pip_options: PipOptions = ctx.obj.get("pip_options", PIP_OPTIONS_DEFAULT)
    if no_build_isolation:
        pip_options = dataclasses.replace(pip_options, no_build_isolation=True)
    check_environment = not ctx.obj.get("no_python_environment_check", False)
    plugin_repo_obj = ctx.obj.get("plugin_repo")
    plugin_spec = plugin
    try:
        sweep_trash()

        with rich.status.Status("collecting environment", console=stderr_console):
            ida_env = IDAEnvironment.from_current()

        install_opts = InstallOptions(pip_options=pip_options, check_environment=check_environment)
        install_ctx = InstallContext(env=ida_env, options=install_opts)

        # --- Source resolution ---

        # Editable install: skip the archive pipeline entirely. Read metadata
        # straight from the source directory and symlink it into place.
        if editable:
            source_dir = Path(plugin_spec).expanduser()
            if not source_dir.exists():
                raise click.BadParameter(f"path does not exist: {plugin_spec}")
            if not source_dir.is_dir():
                raise click.BadParameter(
                    f"--editable requires a directory containing ida-plugin.json, got: {plugin_spec}"
                )
            source_dir = source_dir.resolve()
            try:
                metadata = get_metadata_from_plugin_directory(source_dir)
            except ValueError as e:
                raise click.BadParameter(str(e))
            plugin_name = metadata.plugin.name
            buf = None  # sentinel: editable; no archive bytes

        elif Path(plugin_spec).expanduser().is_dir() and (Path(plugin_spec).expanduser() / "ida-plugin.json").is_file():
            # Local non-editable install: pack the directory into an in-memory
            # zip and run it through the same archive pipeline used for zip /
            # URL / repo installs. The dir must contain ida-plugin.json -- any
            # bare directory name without metadata falls through so it can be
            # resolved as a repository plugin reference instead.
            logger.info("installing from the local file system (directory)")
            source_dir = Path(plugin_spec).expanduser().resolve()
            buf = pack_plugin_directory_to_zip(source_dir)
            plugin_name = _resolve_plugin_name_from_archive(buf)

        elif Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            logger.info("installing from the local file system")
            buf = Path(plugin_spec).read_bytes()
            plugin_name = _resolve_plugin_name_from_archive(buf)

        elif plugin_spec.startswith("file://"):
            logger.info("installing from the local file system")
            buf = fetch_plugin_archive(plugin_spec)
            plugin_name = _resolve_plugin_name_from_archive(buf)

        elif is_github_direct_install_url(plugin_spec):
            logger.info("installing from GitHub repository")
            try:
                owner, repo, tag = parse_github_url(plugin_spec)
                tag_info = f"@{tag}" if tag else " (latest release)"
                with rich.status.Status(
                    f"fetching plugin from GitHub: {owner}/{repo}{tag_info}", console=stderr_console
                ):
                    buf = fetch_github_release_zip_asset(owner, repo, tag)
            except (httpx.ConnectError, httpx.TimeoutException):
                console.print("[red]Cannot connect to GitHub - network unavailable.[/red]")
                console.print("Please check your internet connection.")
                raise click.Abort()
            plugin_name = _resolve_plugin_name_from_archive(buf)

        elif plugin_spec.startswith("https://"):
            logger.info("installing from HTTP URL")
            try:
                with rich.status.Status("fetching plugin", console=stderr_console):
                    buf = fetch_plugin_archive(plugin_spec)
            except (httpx.ConnectError, httpx.TimeoutException):
                console.print(f"[red]Cannot connect to {plugin_spec} - network unavailable.[/red]")
                console.print("Please check your internet connection.")
                raise click.Abort()
            plugin_name = _resolve_plugin_name_from_archive(buf)

        else:
            logger.info("finding plugin in repository")
            try:
                ref = parse_plugin_reference(plugin_spec)
            except ValueError as e:
                raise click.BadParameter(f"invalid plugin reference: {plugin_spec!r}: {e}")

            from hcli.commands.plugin import repo_for_reference

            # Resolve in exactly one repository -- the one named by the
            # prefix, else the default.
            plugin_repo: BasePluginRepo = repo_for_reference(ctx, ref)
            plugin_repo_obj = plugin_repo

            bare_spec = ref.name + ref.version_spec
            try:
                with rich.status.Status("fetching plugin", console=stderr_console):
                    plugin_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
                        bare_spec, ida_env.platform, ida_env.ida_version, host=ref.host
                    )
            except AmbiguousPluginReferenceError as e:
                if ref.version_spec and not e.version_spec:
                    e = AmbiguousPluginReferenceError(e.name, e.candidates, ref.version_spec)
                console.print(f"[red]Error[/red]: plugin name '{e.name}' is ambiguous")
                console.print("Choose one of:")
                for candidate_ref in e.candidate_refs:
                    console.print(f"  {format_qualified_plugin_reference(candidate_ref)}")
                raise click.Abort()

        if not editable:
            assert buf is not None  # invariant: only the editable branch leaves buf as None
            _, metadata = get_metadata_from_plugin_archive(buf, plugin_name)
        # else: metadata was already populated from the source directory.

        # --- Name conflict check ---
        # The install layout is $IDAUSR/plugins/<name>, so only one same-name
        # plugin can be installed at a time. Use the archive metadata host (not
        # the download URL) as the long-term identity because GitHub redirects
        # can cause the fetch URL and the metadata host to differ.
        try:
            installed = find_installed_plugin(plugin_name)
        except PluginNotInstalledError:
            installed = None

        if installed is not None and normalize_plugin_host(installed.host) != normalize_plugin_host(
            metadata.plugin.host
        ):
            raise InstalledPluginNameConflictError(
                requested_name=plugin_name,
                requested_host=metadata.plugin.host,
                installed_name=installed.name,
                installed_host=installed.host,
                installed_path=installed.path,
            )

        # --- Upgrade check ---
        # --upgrade turns an already-installed plugin from an error into an
        # in-place upgrade, so callers that just want the plugin present (e.g.
        # `hcli mcp install`) can be run repeatedly. Editable installs already
        # replace whatever is at the destination, so there's nothing to do
        # there.
        is_upgrade = False
        if upgrade and installed is not None and not editable:
            if parse_plugin_version(metadata.plugin.version) <= parse_plugin_version(installed.version):
                console.print(
                    f"[green]Already installed[/green] plugin: [blue]{plugin_name}[/blue]=={installed.version}"
                )
                return
            is_upgrade = True

        # --- Settings resolution (CLI responsibility) ---

        source = source_dir if editable else buf
        assert source is not None
        component_metadatas = validate_components_for_install(metadata, source, plugin_name, is_upgrade=is_upgrade)

        root_cli_config, component_cli_configs = _partition_config_items(config, component_metadatas)

        root_settings = _resolve_interactive_settings(metadata, plugin_name, root_cli_config)

        component_settings: dict[str, dict[str, str]] = {}
        for comp_name, comp_meta in component_metadatas.items():
            comp_cli = component_cli_configs.get(comp_name, {})
            if not comp_meta.plugin.settings and not comp_cli:
                continue
            component_settings[comp_name] = _resolve_interactive_component_settings(comp_meta, comp_name, comp_cli)

        # --- Orchestrate ---

        from hcli.commands.plugin import resolve_bundle_install_context

        dep_repo = ctx.obj.get("plugin_repos") or plugin_repo_obj

        with resolve_bundle_install_context(plugin_repo_obj, install_ctx, plugin_name) as effective_ctx:
            if is_upgrade:
                assert buf is not None
                with rich.status.Status("upgrading plugin", console=stderr_console):
                    result = orchestrate_upgrade(
                        zip_data=buf,
                        plugin_name=plugin_name,
                        metadata=metadata,
                        ctx=effective_ctx,
                        settings=root_settings or None,
                        component_settings=component_settings or None,
                        plugin_repo=dep_repo,
                    )
            else:
                with rich.status.Status(
                    "installing plugin" if not editable else "installing plugin (editable)",
                    console=stderr_console,
                ):
                    result = orchestrate_install(
                        source=source,
                        plugin_name=plugin_name,
                        metadata=metadata,
                        ctx=effective_ctx,
                        settings=root_settings or None,
                        component_settings=component_settings or None,
                        plugin_repo=dep_repo,
                        editable=editable,
                    )

        render_install_result(result, editable=editable, is_upgrade=is_upgrade)

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
        # Already reported by whoever raised it; re-wrapping would print a
        # second, empty "Error:" line.
        raise

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
