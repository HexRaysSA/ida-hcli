"""Plugin install command."""

from __future__ import annotations

import logging
from pathlib import Path

import questionary
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_metadatas_with_paths_from_plugin_archive,
    split_plugin_version_spec,
)
from hcli.lib.ida.plugin.install import install_plugin_archive, uninstall_plugin
from hcli.lib.ida.plugin.repo import BasePluginRepo, fetch_plugin_archive
from hcli.lib.ida.plugin.settings import has_plugin_setting, set_plugin_setting

logger = logging.getLogger(__name__)


@click.command()
@click.pass_context
@click.argument("plugin")
@click.option("--config", multiple=True, help="Configuration setting in key=value format")
def install_plugin(ctx, plugin: str, config: tuple[str, ...]) -> None:
    plugin_spec = plugin
    try:
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
            buf = plugin_repo.fetch_compatible_plugin_from_spec(plugin_spec, current_ida_platform, current_ida_version)

        metadata = get_metadata_from_plugin_archive(buf, plugin_name)

        if metadata.plugin.settings:
            for config_item in config:
                if "=" not in config_item:
                    raise ValueError(f"invalid config format: {config_item}, expected key=value")
                key, value = config_item.split("=", 1)
                descr = metadata.plugin.get_setting(key)
                descr.validate_value(value)

        install_plugin_archive(buf, plugin_name)

        try:
            if metadata.plugin.settings:
                cli_config: dict[str, str] = {}
                for config_item in config:
                    if "=" not in config_item:
                        raise ValueError(f"invalid config format: {config_item}, expected key=value")
                    key, value = config_item.split("=", 1)
                    cli_config[key] = value

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

                        def make_validator(s):
                            def validate_func(value: str):
                                if not s.required and not value:
                                    return True
                                if s.required and not value:
                                    return "This field is required"
                                try:
                                    s.validate_value(value)
                                    return True
                                except ValueError as e:
                                    return str(e)

                            return validate_func

                        question = questionary.text(
                            # TODO: descr.documentation
                            message=setting.name,
                            default=setting.default or "",
                            validate=make_validator(setting),
                        )
                        questions[setting.key] = question

                    answers = questionary.form(**questions).ask()

                    for key, answer in answers.items():
                        descr = metadata.plugin.get_setting(key)
                        if descr.default == answer:
                            # don't save default values into the settings store
                            # so that we can potentially layer these in the future.
                            # if we do store them, we don't know if they're still the default value
                            # or set by the user.
                            continue

                        set_plugin_setting(metadata.plugin.name, descr.key, answer)

        except Exception as e:
            logger.warning("failed to configure settings, removing installation...")
            uninstall_plugin(plugin_name)
            raise e

        console.print(f"[green]Installed[/green] plugin: [blue]{plugin_name}[/blue]=={metadata.plugin.version}")
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
