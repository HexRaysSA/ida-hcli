"""Plugin install command."""

from __future__ import annotations

import logging
from pathlib import Path

import questionary
import rich.status
import rich_click as click

from hcli.lib.console import console, stderr_console
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    explain_missing_current_installation_directory,
    find_current_ida_platform,
    find_current_ida_version,
)
from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
    split_plugin_version_spec,
)
from hcli.lib.ida.plugin.install import install_plugin_archive, uninstall_plugin
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.settings import has_plugin_setting, parse_setting_value, set_plugin_setting

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option("--config", multiple=True, help="Configuration setting in key=value format (use true/false for booleans)")
def install_plugin(ctx, plugin: str, config: tuple[str, ...]) -> None:
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

        elif plugin_spec.startswith("https://"):
            logger.info("installing from HTTP URL")
            with rich.status.Status("fetching plugin", console=stderr_console):
                buf = fetch_plugin_archive(plugin_spec)
            items = list(get_metadatas_with_paths_from_plugin_archive(buf))
            if len(items) != 1:
                raise ValueError("plugin archive must contain a single plugin for HTTP URL installation")
            plugin_name = items[0][1].plugin.name

        else:
            logger.info("finding plugin in repository")
            plugin_name, _ = split_plugin_version_spec(plugin_spec)
            logger.debug("plugin name: %s", plugin_name)

            plugin_repo: BasePluginRepo = ctx.obj["plugin_repo"]
            with rich.status.Status("fetching plugin", console=stderr_console):
                buf = plugin_repo.fetch_compatible_plugin_from_spec(
                    plugin_spec, current_ida_platform, current_ida_version
                )

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        if metadata.plugin.settings:
            for config_item in config:
                if "=" not in config_item:
                    raise ValueError(f"invalid config format: {config_item}, expected key=value")
                key, value_str = config_item.split("=", 1)
                descr = metadata.plugin.get_setting(key)
                parsed_value = parse_setting_value(descr, value_str)
                descr.validate_value(parsed_value)

        with rich.status.Status("installing plugin", console=stderr_console):
            install_plugin_archive(buf, plugin_name)

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

        except Exception as e:
            logger.warning("failed to configure settings, removing installation...")
            with rich.status.Status("rolling back installation", console=stderr_console):
                uninstall_plugin(plugin_name)
            raise e

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
    except MissingCurrentInstallationDirectory:
        explain_missing_current_installation_directory(console)
        raise click.Abort()

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
