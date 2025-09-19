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
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.install import validate_metadata_in_plugin_directory

logger = logging.getLogger(__name__)


def _validate_and_lint_metadata(metadata: IDAMetadataDescriptor, source_name: str) -> None:
    """Validate a single plugin metadata and show lint recommendations."""
    if not parse_plugin_version(metadata.plugin.version):
        console.print(f"[red]Error[/red] ({source_name}): plugin version should look like 'X.Y.Z'")

    if not metadata.plugin.ida_versions:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.idaVersions")
        console.print("  Specify which IDA versions your plugin supports (e.g., ['9.0', '9.1'])")

    if not metadata.plugin.description:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.description")
        console.print("  A one-line description improves discoverability in the plugin repository")

    if not metadata.plugin.categories:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.categories")
        console.print("  Categories help users find your plugin (e.g., 'malware-analysis', 'decompilation')")

    if not metadata.plugin.logo_path:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.logoPath")
        console.print("  A logo image (16:9 aspect ratio) makes your plugin more visually appealing")

    if not metadata.plugin.keywords:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.keywords")
        console.print("  Keywords improve search discoverability in the plugin repository")

    if not metadata.plugin.license:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.license")
        console.print("  Specify the license (e.g., 'MIT', 'Apache 2.0') to clarify usage rights")

    if not metadata.plugin.authors and not metadata.plugin.maintainers:
        console.print(
            f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.authors or plugin.maintainers"
        )
        console.print("  Contact information helps users report issues and contribute")
    else:
        # Check if contacts have both name and email for better completeness
        for i, author in enumerate(metadata.plugin.authors):
            if not author.name or not author.email:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.authors[{i}]: provide both name and email"
                )
                console.print("  Complete contact information improves user trust and communication")
        for i, maintainer in enumerate(metadata.plugin.maintainers):
            if not maintainer.name or not maintainer.email:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.maintainers[{i}]: provide both name and email"
                )
                console.print("  Complete contact information improves user trust and communication")


def _lint_plugin_directory(plugin_path: Path) -> None:
    """Lint a plugin in a directory."""
    metadata_file = plugin_path / "ida-plugin.json"
    if not metadata_file.exists():
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

        raise click.Abort()

    try:
        # Additional validation
        validate_metadata_in_plugin_directory(plugin_path)
        _validate_and_lint_metadata(metadata, str(plugin_path))

    except Exception as e:
        logger.warning("error: %s", e, exc_info=True)
        console.print(f"[red]Error[/red]: {e}")

        raise click.Abort()


def _lint_plugin_archive(archive_path: Path) -> None:
    """Lint plugins in a .zip archive."""
    logger.debug("reading plugin archive from %s", archive_path)
    try:
        zip_data = archive_path.read_bytes()
    except Exception as e:
        console.print(f"[red]Error[/red]: Failed to read archive {archive_path}: {e}")
        raise click.Abort()

    try:
        plugins_found = list(get_metadatas_with_paths_from_plugin_archive(zip_data))
    except Exception as e:
        console.print(f"[red]Error[/red]: Failed to read plugins from archive {archive_path}: {e}")
        raise click.Abort()
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
            raise click.Abort()


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
