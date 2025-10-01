"""Plugin configuration management commands."""

from __future__ import annotations

import json
import logging
import sys

import rich.table
import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import get_ida_config
from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, get_plugin_directory
from hcli.lib.ida.plugin.settings import del_plugin_setting, get_plugin_setting, parse_setting_value, set_plugin_setting

logger = logging.getLogger(__name__)


@click.group()
@click.argument("plugin_name")
@click.pass_context
def config(ctx, plugin_name: str) -> None:
    """Manage plugin configuration settings."""
    ctx.ensure_object(dict)
    ctx.obj["config_plugin_name"] = plugin_name


@config.command()
@click.argument("key")
@click.pass_context
def get(ctx, key: str) -> None:
    """Get a plugin configuration setting."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        value = get_plugin_setting(plugin_name, key)
        if isinstance(value, bool):
            console.print("true" if value else "false")
        else:
            console.print(value)
    except KeyError as e:
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()


@config.command()
@click.argument("key")
@click.argument("value")
@click.pass_context
def set(ctx, key: str, value: str) -> None:
    """Set a plugin configuration setting."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        plugin_path = get_plugin_directory(plugin_name)
        metadata = get_metadata_from_plugin_directory(plugin_path)
        descr = metadata.plugin.get_setting(key)
        parsed_value = parse_setting_value(descr, value)
        set_plugin_setting(plugin_name, key, parsed_value)
        console.print(f"[green]Set[/green] {plugin_name}.{key}")
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()


@config.command(name="del")
@click.argument("key")
@click.pass_context
def del_(ctx, key: str) -> None:
    """Delete a plugin configuration setting."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        del_plugin_setting(plugin_name, key)
        console.print(f"[green]Deleted[/green] {plugin_name}.{key}")
    except KeyError as e:
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()


@config.command()
@click.pass_context
def list(ctx) -> None:
    """List all configuration settings for a plugin."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        plugin_path = get_plugin_directory(plugin_name)
        metadata = get_metadata_from_plugin_directory(plugin_path)

        if not metadata.plugin.settings:
            console.print(f"[grey69]No settings defined for {plugin_name}[/grey69]")
            return

        table = rich.table.Table(show_header=True, box=None)
        table.add_column("Key", style="blue")
        table.add_column("Value", style="default")
        table.add_column("Description", style="grey69")

        for setting in metadata.plugin.settings:
            try:
                value = get_plugin_setting(plugin_name, setting.key)
                value_str = "true" if value is True else ("false" if value is False else str(value))
                if setting.default is not None and value == setting.default:
                    value_str = f"{value_str} [grey69](default)[/grey69]"
            except KeyError:
                value_str = "[grey69]<not set>[/grey69]"

            description = setting.documentation or ""
            if setting.choices:
                choices_str = ", ".join(setting.choices)
                description = (
                    f"{description}\n[grey69]Choices: {choices_str}[/grey69]"
                    if description
                    else f"[grey69]Choices: {choices_str}[/grey69]"
                )

            table.add_row(setting.key, value_str, description)

        console.print(table)

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()


@config.command()
@click.pass_context
def export(ctx) -> None:
    """Export plugin configuration settings as JSON."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        config = get_ida_config()
        if plugin_name not in config.plugins:
            console.print("{}")
            return

        plugin_config = config.plugins[plugin_name]
        console.print(json.dumps(plugin_config.settings, indent=2))

    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()


@config.command(name="import")
@click.argument("json_input", required=False)
@click.pass_context
def import_(ctx, json_input: str | None) -> None:
    """Import plugin configuration settings from JSON."""
    plugin_name = ctx.obj["config_plugin_name"]
    try:
        if json_input:
            data = json.loads(json_input)
        else:
            data = json.load(sys.stdin)

        if not isinstance(data, dict):
            raise ValueError("JSON input must be an object/dict")

        for key, value in data.items():
            set_plugin_setting(plugin_name, key, value)

        console.print(f"[green]Imported {len(data)} settings for {plugin_name}[/green]")

    except json.JSONDecodeError as e:
        console.print(f"[red]Error[/red]: Invalid JSON: {e}")
        raise click.Abort()
    except Exception as e:
        logger.debug("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
