"""Component tree walking and validation for tightly-coupled plugin suites."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadatas_with_paths_from_plugin_archive,
)

if TYPE_CHECKING:
    from hcli.lib.ida.plugin.install import InstalledPluginRecord

logger = logging.getLogger(__name__)

MAX_COMPONENT_DEPTH = 10


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

    for component_name in metadata.plugin.components:
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
    _all_metadatas: dict[str, tuple[Path, IDAMetadataDescriptor]] | None = None,
) -> list[tuple[Path, IDAMetadataDescriptor]]:
    """Walk a suite's component tree inside a zip archive, validating as we go.

    Returns a flat list of (archive_path, descriptor) for every component
    found at any nesting depth. The root manifest is NOT included.

    Raises:
        ValueError: on missing component, name mismatch, or depth exceeded.
    """
    if _depth >= MAX_COMPONENT_DEPTH:
        raise ValueError(f"component nesting exceeds maximum depth ({MAX_COMPONENT_DEPTH})")

    if _all_metadatas is None:
        _all_metadatas = {}
        for path, meta in get_metadatas_with_paths_from_plugin_archive(zip_data):
            _all_metadatas[meta.plugin.name] = (path, meta)

    root_dir = root_path.parent
    result: list[tuple[Path, IDAMetadataDescriptor]] = []

    for component_name in root_metadata.plugin.components:
        if component_name not in _all_metadatas:
            expected_dir = root_dir / component_name
            raise ValueError(
                f"component '{component_name}' declared by '{root_metadata.plugin.name}' "
                f"but no ida-plugin.json found under {expected_dir}"
            )

        comp_path, comp_meta = _all_metadatas[component_name]
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
        referenced_as_component.update(meta.plugin.components)

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


def check_component_name_collisions(
    component_names: set[str],
    *,
    exclude_suite: str | None = None,
) -> list[str]:
    """Check component names against all installed plugins and their components.

    Returns a list of human-readable collision descriptions.
    """
    from hcli.lib.ida.plugin.install import get_installed_plugin_records

    collisions: list[str] = []
    records = get_installed_plugin_records()

    for name in sorted(component_names):
        for record in records:
            if exclude_suite and record.name == exclude_suite:
                continue

            if record.name == name:
                collisions.append(f"'{name}' collides with installed top-level plugin '{record.name}'")
                continue

            if not record.metadata.plugin.components:
                continue

            try:
                tree = walk_component_tree_from_directory(record.path)
            except ValueError:
                continue

            for _, comp_meta in tree:
                if comp_meta.plugin.name == name:
                    collisions.append(f"'{name}' collides with a component of suite '{record.name}'")

    return collisions



def _read_metadata_from_directory(plugin_dir: Path) -> IDAMetadataDescriptor:
    metadata_file = plugin_dir / "ida-plugin.json"
    if not metadata_file.exists():
        raise ValueError(f"ida-plugin.json not found in {plugin_dir}")
    return IDAMetadataDescriptor.model_validate_json(metadata_file.read_text(encoding="utf-8"))
