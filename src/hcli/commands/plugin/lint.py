"""Plugin lint command."""

from __future__ import annotations

import logging
from pathlib import Path

import rich_click as click
from pydantic import ValidationError

from hcli.lib.console import console
from hcli.lib.ida.plugin import IDAPluginMetadata, parse_ida_version_spec, parse_plugin_version
from hcli.lib.ida.plugin.install import validate_metadata_in_plugin_directory

logger = logging.getLogger(__name__)


@click.command()
@click.argument("path")
def lint_plugin_directory(path: str) -> None:
    plugin_path = Path(path)

    metadata_file = None
    for filename in ("ida-plugin.json", "ida-plugin.json.disabled"):
        candidate_file = plugin_path / filename
        if candidate_file.exists():
            metadata_file = candidate_file
            break

    if not metadata_file:
        console.print(f"[red]Error[/red]: ida-plugin.json not found in {plugin_path}")
        return

    try:
        content = metadata_file.read_text(encoding="utf-8")
        metadata = IDAPluginMetadata.model_validate_json(content)
    except ValidationError as e:
        console.print("[red]Error[/red]: ida-plugin.json validation failed")
        for error in e.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            error_msg = error["msg"]
            error_type = error["type"]

            if error_type == "missing":
                console.print(f"  [red]Missing required field[/red]: {field_path}")
            else:
                console.print(f"  [red]Invalid value[/red] for {field_path}: {error_msg}")

        click.Abort()
        return

    if not parse_plugin_version(metadata.version):
        console.print("[red]Error[/red]: plugin version should look like 'X.Y.Z'")

    if metadata.ida_versions and not parse_ida_version_spec(metadata.ida_versions):
        console.print("[red]Error[/red]: idaVersion should look like 'X.YspZ'")

    try:
        # Additional validation
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

        click.Abort()
        return
