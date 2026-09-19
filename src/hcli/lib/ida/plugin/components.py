"""Component tree walking and validation for tightly-coupled plugin suites."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from hcli.lib.ida.plugin import (
    MAX_COMPONENT_DEPTH,
    IDAMetadataDescriptor,
    get_metadatas_with_paths_from_plugin_archive,
    get_python_dependencies_from_plugin_directory,
    iter_component_names,
)

if TYPE_CHECKING:
    from hcli.lib.ida.plugin.install import InstalledPluginRecord

logger = logging.getLogger(__name__)

__all__ = ["MAX_COMPONENT_DEPTH"]


def walk_component_tree_from_directory(
    plugin_dir: Path,
    *,
    _depth: int = 0,
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Walk a suite's component tree on disk, validating as we go.

    Returns a flat list of (path, descriptor) for every component found
    at any nesting depth. The suite itself is NOT included.

    Raises:
        ValueError: on missing subdirectory, name mismatch, or depth exceeded.
    """
    if _depth >= MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    metadata = _read_metadata_from_directory(plugin_dir)
    result: list[tuple[Path, IDAMetadataDescriptor]] = []

    for component_name in iter_component_names(metadata.plugin):
        component_dir = plugin_dir / component_name
        if not component_dir.is_dir():
            raise ValueError(
                f"component '{component_name}' declared by '{metadata.plugin.name}' "
                f"but subdirectory not found: {component_dir}"
            )

        component_metadata = _read_metadata_from_directory(component_dir)
        if component_metadata.plugin.name != component_name:
            raise ValueError(
                f"component directory '{component_name}' contains plugin "
                f"'{component_metadata.plugin.name}' (name mismatch)"
            )

        result.append((component_dir, component_metadata))
        result.extend(walk_component_tree_from_directory(component_dir, _depth=_depth + 1))

    return result


def walk_component_tree_from_archive(
    zip_data: bytes,
    root_path: Path,
    root_metadata: IDAMetadataDescriptor,
    *,
    _depth: int = 0,
    _all_metadatas: dict[Path, IDAMetadataDescriptor] | None = None,
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Walk a suite's component tree inside a zip archive, validating as we go.

    Each component must live at ``<parent dir>/<name>/ida-plugin.json``.
    Returns a flat list of (archive_path, descriptor) for every component
    found at any nesting depth. The root manifest is NOT included.

    Raises:
        ValueError: on missing component, name mismatch, or depth exceeded.
    """
    if _depth >= MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    if _all_metadatas is None:
        _all_metadatas = dict(get_metadatas_with_paths_from_plugin_archive(zip_data))

    root_dir = root_path.parent
    result: list[tuple[Path, IDAMetadataDescriptor]] = []

    for component_name in iter_component_names(root_metadata.plugin):
        comp_path = root_dir / component_name / "ida-plugin.json"
        if comp_path not in _all_metadatas:
            raise ValueError(
                f"component '{component_name}' declared by '{root_metadata.plugin.name}' "
                f"but no valid ida-plugin.json found at {comp_path}"
            )

        comp_meta = _all_metadatas[comp_path]
        if comp_meta.plugin.name != component_name:
            raise ValueError(
                f"component '{component_name}' manifest has plugin.name '{comp_meta.plugin.name}' (name mismatch)"
            )

        result.append((comp_path, comp_meta))
        result.extend(
            walk_component_tree_from_archive(
                zip_data,
                comp_path,
                comp_meta,
                _depth=_depth + 1,
                _all_metadatas=_all_metadatas,
            )
        )

    return result


def collect_python_dependencies_from_directory(
    plugin_dir: Path,
    metadata: IDAMetadataDescriptor,
) -> list[str]:
    """Collect pythonDependencies from the root and every component on disk."""
    deps = list(get_python_dependencies_from_plugin_directory(plugin_dir, metadata))
    if metadata.plugin.components:
        for comp_dir, comp_meta in walk_component_tree_from_directory(plugin_dir):
            deps.extend(get_python_dependencies_from_plugin_directory(comp_dir, comp_meta))
    return deps


def collect_all_component_names_from_archive(
    zip_data: bytes,
    root_path: Path,
    root_metadata: IDAMetadataDescriptor,
) -> set[str]:
    """Collect all component names in a suite archive (at any depth)."""
    tree = walk_component_tree_from_archive(zip_data, root_path, root_metadata)
    return {meta.plugin.name for _, meta in tree}


def find_root_manifest_in_archive(
    zip_data: bytes,
) -> tuple[Path, IDAMetadataDescriptor]:
    """Identify the root manifest in a multi-manifest archive.

    The root is the manifest whose plugin.name is not listed as a component
    by any other manifest in the archive.

    Raises:
        ValueError: if zero or multiple root candidates exist.
    """
    all_items = list(get_metadatas_with_paths_from_plugin_archive(zip_data))
    if not all_items:
        raise ValueError("no valid ida-plugin.json found in archive")

    if len(all_items) == 1:
        return all_items[0]

    referenced_as_component: set[str] = set()
    for _, meta in all_items:
        referenced_as_component.update(iter_component_names(meta.plugin))

    roots = [(path, meta) for path, meta in all_items if meta.plugin.name not in referenced_as_component]

    if len(roots) == 0:
        raise ValueError("circular component references: every manifest is referenced as a component")
    if len(roots) > 1:
        names = ", ".join(meta.plugin.name for _, meta in roots)
        raise ValueError(f"archive contains multiple unrelated plugins ({names}); specify which one to install")

    return roots[0]


def find_suite_for_component(name: str) -> InstalledPluginRecord | None:
    """If *name* is a component of an installed suite, return that suite's record."""
    from hcli.lib.ida.plugin.install import get_installed_plugin_records

    for record in get_installed_plugin_records():
        if not record.metadata.plugin.components:
            continue

        try:
            tree = walk_component_tree_from_directory(record.path)
        except ValueError:
            continue

        for _, comp_meta in tree:
            if comp_meta.plugin.name == name:
                return record

    return None


def find_undeclared_plugins_in_directory(
    plugin_dir: Path,
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Find subdirectories that contain ida-plugin.json but are not declared as components.

    Walks the declared component tree first, then scans every subdirectory for
    manifests that weren't referenced. Returns a flat list of (path, metadata)
    for each undeclared plugin found.
    """
    try:
        tree = walk_component_tree_from_directory(plugin_dir)
    except ValueError:
        tree = []
    all_declared_dirs = {path for path, _ in tree}
    all_declared_dirs.add(plugin_dir)

    undeclared: list[tuple[Path, IDAMetadataDescriptor]] = []
    _scan_for_undeclared(plugin_dir, all_declared_dirs, undeclared)
    return undeclared


def _scan_for_undeclared(
    directory: Path,
    declared_dirs: set[Path],
    result: list[tuple[Path, IDAMetadataDescriptor]],
) -> None:
    """Recursively scan for undeclared plugin subdirectories."""
    for child in sorted(directory.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "ida-plugin.json"
        if not manifest.exists():
            continue
        if child in declared_dirs:
            _scan_for_undeclared(child, declared_dirs, result)
        else:
            try:
                meta = _read_metadata_from_directory(child)
            except ValueError:
                continue
            result.append((child, meta))


def find_undeclared_plugins_in_archive(
    zip_data: bytes,
    root_path: Path,
    root_metadata: IDAMetadataDescriptor,
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Find manifests in the archive that are not part of the declared component tree.

    Returns (path, metadata) pairs for each undeclared plugin.
    """
    all_items = list(get_metadatas_with_paths_from_plugin_archive(zip_data))

    try:
        tree = walk_component_tree_from_archive(zip_data, root_path, root_metadata)
    except ValueError:
        tree = []
    declared_paths = {root_path}
    declared_paths.update(path for path, _ in tree)

    undeclared = []
    for path, meta in all_items:
        if path not in declared_paths:
            undeclared.append((path, meta))
    return undeclared


def _read_metadata_from_directory(plugin_dir: Path) -> IDAMetadataDescriptor:
    metadata_file = plugin_dir / "ida-plugin.json"
    if not metadata_file.exists():
        raise ValueError(f"ida-plugin.json not found in {plugin_dir}")
    return IDAMetadataDescriptor.model_validate_json(metadata_file.read_text(encoding="utf-8"))
