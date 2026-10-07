"""Plugin lint command."""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx
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


@dataclass
class LintResult:
    recommendations: int = 0
    # any error fails the lint command
    errors: int = 0


def _lint_readme_in_directory(plugin_path: Path, source_name: str, result: LintResult) -> None:
    """Check for README.md file in plugin directory."""
    found_files = []
    has_exact_match = False

    for item in plugin_path.iterdir():
        if item.is_file() and item.name == "README.md":
            has_exact_match = True
            break
        if item.is_file() and item.name.lower().startswith("readme"):
            found_files.append(item.name)

    if has_exact_match:
        return

    if found_files:
        console.print(
            f"[yellow]Recommendation[/yellow] ({source_name}): rename {found_files[0]} to README.md (exact casing)"
        )
        console.print("  Use 'README.md' with exact casing for consistency and discoverability")
        result.recommendations += 1
    else:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): add a README.md file")
        console.print("  A README helps users understand what your plugin does and how to use it")
        result.recommendations += 1


def _lint_readme_in_archive(zip_data: bytes, metadata_path: Path, source_name: str, result: LintResult) -> None:
    """Check for README.md file in plugin archive."""
    plugin_dir = metadata_path.parent

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
            return

        if found_files:
            console.print(
                f"[yellow]Recommendation[/yellow] ({source_name}): rename {found_files[0]} to README.md (exact casing)"
            )
            console.print("  Use 'README.md' with exact casing for consistency and discoverability")
            result.recommendations += 1
        else:
            console.print(f"[yellow]Recommendation[/yellow] ({source_name}): add a README.md file")
            console.print("  A README helps users understand what your plugin does and how to use it")
            result.recommendations += 1


def _check_unexpected_keys(metadata: IDAMetadataDescriptor, source_name: str, result: LintResult) -> None:
    """Check for unexpected keys in the plugin metadata."""
    if hasattr(metadata.plugin, "__pydantic_extra__") and metadata.plugin.__pydantic_extra__:
        extra_keys = sorted(metadata.plugin.__pydantic_extra__.keys())
        for key in extra_keys:
            console.print(f"[yellow]Warning[/yellow] ({source_name}): unexpected key in plugin metadata: '{key}'")
            console.print("  This key is not part of the ida-plugin.json schema and will be ignored")
            result.recommendations += 1


def _lint_metadata(metadata: IDAMetadataDescriptor, source_name: str, result: LintResult) -> None:
    """Validate a single plugin metadata and show lint recommendations."""
    _check_unexpected_keys(metadata, source_name, result)

    if not parse_plugin_version(metadata.plugin.version):
        console.print(f"[red]Error[/red] ({source_name}): plugin version should look like 'X.Y.Z'")
        result.errors += 1

    if not metadata.plugin.ida_versions:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.idaVersions")
        console.print("  Specify which IDA versions your plugin supports (e.g., ['9.0', '9.1'])")
        result.recommendations += 1

    if not metadata.plugin.description:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.description")
        console.print("  A one-line description improves discoverability in the plugin repository")
        result.recommendations += 1

    if not metadata.plugin.categories:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.categories")
        console.print("  Categories help users find your plugin (e.g., 'malware-analysis', 'decompilation')")
        result.recommendations += 1

    if not metadata.plugin.logo_path:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.logoPath")
        console.print("  A logo image (16:9 aspect ratio) makes your plugin more visually appealing")
        result.recommendations += 1

    if not metadata.plugin.keywords:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.keywords")
        console.print("  Keywords improve search discoverability in the plugin repository")
        result.recommendations += 1

    if not metadata.plugin.license:
        console.print(f"[yellow]Recommendation[/yellow] ({source_name}): ida-plugin.json: provide plugin.license")
        console.print("  Specify the license (e.g., 'MIT', 'Apache 2.0') to clarify usage rights")
        result.recommendations += 1

    if not metadata.plugin.authors and not metadata.plugin.maintainers:
        console.print(
            f"[red]Error[/red] ({source_name}): ida-plugin.json: provide plugin.authors or plugin.maintainers"
        )
        console.print("  Contact information is required for authors or maintainers")
        result.errors += 1
    else:
        # Check if contacts have both name and email for better completeness
        for i, author in enumerate(metadata.plugin.authors):
            if not author.name:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.authors[{i}]: provide an author name"
                )
                result.recommendations += 1
        for i, maintainer in enumerate(metadata.plugin.maintainers):
            if not maintainer.name:
                console.print(
                    f"[yellow]Recommendation[/yellow] ({source_name}): plugin.maintainers[{i}]: provide a maintainer name"
                )
                result.recommendations += 1

    _check_dependency_specs(metadata, source_name, result)
    _check_expanded_components(metadata, source_name, result)


def _is_github_host(url: str) -> bool:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower() in ("github.com", "www.github.com")


def _check_dependency_specs(metadata: IDAMetadataDescriptor, source_name: str, result: LintResult) -> None:
    from hcli.lib.ida.plugin.reference import DependencyEntry

    warn_bare = not _is_github_host(metadata.plugin.host)
    for i, entry in enumerate(metadata.plugin.dependencies):
        if not isinstance(entry, DependencyEntry):
            console.print(
                f"[red]Error[/red] ({source_name}): plugin.dependencies[{i}]: unexpected type {type(entry).__name__}"
            )
            result.errors += 1
            continue
        ref = entry.reference
        if warn_bare and ref.host is None:
            console.print(
                f"[yellow]Recommendation[/yellow] ({source_name}): plugin.dependencies[{i}]: "
                f"use name@host format ('{ref.name}@<host>') to avoid ambiguity across repositories"
            )
            result.recommendations += 1


def _check_expanded_components(metadata: IDAMetadataDescriptor, source_name: str, result: LintResult) -> None:
    for i, entry in enumerate(metadata.plugin.components):
        if isinstance(entry, IDAMetadataDescriptor):
            console.print(
                f"[red]Error[/red] ({source_name}): plugin.components[{i}]: "
                f"contains expanded metadata object for '{entry.plugin.name}'; "
                f"use the string form in authored archives"
            )
            result.errors += 1


def _check_components_in_directory(
    plugin_path: Path, metadata: IDAMetadataDescriptor, source_name: str, result: LintResult
) -> None:
    from hcli.lib.ida.plugin.components import (
        find_undeclared_plugins_in_directory,
        walk_component_tree_from_directory,
    )

    undeclared = find_undeclared_plugins_in_directory(plugin_path)
    for ud_path, ud_meta in undeclared:
        console.print(
            f"[yellow]Warning[/yellow] ({source_name}): "
            f"subdirectory '{ud_path.name}' contains ida-plugin.json "
            f"(plugin '{ud_meta.plugin.name}') but is not declared as a component"
        )
        result.recommendations += 1

    if not metadata.plugin.components:
        return

    try:
        tree = walk_component_tree_from_directory(plugin_path)
    except ValueError as e:
        console.print(f"[red]Error[/red] ({source_name}): component validation failed: {e}")
        result.errors += 1
        return

    seen_names: set[str] = {metadata.plugin.name}
    for _, comp_meta in tree:
        comp_source = f"{source_name}:{comp_meta.plugin.name}"
        if comp_meta.plugin.name in seen_names:
            console.print(f"[red]Error[/red] ({comp_source}): duplicate component name '{comp_meta.plugin.name}'")
            result.errors += 1
        seen_names.add(comp_meta.plugin.name)
        _lint_metadata(comp_meta, comp_source, result)


def _check_components_in_archive(
    zip_data: bytes, metadata_path: Path, metadata: IDAMetadataDescriptor, source_name: str, result: LintResult
) -> None:
    from hcli.lib.ida.plugin.components import (
        find_undeclared_plugins_in_archive,
        walk_component_tree_from_archive,
    )

    undeclared = find_undeclared_plugins_in_archive(zip_data, metadata_path, metadata)
    for ud_path, ud_meta in undeclared:
        console.print(
            f"[yellow]Warning[/yellow] ({source_name}): "
            f"archive contains plugin '{ud_meta.plugin.name}' "
            f"that is not declared as a component"
        )
        result.recommendations += 1

    if not metadata.plugin.components:
        return

    try:
        tree = walk_component_tree_from_archive(zip_data, metadata_path, metadata)
    except ValueError as e:
        console.print(f"[red]Error[/red] ({source_name}): component validation failed: {e}")
        result.errors += 1
        return

    seen_names: set[str] = {metadata.plugin.name}
    for _, comp_meta in tree:
        comp_source = f"{source_name}:{comp_meta.plugin.name}"
        if comp_meta.plugin.name in seen_names:
            console.print(f"[red]Error[/red] ({comp_source}): duplicate component name '{comp_meta.plugin.name}'")
            result.errors += 1
        seen_names.add(comp_meta.plugin.name)
        _lint_metadata(comp_meta, comp_source, result)


def _lint_plugin_directory(plugin_path: Path, result: LintResult) -> None:
    """Lint a plugin in a directory."""
    metadata_file = plugin_path / "ida-plugin.json"
    if not metadata_file.exists():
        console.print(f"[red]Error[/red]: ida-plugin.json not found in {plugin_path}")
        result.errors += 1
        return

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

            result.errors += 1

        return

    try:
        validate_metadata_in_plugin_directory(plugin_path)
    except Exception as e:
        console.print(f"[red]Error[/red]: ida-plugin.json validation failed: {e}")
        result.errors += 1
        return

    _lint_metadata(metadata, str(plugin_path), result)
    _lint_readme_in_directory(plugin_path, str(plugin_path), result)
    _check_components_in_directory(plugin_path, metadata, str(plugin_path), result)


def _check_root_manifest_at_top_level(
    plugins_found: list[tuple[Path, IDAMetadataDescriptor]],
    source_name: str,
    result: LintResult,
) -> None:
    if len(plugins_found) <= 1:
        return

    from hcli.lib.ida.plugin import get_component_name

    referenced_as_component: set[str] = set()
    for _, meta in plugins_found:
        referenced_as_component.update(get_component_name(e) for e in meta.plugin.components)

    roots = [(path, meta) for path, meta in plugins_found if meta.plugin.name not in referenced_as_component]
    for root_path, root_meta in roots:
        parts = root_path.parts
        if len(parts) != 2:
            console.print(
                f"[red]Error[/red] ({source_name}): root manifest for '{root_meta.plugin.name}' "
                f"is at '{root_path}' but should be at the archive's top level "
                f"(e.g., '{root_meta.plugin.name}/ida-plugin.json')"
            )
            result.errors += 1


def _lint_plugin_archive(zip_data: bytes, source_name: str, result: LintResult) -> None:
    """Lint plugins in a .zip archive from bytes."""
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
                        result.errors += 1
                    continue
                else:
                    logger.debug(m("found valid metadata: %s", file_path))
                    plugins_found.append((Path(file_path), metadata))

    for path, meta in plugins_found:
        logger.debug("found plugin %s at %s", meta.plugin.name, path)

    if not plugins_found:
        console.print(f"[red]Error[/red]: No valid plugins found in archive {source_name}")
        result.errors += 1
        return

    _check_root_manifest_at_top_level(plugins_found, source_name, result)

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
                result.errors += 1

            continue

        except Exception as e:
            console.print(f"[red]Error[/red]: {metadata_path}: ida-plugin.json validation failed: {e}")
            result.errors += 1
            continue

        _lint_metadata(metadata, plugin_source_name, result)
        _lint_readme_in_archive(zip_data, metadata_path, plugin_source_name, result)
        _check_components_in_archive(zip_data, metadata_path, metadata, plugin_source_name, result)


@click.command()
@click.argument(
    "path",
    metavar="PATH|URL",
)
def lint_plugin_directory(path: str) -> None:
    """Lint an IDA plugin directory, archive (.zip file), or HTTPS URL."""
    result = LintResult()

    if path.startswith("https://"):
        logger.info("linting from HTTP URL")
        try:
            buf = fetch_plugin_archive(path)
        except (httpx.ConnectError, httpx.TimeoutException):
            console.print(f"[red]Cannot connect to {path} - network unavailable.[/red]")
            console.print("Please check your internet connection.")
            raise click.Abort()
        except Exception as e:
            console.print(f"[red]Error[/red]: Failed to fetch archive from {path}: {e}")
            raise click.Abort()

        _lint_plugin_archive(buf, path, result)

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
            _lint_plugin_archive(zip_data, str(plugin_path), result)
        elif plugin_path.is_dir():
            _lint_plugin_directory(plugin_path, result)
        else:
            console.print(f"[red]Error[/red]: Path must be a directory or .zip file: {plugin_path}")
            raise click.Abort()

    if not result.recommendations and not result.errors:
        console.print("[green]no recommendations[/green]")

    if result.errors:
        raise click.Abort()
