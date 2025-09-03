"""Plugin search command."""

from __future__ import annotations

import logging
from typing import Optional

import rich_click as click

from hcli.lib.commands import async_command
from hcli.lib.console import console
from hcli.lib.ida.plugin import ALL_PLATFORMS

logger = logging.getLogger(__name__)


@click.command()
@click.argument("query", required=False)
@click.pass_context
@async_command
async def search_plugins(ctx, query: Optional[str] = None) -> None:
    try:
        plugin_repo = ctx.obj["plugin_repo"]

        plugins = plugin_repo.get_plugins()

        for plugin in sorted(plugins, key=lambda p: p.name):
            if query and query.lower() not in plugin.name.lower():
                continue

            console.print(f"[blue]{plugin.name}[/blue]")

            for version, locations in plugin.locations_by_version.items():
                console.print(f"  {version}:")
                for location in locations:
                    ida_versions_str = location.ida_versions if location.ida_versions != ">=0" else "all"
                    locations_str = ", ".join(location.platforms) if location.platforms != ALL_PLATFORMS else "all"
                    console.print(
                        f"    IDA: [yellow]{ida_versions_str}[/yellow], platforms: [yellow]{locations_str}[/yellow]"
                    )
                    console.print(f"    {location.url}")
                    console.print("")

        if not plugins:
            console.print("[grey69]No plugins found[/grey69]")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
        raise click.Abort()
