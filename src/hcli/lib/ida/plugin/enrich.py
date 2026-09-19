"""Recursive expansion of plugin metadata for publication in repository indexes."""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from pydantic import ValidationError

from hcli.lib.ida.plugin import (
    MAX_COMPONENT_DEPTH,
    IDAMetadataDescriptor,
    get_component_name,
    get_file_content_from_plugin_archive_at,
    get_python_dependencies_from_plugin_directory,
    parse_pep723_metadata,
)

logger = logging.getLogger(__name__)


def expand_metadata_from_archive(zip_data: bytes, root_manifest_path: Path) -> IDAMetadataDescriptor:
    """Return the manifest at ``root_manifest_path`` with its component tree fully embedded.

    Each component is read from ``<parent dir>/<name>/ida-plugin.json`` inside the
    archive and embedded as a descriptor. ``pythonDependencies: "inline"`` is
    replaced by the PEP 723 list from the entry point at every node. The archive
    bytes are not modified.

    Raises:
        ValueError: when a manifest is missing or malformed, a component name does
            not match its directory, an embedded source entry disagrees with the
            physical manifest, an inline entry point cannot be read, or nesting
            exceeds ``MAX_COMPONENT_DEPTH``.
    """
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        members = set(zip_file.namelist())
    return _expand_archive_node(zip_data, members, root_manifest_path, depth=0)


def expand_metadata_from_directory(plugin_dir: Path) -> IDAMetadataDescriptor:
    """Return the manifest in ``plugin_dir`` with its component tree fully embedded.

    Raises:
        ValueError: on the same conditions as ``expand_metadata_from_archive``, and
            when symlinks make the component tree cyclic.
    """
    return _expand_directory_node(plugin_dir, depth=0, visited=frozenset())


def _parse_manifest(raw: bytes, label: str) -> IDAMetadataDescriptor:
    try:
        return IDAMetadataDescriptor.model_validate_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, ValidationError) as e:
        raise ValueError(f"malformed ida-plugin.json at {label}: {e}") from e


def _validate_child(parent: IDAMetadataDescriptor, entry: str | IDAMetadataDescriptor, child: IDAMetadataDescriptor):
    name = get_component_name(entry)
    if child.plugin.name != name:
        raise ValueError(
            f"component '{name}' declared by '{parent.plugin.name}' "
            f"contains plugin '{child.plugin.name}' (name mismatch)"
        )
    if isinstance(entry, IDAMetadataDescriptor) and entry.plugin.version != child.plugin.version:
        raise ValueError(
            f"component '{name}' embedded in '{parent.plugin.name}' declares version "
            f"{entry.plugin.version} but the manifest on disk is {child.plugin.version}"
        )


def _with_expansion(
    descriptor: IDAMetadataDescriptor,
    python_dependencies: list[str],
    components: list[str | IDAMetadataDescriptor],
) -> IDAMetadataDescriptor:
    plugin = descriptor.plugin.model_copy(
        update={"python_dependencies": list(python_dependencies), "components": components},
        deep=True,
    )
    return descriptor.model_copy(update={"plugin": plugin})


def _expand_archive_node(
    zip_data: bytes,
    members: set[str],
    manifest_path: Path,
    *,
    depth: int,
) -> IDAMetadataDescriptor:
    if depth > MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    manifest_key = manifest_path.as_posix()
    if manifest_key not in members:
        raise ValueError(f"ida-plugin.json not found at {manifest_key}")
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        raw = zip_file.read(manifest_key)
    descriptor = _parse_manifest(raw, manifest_key)

    python_dependencies = descriptor.plugin.python_dependencies
    if python_dependencies == "inline":
        entry_point = descriptor.plugin.entry_point
        if not entry_point.endswith(".py"):
            raise ValueError(f"{manifest_key}: entry point must be a Python file for inline dependencies")
        try:
            content = get_file_content_from_plugin_archive_at(zip_data, manifest_path, entry_point)
        except KeyError as e:
            raise ValueError(f"{manifest_key}: inline dependencies entry point not found: {entry_point}") from e
        python_dependencies = parse_pep723_metadata(content.decode("utf-8"))
    assert isinstance(python_dependencies, list)

    components: list[str | IDAMetadataDescriptor] = []
    for entry in descriptor.plugin.components:
        name = get_component_name(entry)
        child_path = manifest_path.parent / name / "ida-plugin.json"
        child = _expand_archive_node(zip_data, members, child_path, depth=depth + 1)
        _validate_child(descriptor, entry, child)
        components.append(child)

    return _with_expansion(descriptor, python_dependencies, components)


def _expand_directory_node(
    plugin_dir: Path,
    *,
    depth: int,
    visited: frozenset[Path],
) -> IDAMetadataDescriptor:
    if depth > MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    resolved = plugin_dir.resolve()
    if resolved in visited:
        raise ValueError(f"component tree contains a cycle through {plugin_dir}")

    manifest_file = plugin_dir / "ida-plugin.json"
    if not manifest_file.is_file():
        raise ValueError(f"ida-plugin.json not found at {manifest_file}")
    descriptor = _parse_manifest(manifest_file.read_bytes(), str(manifest_file))

    try:
        python_dependencies = get_python_dependencies_from_plugin_directory(plugin_dir, descriptor)
    except OSError as e:
        raise ValueError(f"{manifest_file}: inline dependencies entry point not readable: {e}") from e

    components: list[str | IDAMetadataDescriptor] = []
    for entry in descriptor.plugin.components:
        name = get_component_name(entry)
        child_dir = plugin_dir / name
        if not child_dir.is_dir():
            raise ValueError(
                f"component '{name}' declared by '{descriptor.plugin.name}' but subdirectory not found: {child_dir}"
            )
        child = _expand_directory_node(child_dir, depth=depth + 1, visited=visited | {resolved})
        _validate_child(descriptor, entry, child)
        components.append(child)

    return _with_expansion(descriptor, python_dependencies, components)
