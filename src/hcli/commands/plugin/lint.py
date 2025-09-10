"""Plugin lint command."""

from __future__ import annotations

import logging
from pathlib import Path

import rich_click as click
from pydantic import ValidationError

from hcli.lib.console import console
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadatas_with_paths_from_plugin_archive,
    parse_ida_version_spec,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.install import validate_metadata_in_plugin_directory

logger = logging.getLogger(__name__)


def _validate_and_lint_metadata(metadata: IDAMetadataDescriptor, source_name: str) -> None:
    """Validate a single plugin metadata and show lint recommendations."""
    if not parse_plugin_version(metadata.plugin.version):
        console.print(f"[red]Error[/red] ({source_name}): plugin version should look like 'X.Y.Z'")

    if metadata.plugin.ida_versions and not parse_ida_version_spec(metadata.plugin.ida_versions):
        console.print(f"[red]Error[/red] ({source_name}): idaVersion should look like 'X.YspZ'")

    if not metadata.schema_:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide $schema")
        console.print(
            "  like: https://raw.githubusercontent.com/HexRaysSA/ida-hcli/refs/heads/v0.9.0/docs/reference/ida-plugin.schema.json"
        )

    if not metadata.plugin.ida_versions:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.idaVersions")

    if not metadata.plugin.description:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.description")

    if not metadata.plugin.categories:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.categories")

    if not metadata.plugin.logo_path:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.logoPath")


def _lint_plugin_directory(plugin_path: Path) -> None:
    """Lint a plugin in a directory."""
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
        metadata = IDAMetadataDescriptor.model_validate_json(content)
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

    try:
        # Additional validation
        validate_metadata_in_plugin_directory(plugin_path)
        _validate_and_lint_metadata(metadata, str(plugin_path))

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")

        click.Abort()
        return


def _lint_plugin_archive(archive_path: Path) -> None:
    """Lint plugins in a .zip archive."""
    logger.debug("reading plugin archive from %s", archive_path)
    try:
        zip_data = archive_path.read_bytes()
    except Exception as e:
        console.print(f"[red]Error[/red]: Failed to read archive {archive_path}: {e}")
        return

    try:
        plugins_found = list(get_metadatas_with_paths_from_plugin_archive(zip_data))
    except Exception as e:
        console.print(f"[red]Error[/red]: Failed to read plugins from archive {archive_path}: {e}")
        return
    else:
        for path, meta in plugins_found:
            logger.debug("found plugin %s at %s", meta.plugin.name, path)

    if not plugins_found:
        console.print(f"[red]Error[/red]: No valid plugins found in archive {archive_path}")
        return

    for metadata_path, metadata in plugins_found:
        source_name = f"{archive_path}:{metadata_path}"

        try:
            validate_metadata_in_plugin_archive(zip_data, metadata)
            _validate_and_lint_metadata(metadata, source_name)

        except ValidationError as e:
            console.print(f"[red]Error[/red] ({source_name}): ida-plugin.json validation failed")
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error["loc"])
                error_msg = error["msg"]
                error_type = error["type"]

                if error_type == "missing":
                    console.print(f"  [red]Missing required field[/red]: {field_path}")
                else:
                    console.print(f"  [red]Invalid value[/red] for {field_path}: {error_msg}")

        except Exception as e:
            logger.warning("error: %s", e, exc_info=True)
            console.print(f"[red]Error[/red] ({source_name}): {e}")


@click.command()
@click.argument("path")
def lint_plugin_directory(path: str) -> None:
    """Lint an IDA plugin directory or archive (.zip file)."""
    plugin_path = Path(path)

    if not plugin_path.exists():
        console.print(f"[red]Error[/red]: Path does not exist: {plugin_path}")
        return

    if plugin_path.is_file():
        if plugin_path.suffix.lower() == ".zip":
            _lint_plugin_archive(plugin_path)
        else:
            console.print(f"[red]Error[/red]: File must be a .zip archive: {plugin_path}")
    elif plugin_path.is_dir():
        _lint_plugin_directory(plugin_path)
    else:
        console.print(f"[red]Error[/red]: Path must be a directory or .zip file: {plugin_path}")
