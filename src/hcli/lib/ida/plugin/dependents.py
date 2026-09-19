"""Dependency relationships between installed plugins and their components."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from hcli.lib.ida.plugin import DependencySpec, IDAMetadataDescriptor, iter_dependency_specs, iter_expanded_components
from hcli.lib.ida.plugin.install import InstalledPluginRecord, find_installed_plugin_in
from hcli.lib.ida.plugin.reference import parse_dependency_spec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DependencyDeclaration:
    """One dependency declared by an installed plugin or one of its components."""

    owner: InstalledPluginRecord
    declarer: str
    spec: DependencySpec
    target: str

    @property
    def declared_by_component(self) -> bool:
        return self.declarer.lower() != self.owner.name.lower()

    def describe_declarer(self) -> str:
        if self.declared_by_component:
            return f"{self.declarer} (component of {self.owner.name})"
        return self.owner.name


def expand_installed_record(record: InstalledPluginRecord) -> IDAMetadataDescriptor:
    """Expanded metadata for ``record``, falling back to the root manifest when the tree is broken."""
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

    try:
        return expand_metadata_from_directory(record.path)
    except ValueError as e:
        logger.debug("cannot expand installed plugin %s: %s", record.name, e)
        return record.metadata


def get_owned_names(record: InstalledPluginRecord) -> set[str]:
    """Lowercase names of ``record`` and every component beneath it."""
    names = {record.name.lower()}
    names.update(component.plugin.name.lower() for _, component in iter_expanded_components(expand_installed_record(record)))
    return names


def collect_dependency_declarations(records: Iterable[InstalledPluginRecord]) -> list[DependencyDeclaration]:
    """Every dependency declared by ``records`` and their components, in record order."""
    declarations: list[DependencyDeclaration] = []
    for record in records:
        expanded = expand_installed_record(record)
        for path, spec in iter_dependency_specs(expanded):
            try:
                target = parse_dependency_spec(spec.plugin).name
            except ValueError as e:
                logger.debug("skipping malformed dependency %r of %s: %s", spec.plugin, record.name, e)
                continue
            declarer = path[-1] if path else record.name
            declarations.append(DependencyDeclaration(record, declarer, spec, target))
    return declarations


def find_dependents(records: list[InstalledPluginRecord], record: InstalledPluginRecord) -> list[DependencyDeclaration]:
    """Declarations from other installed plugins that name ``record`` or one of its components."""
    owned = get_owned_names(record)
    others = [r for r in records if r.path != record.path]
    return [d for d in collect_dependency_declarations(others) if d.target.lower() in owned]


def find_companions(records: list[InstalledPluginRecord], record: InstalledPluginRecord) -> list[InstalledPluginRecord]:
    """Installed top-level plugins that ``record`` or its components declare as dependencies."""
    owned = get_owned_names(record)
    companions: dict[str, InstalledPluginRecord] = {}
    for declaration in collect_dependency_declarations([record]):
        name = declaration.target.lower()
        if name in owned or name in companions:
            continue
        installed = find_installed_plugin_in(records, declaration.target)
        if installed is not None:
            companions[name] = installed
    return list(companions.values())


def find_remaining_declarers(records: Iterable[InstalledPluginRecord], name: str) -> list[DependencyDeclaration]:
    """Declarations among ``records`` that still name ``name``."""
    wanted = name.lower()
    return [d for d in collect_dependency_declarations(records) if d.target.lower() == wanted]
