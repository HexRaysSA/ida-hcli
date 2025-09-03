"""Plugin lint command."""

from __future__ import annotations

import logging
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory, validate_metadata_in_plugin_directory

logger = logging.getLogger(__name__)


@click.command()
@click.argument("path")
def lint_plugin_directory(path: str) -> None:
    plugin_path = Path(path)
    try:
        metadata = get_metadata_from_plugin_directory(plugin_path)
        validate_metadata_in_plugin_directory(plugin_path)

        if not metadata.schema_:
            console.print("[yellow]Recommendation[/yellow]: ida-plugin.json: provide $schema")
            console.print(
                "  like: https://raw.githubusercontent.com/HexRaysSA/ida-hcli/refs/heads/v0.9.0/docs/reference/ida-plugin.schema.json"
            )

        if not metadata.ida_versions:
            console.print("[yellow]Recommendation[/yellow]: ida-plugin.json: provide plugin.idaVersions")

        if not metadata.description:
            console.print("[yellow]Recommendation[/yellow]: ida-plugin.json: provide plugin.description")

        if not metadata.categories:
            console.print("[yellow]Recommendation[/yellow]: ida-plugin.json: provide plugin.categories")

        if not metadata.logo_path:
            console.print("[yellow]Recommendation[/yellow]: ida-plugin.json: provide plugin.logoPath")

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")
