"""Plugin lint command."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import requests
import rich_click as click
from pydantic import ValidationError

from hcli.lib.console import console
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.install import validate_metadata_in_plugin_directory
from hcli.lib.ida.plugin.repo import fetch_plugin_archive
from hcli.lib.util.logging import m

logger = logging.getLogger(__name__)


def _lint_readme_in_directory(plugin_path: Path, source_name: str) -> int:
    """Check for README.md file in plugin directory.

    returns: number of recommendations made
    """
    found_files = []
    has_exact_match = False
    recommendation_count = 0

    for item in plugin_path.iterdir():
        if item.is_file() and item.name == "README.md":
            has_exact_match = True
            break
        if item.is_file() and item.name.lower().startswith("readme"):
            found_files.append(item.name)

    if has_exact_match:
        return 0

    if found_files:
        console.print(
            f"[yellow]Recommendation[/yellow] ({source_name}): rename {found_files[0]} to README.md (exact casing)"
        )
        console.print("  Use 'README.md' with exact casing for consistency and discoverability")
        recommendation_count += 1
    else:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): add a README.md file")
        console.print("  A README helps users understand what your plugin does and how to use it")
        recommendation_count += 1

    return recommendation_count


def _lint_readme_in_archive(zip_data: bytes, metadata_path: Path, source_name: str) -> int:
    """Check for README.md file in plugin archive.

    returns: number of recommendations made
    """
    plugin_dir = metadata_path.parent
    recommendation_count = 0

    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        namelist = zip_file.namelist()

        found_files = []
        has_exact_match = False

        for file_path in namelist:
            path_obj = Path(file_path)
            if path_obj.parent == plugin_dir:
                if path_obj.name == "README.md":
                    has_exact_match = True
                    break
                if path_obj.name.lower().startswith("readme"):
                    found_files.append(path_obj.name)

        if has_exact_match:
            return 0

        if found_files:
            console.print(
                f"[yellow]Recommendation[/yellow] ({source_name}): rename {found_files[0]} to README.md (exact casing)"
            )
            console.print("  Use 'README.md' with exact casing for consistency and discoverability")
            recommendation_count += 1
        else:
            console.print(f"[yellow]Recommendation[/yellow] ({source_name}): add a README.md file")
            console.print("  A README helps users understand what your plugin does and how to use it")
            recommendation_count += 1

        return recommendation_count


def _check_unexpected_keys(metadata: IDAMetadataDescriptor, source_name: str) -> int:
    """Check for unexpected keys in the plugin metadata.

    returns: number of recommendations made
    """
    recommendation_count = 0

    if hasattr(metadata.plugin, "__pydantic_extra__") and metadata.plugin.__pydantic_extra__:
        extra_keys = sorted(metadata.plugin.__pydantic_extra__.keys())
        for key in extra_keys:
            console.print(f"[yellow]Warning[/yellow] ({source_name}): unexpected key in plugin metadata: '{key}'")
            console.print("  This key is not part of the ida-plugin.json schema and will be ignored")
            recommendation_count += 1

    return recommendation_count


def _lint_metadata(metadata: IDAMetadataDescriptor, source_name: str) -> int:
    """Validate a single plugin metadata and show lint recommendations.

    returns: number of recommendations made
    """
    recommendation_count = 0

    recommendation_count += _check_unexpected_keys(metadata, source_name)

    if not parse_plugin_version(metadata.plugin.version):
        console.print(f"[red]Error[/red] ({source_name}): plugin version should look like 'X.Y.Z'")
        recommendation_count += 1

    if not metadata.plugin.ida_versions:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.idaVersions")
        console.print("  Specify which IDA versions your plugin supports (e.g., ['9.0', '9.1'])")
        recommendation_count += 1

    if not metadata.plugin.description:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.description")
        console.print("  A one-line description improves discoverability in the plugin repository")
        recommendation_count += 1

    if not metadata.plugin.categories:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.categories")
        console.print("  Categories help users find your plugin (e.g., 'malware-analysis', 'decompilation')")
        recommendation_count += 1

    if not metadata.plugin.logo_path:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.logoPath")
        console.print("  A logo image (16:9 aspect ratio) makes your plugin more visually appealing")
        recommendation_count += 1

    if not metadata.plugin.keywords:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.keywords")
        console.print("  Keywords improve search discoverability in the plugin repository")
        recommendation_count += 1

    if not metadata.plugin.license:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.license")
        console.print("  Specify the license (e.g., 'MIT', 'Apache 2.0') to clarify usage rights")
        recommendation_count += 1

    if not metadata.plugin.authors and not metadata.plugin.maintainers:
        console.print(
            f"[red]Error[/red] ({source_name}): ida-plugin.json: provide plugin.authors or plugin.maintainers"
        )
        console.print("  Contact information is required for authors or maintainers")
        recommendation_count += 1
    else:
        # Check if contacts have both name and email for better completeness
        for i, author in enumerate(metadata.plugin.authors):
            if not author.name:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.authors[{i}]: provide an author name"
                )
                recommendation_count += 1
        for i, maintainer in enumerate(metadata.plugin.maintainers):
            if not maintainer.name:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.maintainers[{i}]: provide a maintainer name"
                )
                recommendation_count += 1

    return recommendation_count


def _lint_plugin_directory(plugin_path: Path) -> int:
    """Lint a plugin in a directory.

    returns: number of recommendations made
    """
    recommendation_count = 0

    metadata_file = plugin_path / "ida-plugin.json"
    if not metadata_file.exists():
        console.print(f"[red]Error[/red]: ida-plugin.json not found in {plugin_path}")
        return 1

    content = metadata_file.read_text(encoding="utf-8")
    try:
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

            recommendation_count += 1

        return recommendation_count

    try:
        validate_metadata_in_plugin_directory(plugin_path)
    except Exception as e:
        console.print(f"[red]Error[/red]: ida-plugin.json validation failed: {e}")
        recommendation_count += 1
        return recommendation_count

    recommendation_count += _lint_metadata(metadata, str(plugin_path))
    recommendation_count += _lint_readme_in_directory(plugin_path, str(plugin_path))

    return recommendation_count


def _lint_plugin_archive(zip_data: bytes, source_name: str) -> int:
    """Lint plugins in a .zip archive from bytes.

    returns: number of recommendations made
    """
    recommendation_count = 0

    plugins_found = []
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        for file_path in zip_file.namelist():
            if not file_path.endswith("ida-plugin.json"):
                continue

            logger.debug(m("found metadata path: %s", file_path))
            with zip_file.open(file_path) as f:
                try:
                    metadata = IDAMetadataDescriptor.model_validate_json(f.read().decode("utf-8"))
                except ValidationError as e:
                    logger.debug(m("failed to validate metadata: %s", file_path, path=file_path, error=str(e)))
                    console.print(f"[red]Error[/red] ({source_name}): {file_path}: ida-plugin.json validation failed")
                    for error in e.errors():
                        field_path = ".".join(str(loc) for loc in error["loc"])
                        error_msg = error["msg"]
                        error_type = error["type"]

                        if error_type == "missing":
                            console.print(f"  [red]Missing required field[/red]: {field_path}")
                        else:
                            console.print(f"  [red]Invalid value[/red] for {field_path}: {error_msg}")
                        recommendation_count += 1
                    continue
                else:
                    logger.debug(m("found valid metadata: %s", file_path))
                    plugins_found.append((Path(file_path), metadata))

    for path, meta in plugins_found:
        logger.debug("found plugin %s at %s", meta.plugin.name, path)

    if not plugins_found:
        console.print(f"[red]Error[/red]: No valid plugins found in archive {source_name}")
        recommendation_count += 1
        return recommendation_count

    for metadata_path, metadata in plugins_found:
        plugin_source_name = f"{source_name}:{metadata_path}"

        try:
            validate_metadata_in_plugin_archive(zip_data, metadata_path, metadata)
        except ValidationError as e:
            console.print(
                f"[red]Error[/red] ({plugin_source_name}): {metadata_path}: ida-plugin.json validation failed"
            )
            for error in e.errors():
                field_path = ".".join(str(loc) for loc in error["loc"])
                error_msg = error["msg"]
                error_type = error["type"]

                if error_type == "missing":
                    console.print(f"  [red]Missing required field[/red]: {field_path}")
                else:
                    console.print(f"  [red]Invalid value[/red] for {field_path}: {error_msg}")
                recommendation_count += 1

            continue

        except Exception as e:
            console.print(f"[red]Error[/red]: {metadata_path}: ida-plugin.json validation failed: {e}")
            recommendation_count += 1
            continue

        recommendation_count += _lint_metadata(metadata, plugin_source_name)
        recommendation_count += _lint_readme_in_archive(zip_data, metadata_path, plugin_source_name)

    return recommendation_count


@click.command()
@click.argument(
    "path",
    metavar="PATH|URL",
)
def lint_plugin_directory(path: str) -> None:
    """Lint an IDA plugin directory, archive (.zip file), or HTTPS URL."""
    recommendation_count = 0
    if path.startswith("https://"):
        logger.info("linting from HTTP URL")
        try:
            buf = fetch_plugin_archive(path)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            console.print(f"[red]Cannot connect to {path} - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()
        except Exception as e:
            console.print(f"[red]Error[/red]: Failed to fetch archive from {path}: {e}")
            raise click.Abort()

        recommendation_count = _lint_plugin_archive(buf, path)

    else:
        plugin_path = Path(path).expanduser().resolve()
        if not plugin_path.exists():
            console.print(f"[red]Error[/red]: Path does not exist: {plugin_path}")
            raise click.Abort()

        if plugin_path.is_file():
            if plugin_path.suffix.lower() != ".zip":
                console.print(f"[red]Error[/red]: File must be a .zip archive: {plugin_path}")
                raise click.Abort()

            zip_data = plugin_path.read_bytes()
            recommendation_count = _lint_plugin_archive(zip_data, str(plugin_path))
        elif plugin_path.is_dir():
            recommendation_count = _lint_plugin_directory(plugin_path)
        else:
            console.print(f"[red]Error[/red]: Path must be a directory or .zip file: {plugin_path}")
            raise click.Abort()

    if not recommendation_count:
        console.print("[green]no recommendations[/green]")
