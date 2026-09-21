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
from hcli.lib.ida.plugin.dependencies import install_dependencies
from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    InstalledPluginNameConflictError,
    PluginNotInstalledError,
)
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_metadata_from_plugin_directory,
    install_plugin_archive,
    install_plugin_directory_editable,
    pack_plugin_directory_to_zip,
    sweep_trash,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.reference import (
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    normalize_plugin_host,
    parse_plugin_reference,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.repo.github import fetch_github_release_zip_asset, parse_github_url
from hcli.lib.ida.plugin.settings import (
    has_setting_in_config,
    parse_setting_value,
    set_setting_for_metadata,
)
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
            # fetch from file system
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

            # Installing is a choice, not a survey: resolve in exactly one
            # repository -- the one named by the prefix, else the default.
            plugin_repo: BasePluginRepo = repo_for_reference(ctx, ref)
            plugin_repo_obj = plugin_repo

            # reconstruct the plugin_spec for repo lookup without the @host suffix
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
        # else: `metadata` was already populated from the source directory.

        # Same-name install conflict: another plugin with the same bare name is already
        # installed from a different repository. The install layout is
        # $IDAUSR/plugins/<name>, so only one same-name plugin can be installed at a time.
        # Use the archive metadata host (not the download URL) as the long-term identity
        # because GitHub redirects can cause the fetch URL and the metadata host to differ.
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

        # `--upgrade` turns an already-installed plugin from an error into an
        # in-place upgrade, so callers that just want the plugin present (e.g.
        # `hcli mcp install`) can be run repeatedly. Editable installs already
        # replace whatever is at the destination, so there's nothing to do
        # there. Nothing newer to install is success, not an error.
        is_upgrade = False
        if upgrade and installed is not None and not editable:
            if parse_plugin_version(metadata.plugin.version) <= parse_plugin_version(installed.version):
                console.print(
                    f"[green]Already installed[/green] plugin: [blue]{plugin_name}[/blue]=={installed.version}"
                )
                return
            is_upgrade = True

        source = source_dir if editable else buf
        assert source is not None
        component_metadatas = validate_components_for_install(metadata, source, plugin_name, is_upgrade=is_upgrade)

        root_cli_config, component_cli_configs = _partition_config_items(
            config,
            component_metadatas,
        )

        if metadata.plugin.settings or root_cli_config:
            for key, value_str in root_cli_config.items():
                descr = metadata.plugin.get_setting(key)
                parsed_value = parse_setting_value(descr, value_str)
                descr.validate_value(parsed_value)

        for comp_name, comp_config in component_cli_configs.items():
            comp_meta = component_metadatas[comp_name]
            for key, value_str in comp_config.items():
                descr = comp_meta.plugin.get_setting(key)
                parsed_value = parse_setting_value(descr, value_str)
                descr.validate_value(parsed_value)

        if editable:
            install_plugin_directory_editable(source_dir, plugin_name, install_ctx)
        else:
            assert buf is not None
            if is_upgrade:
                write_archive = upgrade_plugin_archive
                status_text = "upgrading plugin"
            else:
                write_archive = install_plugin_archive
                status_text = "installing plugin"

            from hcli.commands.plugin import resolve_bundle_install_context

            with (
                resolve_bundle_install_context(plugin_repo_obj, install_ctx, plugin_name) as effective_ctx,
                rich.status.Status(status_text, console=stderr_console),
            ):
                write_archive(buf, plugin_name, effective_ctx)

        try:
            _apply_plugin_settings(
                metadata,
                plugin_name,
                root_cli_config,
            )

            for comp_name, comp_meta in component_metadatas.items():
                if not comp_meta.plugin.settings and comp_name not in component_cli_configs:
                    continue
                _apply_component_settings(
                    comp_meta,
                    comp_name,
                    component_cli_configs.get(comp_name, {}),
                )

        except Exception:
            if is_upgrade:
                # The upgrade itself succeeded; the plugin the user already had
                # is now at the new version. Uninstalling it here would be a
                # worse outcome than leaving the settings unconfigured.
                logger.warning("failed to configure settings")
                raise
            logger.warning("failed to configure settings, removing installation...")
            with rich.status.Status("rolling back installation", console=stderr_console):
                uninstall_plugin(plugin_name)
            raise

        suffix = " [yellow](editable)[/yellow]" if editable else ""
        verb = "Upgraded" if is_upgrade else "Installed"
        console.print(f"[green]{verb}[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}{suffix}")

        if metadata.plugin.dependencies:
            try:
                # Resolve deps across all configured repos, not just the source repo.
                dep_repo = ctx.obj.get("plugin_repos") or plugin_repo_obj
                _handle_install_dependencies(
                    metadata=metadata,
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


def _apply_plugin_settings(
    metadata: IDAMetadataDescriptor,
    plugin_name: str,
    cli_config: dict[str, str],
) -> None:
    """Apply root plugin settings from --config or interactive prompt."""
    if not metadata.plugin.settings and not cli_config:
        return

    if cli_config:
        for key, value_str in cli_config.items():
            descr = metadata.plugin.get_setting(key)
            parsed_value = parse_setting_value(descr, value_str)
            descr.validate_value(parsed_value)
            if descr.default != parsed_value:
                set_setting_for_metadata(plugin_name, key, parsed_value, metadata)
    elif metadata.plugin.settings:
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
        else:
            answers = {}

        for key, answer in answers.items():
            descr = metadata.plugin.get_setting(key)
            if descr.default == answer:
                continue
            set_setting_for_metadata(plugin_name, descr.key, answer, metadata)


def _apply_component_settings(
    comp_metadata: IDAMetadataDescriptor,
    comp_name: str,
    cli_config: dict[str, str],
) -> None:
    """Apply component settings from --config or interactive prompt."""
    if not comp_metadata.plugin.settings and not cli_config:
        return

    if cli_config:
        for key, value_str in cli_config.items():
            descr = comp_metadata.plugin.get_setting(key)
            parsed_value = parse_setting_value(descr, value_str)
            descr.validate_value(parsed_value)
            if descr.default != parsed_value:
                set_setting_for_metadata(comp_name, key, parsed_value, comp_metadata)
    elif comp_metadata.plugin.settings:
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
        else:
            answers = {}

        for key, answer in answers.items():
            descr = comp_metadata.plugin.get_setting(key)
            if descr.default == answer:
                continue
            set_setting_for_metadata(comp_name, key, answer, comp_metadata)


def _handle_install_dependencies(
    *,
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo | None,
    install_ctx: InstallContext,
) -> None:
    if plugin_repo is None:
        console.print(
            f"[yellow]Warning[/yellow]: {metadata.plugin.name} declares dependencies "
            f"but they cannot be auto-installed from a local source."
        )
        for dep in metadata.plugin.dependencies:
            console.print(f"  {dep}")
        console.print("Install them manually from a plugin repository.")
        return

    console.print(f"Installing dependencies for [blue]{metadata.plugin.name}[/blue]...")
    with rich.status.Status("installing dependencies", console=stderr_console):
        result = install_dependencies(
            metadata=metadata,
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
