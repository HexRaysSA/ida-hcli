"""Plugin install command."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import questionary
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
from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
)
from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    InstalledPluginNameConflictError,
    PluginNotInstalledError,
)
from hcli.lib.ida.plugin.install import find_installed_plugin, install_plugin_archive, uninstall_plugin
from hcli.lib.ida.plugin.reference import (
    format_qualified_plugin_reference,
    is_github_direct_install_url,
    normalize_plugin_host,
    parse_plugin_reference,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.repo.github import fetch_github_release_zip_asset, parse_github_url
from hcli.lib.ida.plugin.settings import has_plugin_setting, parse_setting_value, set_plugin_setting

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option("--config", multiple=True, help="Configuration setting in key=value format (use true/false for booleans)")
@click.option(
    "--no-build-isolation",
    is_flag=True,
    default=False,
    help="Disable pip build isolation when installing Python dependencies",
)
def install_plugin(ctx, plugin: str, config: tuple[str, ...], no_build_isolation: bool) -> None:
    """Install a plugin from repository, local .zip file, or URL."""
    plugin_spec = plugin
    try:
        with rich.status.Status("collecting environment", console=stderr_console):
            current_ida_platform = find_current_ida_platform()
            current_ida_version = find_current_ida_version()

        if Path(plugin_spec).exists() and plugin_spec.endswith(".zip"):
            logger.info("installing from the local file system")
            buf = Path(plugin_spec).read_bytes()
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].plugin.name

        elif plugin_spec.startswith("file://"):
            logger.info("installing from the local file system")
            # fetch from file system
            buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for local file system installation")
            plugin_name = items[0][1].plugin.name

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
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for GitHub installation")
            plugin_name = items[0][1].plugin.name

        elif plugin_spec.startswith("https://"):
            logger.info("installing from HTTP URL")
            try:
                with rich.status.Status("fetching plugin", console=stderr_console):
                    buf = fetch_plugin_archive(plugin_spec)
            except (httpx.ConnectError, httpx.TimeoutException):
                console.print(f"[red]Cannot connect to {plugin_spec} - network unavailable.[/red]")
                console.print("Please check your internet connection.")
                raise click.Abort()
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for HTTP URL installation")
            plugin_name = items[0][1].plugin.name

        else:
            logger.info("finding plugin in repository")
            plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
            try:
                ref = parse_plugin_reference(plugin_spec)
            except ValueError as e:
                raise click.BadParameter(f"invalid plugin reference: {plugin_spec!r}: {e}")

            # reconstruct the plugin_spec for repo lookup without the @host suffix
            bare_spec = ref.name + ref.version_spec
            try:
                with rich.status.Status("fetching plugin", console=stderr_console):
                    plugin_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
                        bare_spec, current_ida_platform, current_ida_version, host=ref.host
                    )
            except AmbiguousPluginReferenceError as e:
                if ref.version_spec and not e.version_spec:
                    e = AmbiguousPluginReferenceError(e.name, e.candidates, ref.version_spec)
                console.print(f"[red]Error[/red]: plugin name '{e.name}' is ambiguous")
                console.print("Choose one of:")
                for candidate_ref in e.candidate_refs:
                    console.print(f"  {format_qualified_plugin_reference(candidate_ref)}")
                raise click.Abort()

        _, metadata = get_metadata_from_plugin_archive(buf, plugin_name)

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

        if metadata.plugin.settings:
            for config_item in config:
                if "=" not in config_item:
                    raise ValueError(f"invalid config format: {config_item}, expected key=value")
                key, value_str = config_item.split("=", 1)
                descr = metadata.plugin.get_setting(key)
                parsed_value = parse_setting_value(descr, value_str)
                descr.validate_value(parsed_value)

        with rich.status.Status("installing plugin", console=stderr_console):
            install_plugin_archive(buf, plugin_name, no_build_isolation=no_build_isolation)

        try:
            if metadata.plugin.settings:
                cli_config: dict[str, str | bool] = {}
                for config_item in config:
                    if "=" not in config_item:
                        raise ValueError(f"invalid config format: {config_item}, expected key=value")
                    key, value_str = config_item.split("=", 1)
                    descr = metadata.plugin.get_setting(key)
                    parsed_value = parse_setting_value(descr, value_str)
                    cli_config[key] = parsed_value

                if cli_config:
                    for key, value in cli_config.items():
                        descr = metadata.plugin.get_setting(key)
                        descr.validate_value(value)
                        if descr.default != value:
                            set_plugin_setting(metadata.plugin.name, key, value)
                else:
                    needed_settings = [
                        s
                        for s in metadata.plugin.settings
                        if not has_plugin_setting(plugin_name, s.key) and (s.required and not s.default)
                    ]

                    if needed_settings and not console.is_interactive:
                        setting_names = ", ".join(f"--config {s.key}=<value>" for s in needed_settings)
                        raise ValueError(
                            f"plugin requires configuration but console is not interactive. Please provide settings via command line: {setting_names}"
                        )

                    console.print(f"configure {len(metadata.plugin.settings)} settings:")

                    questions: dict[str, questionary.Question] = {}
                    for setting in metadata.plugin.settings:
                        if has_plugin_setting(plugin_name, setting.key):
                            continue

                        if not setting.prompt:
                            continue

                        if setting.type == "boolean":
                            default_bool = setting.default if isinstance(setting.default, bool) else False
                            question = questionary.confirm(
                                message=setting.name,
                                default=default_bool,
                            )
                        elif setting.choices:
                            default_str = str(setting.default) if setting.default is not None else setting.choices[0]
                            question = questionary.select(
                                message=setting.name,
                                choices=setting.choices,
                                default=default_str,
                            )
                        else:

                            def make_validator(s):
                                def validate_func(value: str):
                                    if not s.required and not value:
                                        return True
                                    if s.required and not value:
                                        return "This field is required"
                                    try:
                                        parsed = parse_setting_value(s, value)
                                        s.validate_value(parsed)
                                        return True
                                    except ValueError as e:
                                        return str(e)

                                return validate_func

                            default_str = str(setting.default) if setting.default is not None else ""
                            question = questionary.text(
                                # TODO: descr.documentation
                                message=setting.name,
                                default=default_str,
                                validate=make_validator(setting),
                            )
                        questions[setting.key] = question

                    answers = questionary.form(**questions).ask()

                    for key, answer in answers.items():
                        descr = metadata.plugin.get_setting(key)
                        if descr.default == answer:
                            continue

                        set_plugin_setting(metadata.plugin.name, descr.key, answer)

        except Exception:
            logger.warning("failed to configure settings, removing installation...")
            with rich.status.Status("rolling back installation", console=stderr_console):
                uninstall_plugin(plugin_name)
            raise

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
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

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
