"""Pure planning of recursive plugin installations.

The planner turns explicit root requests plus expanded repository metadata and
a snapshot of the installed state into an ``InstallPlan``. It never publishes
plugin directories, changes editable links, mutates Python, or prompts. The
only I/O it performs is reading installed plugin directories to expand their
metadata and, for historical index entries that are not fully expanded,
fetching that one archive so its contents can be inspected.

Selection follows a single required-edge traversal with a selected-node table.
A node's identity and version are fixed on first visit; later edges validate
that selection and never change it. There is no backtracking or solver.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from hcli.lib.ida import IDAConfigJson
from hcli.lib.ida.plugin import (
    DependencySpec,
    IDAMetadataDescriptor,
    PluginMetadata,
    PluginSettingDescriptor,
    get_metadata_from_plugin_archive,
    get_metadata_path_from_plugin_archive,
    is_expanded_for_planning,
    is_ida_version_compatible,
    iter_dependency_specs,
    iter_expanded_components,
    parse_plugin_version,
)
from hcli.lib.ida.plugin.exceptions import (
    AmbiguousPluginReferenceError,
    DependencyConflictError,
    DependencyResolutionError,
    DependencyTargetsComponentError,
    DependencyUnavailableError,
    IDAVersionIncompatibleError,
    IncompleteMetadataError,
    InstalledPluginNameConflictError,
    PlatformIncompatibleError,
    PluginAccessDeniedError,
    PluginAlreadyInstalledError,
    PluginNotInstalledError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.install import InstalledPluginRecord, find_installed_plugin_in
from hcli.lib.ida.plugin.reference import PluginReference, normalize_plugin_host
from hcli.lib.ida.plugin.repo import BasePluginRepo, PluginArchiveLocation

logger = logging.getLogger(__name__)

MAX_PLAN_NODES = 1000

PlanOperation = Literal["retain", "install", "upgrade", "editable"]
DiagnosticKind = Literal["selected", "satisfied", "retained-newer", "unavailable", "conflict", "parent-unavailable"]


@dataclass(frozen=True)
class PluginIdentity:
    """Normalized plugin identity: lowercase name and normalized host."""

    name: str
    host: str

    @classmethod
    def from_metadata(cls, metadata: IDAMetadataDescriptor) -> PluginIdentity:
        return cls(metadata.plugin.name.lower(), normalize_plugin_host(metadata.plugin.host))

    def __str__(self) -> str:
        return f"{self.name}@{self.host}"


@dataclass(frozen=True)
class LocationSource:
    """An exact repository artifact selected from an index."""

    location: PluginArchiveLocation
    repo: BasePluginRepo
    repo_name: str | None = None
    artifact_sha256: str | None = None

    @property
    def url(self) -> str:
        return self.location.url


@dataclass(frozen=True)
class InstalledSource:
    """A plugin retained from the installed state."""

    path: Path


@dataclass(frozen=True)
class LocalArchiveSource:
    """A local archive supplied by the caller; bytes live in the artifact cache."""

    sha256: str
    manifest_path: Path


@dataclass(frozen=True)
class EditableSource:
    """A source directory to be linked into the plugins directory."""

    directory: Path


NodeSource = LocationSource | InstalledSource | LocalArchiveSource | EditableSource


@dataclass(frozen=True)
class DependencyEdge:
    """One declared requirement, with the chain that leads to its declaring node.

    ``parent`` is ``None`` for an explicit root request. ``chain`` lists display
    labels from the root down to the declaring node or component, such as
    ``("suite", "suite/comp-a")``.
    """

    parent: PluginIdentity | None
    spec: DependencySpec
    chain: tuple[str, ...]

    @property
    def required(self) -> bool:
        return self.spec.required

    @property
    def reference(self) -> PluginReference:
        return self.spec.reference

    def describe(self) -> str:
        kind = "required" if self.required else "optional"
        if not self.chain:
            return f"{self.spec.plugin} (explicit root)"
        return f"{self.spec.plugin} ({kind}, declared by {' -> '.join(self.chain)})"


@dataclass(frozen=True)
class EdgeDiagnostic:
    edge: DependencyEdge
    target: PluginIdentity | None
    kind: DiagnosticKind
    message: str


@dataclass
class PlannedNode:
    """A selected plugin and how it will be brought to the planned state."""

    identity: PluginIdentity
    metadata: IDAMetadataDescriptor
    source: NodeSource
    operation: PlanOperation
    installed: InstalledPluginRecord | None
    selected_by: DependencyEdge
    is_root: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.metadata.plugin.name

    @property
    def version(self) -> str:
        return self.metadata.plugin.version

    @property
    def chain(self) -> tuple[str, ...]:
        return (*self.selected_by.chain, self.name)

    @property
    def mutates(self) -> bool:
        return self.operation != "retain"

    def iter_manifests(self) -> Iterator[tuple[tuple[str, ...], IDAMetadataDescriptor]]:
        """Yield the root manifest and every embedded component with its path."""
        yield (), self.metadata
        yield from iter_expanded_components(self.metadata)

    def component_names(self) -> list[str]:
        return [descriptor.plugin.name for _, descriptor in iter_expanded_components(self.metadata)]

    def python_requirements(self) -> list[str]:
        requirements: list[str] = []
        for _, descriptor in self.iter_manifests():
            deps = descriptor.plugin.python_dependencies
            assert isinstance(deps, list)
            requirements.extend(deps)
        return requirements


@dataclass(frozen=True)
class ConfigurationRequirement:
    """A setting that must have a value before the owning node can be installed."""

    plugin_name: str
    key: str
    descriptor: PluginSettingDescriptor
    chain: tuple[str, ...]
    component_path: tuple[str, ...]
    is_root: bool
    branch: int | None
    reason: Literal["missing", "invalid"]
    current_value: str | bool | None = None

    def argument(self) -> str:
        flag = "--config" if self.is_root else "--dependency-config"
        if self.is_root and not self.component_path:
            return f"{flag} {self.key}=<value>"
        return f"{flag} {self.plugin_name}.{self.key}=<value>"


@dataclass
class OptionalBranch:
    """An optional edge and the required closure planned for it."""

    index: int
    edge: DependencyEdge
    prerequisite: int | None
    nodes: dict[PluginIdentity, PlannedNode] = field(default_factory=dict)
    order: list[PluginIdentity] = field(default_factory=list)
    edges: list[DependencyEdge] = field(default_factory=list)
    diagnostics: list[EdgeDiagnostic] = field(default_factory=list)
    configuration: list[ConfigurationRequirement] = field(default_factory=list)
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    @property
    def target(self) -> PluginIdentity | None:
        for diagnostic in self.diagnostics:
            if diagnostic.edge == self.edge:
                return diagnostic.target
        return None


@dataclass
class _SettingsTarget:
    display_name: str
    metadata: PluginMetadata
    branch: int | None


@dataclass
class InstallPlan:
    """The frozen required closure plus independently planned optional branches."""

    roots: list[PluginIdentity]
    nodes: dict[PluginIdentity, PlannedNode]
    order: list[PluginIdentity]
    edges: list[DependencyEdge]
    diagnostics: list[EdgeDiagnostic]
    optional_branches: list[OptionalBranch]
    configuration: list[ConfigurationRequirement]
    warnings: list[str] = field(default_factory=list)
    configuration_values: dict[tuple[str, str], str | bool] = field(default_factory=dict)
    _settings_targets: dict[str, list[_SettingsTarget]] = field(default_factory=dict, repr=False)

    def ordered_nodes(self) -> list[PlannedNode]:
        return [self.nodes[identity] for identity in self.order]

    def node_for_name(self, name: str) -> PlannedNode | None:
        wanted = name.lower()
        for node in self.nodes.values():
            if node.identity.name == wanted:
                return node
        return None

    def combined_python_requirements(self, branches: Iterable[int] = ()) -> list[str]:
        """Python requirements of every planned node, plus those of the given branches.

        Retained nodes contribute too so a combined pip check sees constraints
        from everything that will be present after the operation.
        """
        seen: dict[str, None] = {}
        for node in self.ordered_nodes():
            for requirement in node.python_requirements():
                seen.setdefault(requirement, None)
        for index in branches:
            branch = self.optional_branches[index]
            for identity in branch.order:
                for requirement in branch.nodes[identity].python_requirements():
                    seen.setdefault(requirement, None)
        return list(seen)

    def missing_configuration(self, branches: Iterable[int] = ()) -> list[ConfigurationRequirement]:
        """Requirements that still have no value after ``apply_configuration_values``."""
        requirements = list(self.configuration)
        for index in branches:
            requirements.extend(self.optional_branches[index].configuration)
        return [r for r in requirements if (r.plugin_name, r.key) not in self.configuration_values]

    def apply_configuration_values(self, values: Mapping[tuple[str, str], str | bool]) -> None:
        """Validate and record setting values keyed by ``(plugin_or_component, key)``.

        String values for boolean settings are parsed. Names are resolved
        case-insensitively against every plugin and component in the plan,
        including optional branches.

        Raises:
            ValueError: for an unknown target, unknown key, or invalid value.
        """
        from hcli.lib.ida.plugin.settings import parse_setting_value

        for (target_name, key), raw in values.items():
            targets = self._settings_targets.get(target_name.lower())
            if not targets:
                raise ValueError(f"unknown plugin or component in configuration: {target_name!r}")
            target = targets[0]
            try:
                descriptor = target.metadata.get_setting(key)
            except KeyError as e:
                raise ValueError(f"unknown setting for {target.display_name}: {key!r}") from e
            value = parse_setting_value(descriptor, raw) if isinstance(raw, str) else raw
            try:
                descriptor.validate_value(value)
            except ValueError as e:
                raise ValueError(f"invalid value for {target.display_name}.{key}: {e}") from e
            self.configuration_values[(target.display_name, key)] = value

    @property
    def mutating_nodes(self) -> list[PlannedNode]:
        return [node for node in self.ordered_nodes() if node.mutates]


class PhysicalArtifactCache:
    """Verified archive bytes keyed by digest, shared between planning and execution."""

    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}

    def store(self, zip_data: bytes) -> str:
        sha256 = hashlib.sha256(zip_data).hexdigest()
        self._artifacts.setdefault(sha256, zip_data)
        return sha256

    def get(self, sha256: str) -> bytes:
        """Raises:
        KeyError: when no artifact with that digest was stored.
        """
        return self._artifacts[sha256]

    def __contains__(self, sha256: object) -> bool:
        return sha256 in self._artifacts


@dataclass
class ResolutionContext:
    """Everything the planner reads. Built once per operation and never re-detected."""

    current_platform: str
    current_version: str
    installed: list[InstalledPluginRecord]
    installed_config: IDAConfigJson
    dependency_repo: BasePluginRepo | None
    artifacts: PhysicalArtifactCache = field(default_factory=PhysicalArtifactCache)
    index_only: bool = False
    max_nodes: int = MAX_PLAN_NODES
    _expanded_installed: dict[Path, IDAMetadataDescriptor] = field(default_factory=dict, repr=False)
    _installed_component_owners: dict[str, InstalledPluginRecord] | None = field(default=None, repr=False)

    @classmethod
    def from_environment(
        cls,
        dependency_repo: BasePluginRepo | None,
        *,
        index_only: bool = False,
        max_nodes: int = MAX_PLAN_NODES,
    ) -> ResolutionContext:
        """Snapshot the current IDA platform, version, installed plugins, and config."""
        from hcli.lib.ida import find_current_ida_platform, find_current_ida_version, get_ida_config
        from hcli.lib.ida.plugin.install import get_installed_plugin_records

        return cls(
            current_platform=find_current_ida_platform(),
            current_version=find_current_ida_version(),
            installed=get_installed_plugin_records(),
            installed_config=get_ida_config(),
            dependency_repo=dependency_repo,
            index_only=index_only,
            max_nodes=max_nodes,
        )

    def find_installed(self, name: str) -> InstalledPluginRecord | None:
        return find_installed_plugin_in(self.installed, name)

    def expand_installed(self, record: InstalledPluginRecord) -> IDAMetadataDescriptor:
        """Expanded metadata for an installed plugin, read from disk once.

        Raises:
            DependencyResolutionError: when the installed tree cannot be expanded.
        """
        from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

        cached = self._expanded_installed.get(record.path)
        if cached is not None:
            return cached
        try:
            expanded = expand_metadata_from_directory(record.path)
        except ValueError as e:
            raise DependencyResolutionError(f"installed plugin '{record.name}' at {record.path} is broken: {e}") from e
        self._expanded_installed[record.path] = expanded
        return expanded

    def installed_component_owner(self, name: str) -> InstalledPluginRecord | None:
        """The installed suite owning component ``name`` at any depth, if any."""
        if self._installed_component_owners is None:
            owners: dict[str, InstalledPluginRecord] = {}
            for record in self.installed:
                if not record.metadata.plugin.components:
                    continue
                try:
                    expanded = self.expand_installed(record)
                except DependencyResolutionError as e:
                    logger.debug("skipping component scan of %s: %s", record.name, e)
                    continue
                for _, component in iter_expanded_components(expanded):
                    owners.setdefault(component.plugin.name.lower(), record)
            self._installed_component_owners = owners
        return self._installed_component_owners.get(name.lower())

    def stored_setting(self, plugin_name: str, key: str) -> tuple[bool, str | bool | None]:
        plugin_config = self.installed_config.plugins.get(plugin_name)
        if plugin_config is None or key not in plugin_config.settings:
            return False, None
        return True, plugin_config.settings[key]


@dataclass(frozen=True)
class RepositoryRoot:
    """Install ``reference`` selected from ``repo``.

    ``upgrade`` allows replacing a lower installed version and repairs the
    dependencies of an already-current one; without it an installed plugin is
    an error.
    """

    reference: PluginReference
    repo: BasePluginRepo
    repo_name: str | None = None
    upgrade: bool = False


@dataclass(frozen=True)
class LocationRoot:
    """Install an exact, already selected archive location."""

    location: PluginArchiveLocation
    repo: BasePluginRepo
    repo_name: str | None = None
    upgrade: bool = False


@dataclass(frozen=True)
class ArchiveRoot:
    """Install from local archive bytes; ``plugin_name`` picks a root in a multi-plugin archive."""

    zip_data: bytes
    plugin_name: str | None = None
    upgrade: bool = False


@dataclass(frozen=True)
class EditableRoot:
    """Link a source directory as an editable install, replacing any installed copy."""

    directory: Path


@dataclass(frozen=True)
class InstalledRoot:
    """Retain an installed plugin and repair its dependency closure."""

    name: str


RootRequest = RepositoryRoot | LocationRoot | ArchiveRoot | EditableRoot | InstalledRoot


class _Graph:
    """Selected-node table with an optional frozen parent layer."""

    def __init__(self, parent: _Graph | None = None):
        self.parent = parent
        self.nodes: dict[PluginIdentity, PlannedNode] = {}
        self.order: list[PluginIdentity] = []
        self.visiting: set[PluginIdentity] = set()
        self._by_name: dict[str, PluginIdentity] = {}
        self._components: dict[str, PluginIdentity] = {}

    def get(self, identity: PluginIdentity) -> PlannedNode | None:
        node = self.nodes.get(identity)
        if node is None and self.parent is not None:
            return self.parent.get(identity)
        return node

    def find_by_name(self, name: str) -> PlannedNode | None:
        identity = self._by_name.get(name.lower())
        if identity is not None:
            return self.nodes[identity]
        if self.parent is not None:
            return self.parent.find_by_name(name)
        return None

    def component_owner(self, name: str) -> PlannedNode | None:
        identity = self._components.get(name.lower())
        if identity is not None:
            return self.get(identity)
        if self.parent is not None:
            return self.parent.component_owner(name)
        return None

    def is_frozen(self, identity: PluginIdentity) -> bool:
        return self.parent is not None and self.parent.get(identity) is not None

    def total_size(self) -> int:
        size = len(self.nodes)
        if self.parent is not None:
            size += self.parent.total_size()
        return size

    def add(self, node: PlannedNode) -> None:
        self.nodes[node.identity] = node
        self._by_name[node.identity.name] = node.identity
        for component_name in node.component_names():
            self._components[component_name.lower()] = node.identity


@dataclass
class _Frame:
    node: PlannedNode
    pending: list[tuple[tuple[str, ...], DependencySpec]]


@dataclass
class _Closure:
    edges: list[DependencyEdge] = field(default_factory=list)
    diagnostics: list[EdgeDiagnostic] = field(default_factory=list)
    optional_edges: list[DependencyEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _pinned_version(reference: PluginReference) -> str | None:
    if not reference.version_spec:
        return None
    return reference.version_spec[len("==") :]


def _label(node_name: str, path: tuple[str, ...]) -> str:
    return "/".join((node_name, *path))


class _Planner:
    def __init__(self, context: ResolutionContext):
        self.context = context

    def _check_compatible(self, metadata: IDAMetadataDescriptor) -> None:
        platforms = metadata.plugin.platforms
        if self.context.current_platform not in platforms:
            raise PlatformIncompatibleError(self.context.current_platform, platforms)
        if metadata.plugin.ida_versions and not is_ida_version_compatible(
            self.context.current_version, metadata.plugin.ida_versions
        ):
            raise IDAVersionIncompatibleError(self.context.current_version, metadata.plugin.ida_versions)

    def _find_location(
        self,
        repo: BasePluginRepo | None,
        reference: PluginReference,
        host: str | None,
        edge: DependencyEdge,
    ) -> tuple[PluginArchiveLocation, BasePluginRepo]:
        spec = reference.name + reference.version_spec
        if repo is None:
            raise DependencyUnavailableError(edge.spec.plugin, "no plugin repository is available", edge.chain)
        try:
            location = repo.find_compatible_plugin_from_spec(
                spec, self.context.current_platform, self.context.current_version, host=host
            )
        except KeyError:
            reason = (
                f"no version compatible with {self.context.current_platform} and IDA "
                f"{self.context.current_version} was found in the allowed repositories"
            )
            notes = getattr(repo, "notes", None)
            if callable(notes):
                extra = notes()
                if extra:
                    reason += "; " + "; ".join(extra)
            raise DependencyUnavailableError(edge.spec.plugin, reason, edge.chain) from None
        except AmbiguousPluginReferenceError as e:
            candidates = ", ".join(f"{name}@{chost}" for name, chost in e.candidates)
            raise DependencyUnavailableError(
                edge.spec.plugin,
                f"the name matches several plugins ({candidates}); qualify it with @host",
                edge.chain,
            ) from e
        except PluginAccessDeniedError as e:
            raise DependencyUnavailableError(edge.spec.plugin, str(e), edge.chain) from e
        except (httpx.HTTPError, OSError) as e:
            raise DependencyUnavailableError(edge.spec.plugin, f"repository error: {e}", edge.chain) from e
        return location, repo

    def _ensure_expanded(
        self, location: PluginArchiveLocation, repo: BasePluginRepo, edge: DependencyEdge
    ) -> tuple[IDAMetadataDescriptor, str | None]:
        """Expanded metadata for ``location``, fetching the archive once when the index is incomplete."""
        metadata = location.metadata
        if is_expanded_for_planning(metadata):
            return metadata, None
        name, version = metadata.plugin.name, metadata.plugin.version
        if self.context.index_only:
            raise IncompleteMetadataError(name, version, location.url, "index-only planning was requested")
        from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

        try:
            buf = repo.fetch_location(location)
        except (ValueError, OSError, httpx.HTTPError, PluginAccessDeniedError) as e:
            raise DependencyUnavailableError(
                edge.spec.plugin, f"failed to fetch {location.url}: {e}", edge.chain
            ) from e
        sha256 = self.context.artifacts.store(buf)
        try:
            manifest_path = get_metadata_path_from_plugin_archive(buf, name)
            expanded = expand_metadata_from_archive(buf, manifest_path)
        except ValueError as e:
            raise IncompleteMetadataError(name, version, location.url, str(e)) from e
        return expanded, sha256

    def _check_collisions(self, graph: _Graph, node: PlannedNode) -> None:
        """Reject a node whose name or components collide with anything installed or planned."""
        replaced = {node.identity}
        if node.installed is not None:
            replaced.add(PluginIdentity.from_metadata(node.installed.metadata))

        def installed_conflict(record: InstalledPluginRecord | None) -> InstalledPluginRecord | None:
            if record is None:
                return None
            identity = PluginIdentity.from_metadata(record.metadata)
            if identity in replaced or graph.get(identity) is not None:
                return None
            return record

        owner = graph.component_owner(node.name)
        if owner is not None and owner.identity not in replaced:
            raise DependencyConflictError(
                node.name,
                first=f"component '{node.name}' of planned suite '{owner.name}'",
                second=node.selected_by.describe(),
                reason="a plugin cannot share the name of a suite component",
            )
        installed_owner = installed_conflict(self.context.installed_component_owner(node.name))
        if installed_owner is not None:
            raise DependencyConflictError(
                node.name,
                first=f"component '{node.name}' of installed suite '{installed_owner.name}'",
                second=node.selected_by.describe(),
                reason="a plugin cannot share the name of an installed suite component",
            )

        for component_name in node.component_names():
            planned = graph.find_by_name(component_name)
            if planned is not None and planned.identity not in replaced:
                raise DependencyConflictError(
                    component_name,
                    first=planned.selected_by.describe(),
                    second=f"component '{component_name}' of {node.selected_by.describe()}",
                    reason="a suite component cannot share the name of a planned plugin",
                )
            planned_owner = graph.component_owner(component_name)
            if planned_owner is not None and planned_owner.identity not in replaced:
                raise DependencyConflictError(
                    component_name,
                    first=f"component '{component_name}' of planned suite '{planned_owner.name}'",
                    second=f"component '{component_name}' of suite '{node.name}'",
                    reason="two planned suites declare the same component",
                )
            installed = installed_conflict(self.context.find_installed(component_name))
            if installed is not None:
                raise DependencyConflictError(
                    component_name,
                    first=f"installed plugin '{installed.name}' at {installed.path}",
                    second=f"component '{component_name}' of suite '{node.name}'",
                    reason="a suite component cannot share the name of an installed plugin",
                )
            installed_owner = installed_conflict(self.context.installed_component_owner(component_name))
            if installed_owner is not None:
                raise DependencyConflictError(
                    component_name,
                    first=f"component '{component_name}' of installed suite '{installed_owner.name}'",
                    second=f"component '{component_name}' of suite '{node.name}'",
                    reason="a suite component cannot share the name of an installed suite component",
                )

    def _check_repeat(self, graph: _Graph, existing: PlannedNode, edge: DependencyEdge, closure: _Closure) -> None:
        reference = edge.reference
        if reference.host is not None and normalize_plugin_host(reference.host) != existing.identity.host:
            raise DependencyConflictError(
                existing.name,
                first=existing.selected_by.describe(),
                second=edge.describe(),
                reason=f"selected from {existing.identity.host} but also required from {reference.host}",
            )
        pinned = _pinned_version(reference)
        note = " (cycle)" if existing.identity in graph.visiting else ""
        satisfied = EdgeDiagnostic(
            edge, existing.identity, "satisfied", f"satisfied by {existing.name} {existing.version}{note}"
        )
        if pinned is None:
            closure.diagnostics.append(satisfied)
            return
        pinned_version = parse_plugin_version(pinned)
        selected_version = parse_plugin_version(existing.version)
        if pinned_version == selected_version:
            closure.diagnostics.append(satisfied)
            return
        if pinned_version < selected_version:
            message = (
                f"{existing.name} is selected at {existing.version}, newer than pin {pinned}; keeping the newer version"
            )
            closure.diagnostics.append(EdgeDiagnostic(edge, existing.identity, "retained-newer", message))
            closure.warnings.append(message)
            return
        frozen = " (frozen by the required plan)" if graph.is_frozen(existing.identity) else ""
        raise DependencyConflictError(
            existing.name,
            first=existing.selected_by.describe(),
            second=edge.describe(),
            reason=(
                f"already selected at {existing.version}{frozen} but a later declaration pins {pinned}; "
                f"version reselection is unsupported, use consistent pins"
            ),
        )

    def _select_dependency(self, graph: _Graph, edge: DependencyEdge) -> PlannedNode:
        """First-visit selection for a dependency edge."""
        reference = edge.reference
        installed = self.context.find_installed(reference.name)
        repo = self.context.dependency_repo
        if installed is not None:
            installed_host = normalize_plugin_host(installed.host)
            if reference.host is not None and normalize_plugin_host(reference.host) != installed_host:
                raise DependencyConflictError(
                    installed.name,
                    first=f"installed plugin '{installed.name}@{installed_host}' at {installed.path}",
                    second=edge.describe(),
                    reason="the installed plugin comes from a different host and only one plugin can own the name",
                )
            identity = PluginIdentity.from_metadata(installed.metadata)
            pinned = _pinned_version(reference)
            warnings: list[str] = []
            if pinned is not None and parse_plugin_version(pinned) > parse_plugin_version(installed.version):
                location, owner = self._find_location(repo, reference, installed_host, edge)
                metadata, sha256 = self._ensure_expanded(location, owner, edge)
                source = LocationSource(location, owner, owner.describe_location_source(location), sha256)
                return PlannedNode(identity, metadata, source, "upgrade", installed, edge)
            if pinned is not None and parse_plugin_version(pinned) < parse_plugin_version(installed.version):
                warnings.append(
                    f"{installed.name} is installed at {installed.version}, newer than pin {pinned}; not downgrading"
                )
            metadata = self.context.expand_installed(installed)
            return PlannedNode(
                identity, metadata, InstalledSource(installed.path), "retain", installed, edge, warnings=warnings
            )

        location, owner = self._find_location(repo, reference, reference.host, edge)
        metadata, sha256 = self._ensure_expanded(location, owner, edge)
        source = LocationSource(location, owner, owner.describe_location_source(location), sha256)
        return PlannedNode(PluginIdentity.from_metadata(metadata), metadata, source, "install", None, edge)

    def _visit_edge(
        self,
        graph: _Graph,
        edge: DependencyEdge,
        closure: _Closure,
        stack: list[_Frame],
        select: Callable[[_Graph, DependencyEdge], PlannedNode],
    ) -> None:
        reference = edge.reference
        owner = graph.component_owner(reference.name)
        if owner is not None:
            raise DependencyTargetsComponentError(edge.spec.plugin, reference.name, owner.name, edge.chain)
        installed_owner = self.context.installed_component_owner(reference.name)
        if installed_owner is not None and graph.get(PluginIdentity.from_metadata(installed_owner.metadata)) is None:
            raise DependencyTargetsComponentError(edge.spec.plugin, reference.name, installed_owner.name, edge.chain)

        existing = graph.find_by_name(reference.name)
        if existing is not None:
            self._check_repeat(graph, existing, edge, closure)
            return

        if graph.total_size() >= self.context.max_nodes:
            raise DependencyResolutionError(f"dependency graph exceeds {self.context.max_nodes} plugins")

        node = select(graph, edge)
        self._check_collisions(graph, node)
        graph.add(node)
        graph.visiting.add(node.identity)
        closure.warnings.extend(node.warnings)
        closure.diagnostics.append(
            EdgeDiagnostic(edge, node.identity, "selected", f"{node.operation} {node.name} {node.version}")
        )
        stack.append(_Frame(node, list(iter_dependency_specs(node.metadata))))

    def _drain(self, graph: _Graph, closure: _Closure, stack: list[_Frame]) -> None:
        while stack:
            frame = stack[-1]
            if not frame.pending:
                graph.visiting.discard(frame.node.identity)
                graph.order.append(frame.node.identity)
                stack.pop()
                continue
            path, spec = frame.pending.pop(0)
            chain = (*frame.node.selected_by.chain, _label(frame.node.name, path))
            edge = DependencyEdge(frame.node.identity, spec, chain)
            closure.edges.append(edge)
            if not spec.required:
                closure.optional_edges.append(edge)
                continue
            self._visit_edge(graph, edge, closure, stack, self._select_dependency)

    def resolve_closure(
        self,
        graph: _Graph,
        edges: Sequence[tuple[DependencyEdge, Callable[[_Graph, DependencyEdge], PlannedNode]]],
    ) -> _Closure:
        closure = _Closure()
        stack: list[_Frame] = []
        for edge, select in edges:
            closure.edges.append(edge)
            self._visit_edge(graph, edge, closure, stack, select)
            self._drain(graph, closure, stack)
        return closure

    def _root_installed_state(
        self,
        request_upgrade: bool,
        installed: InstalledPluginRecord | None,
        metadata: IDAMetadataDescriptor,
        host: str | None,
        pinned: bool,
    ) -> tuple[PlanOperation, list[str]]:
        if installed is None:
            return "install", []
        installed_host = normalize_plugin_host(installed.host)
        new_host = normalize_plugin_host(metadata.plugin.host)
        if installed_host != new_host or (host is not None and normalize_plugin_host(host) != installed_host):
            raise InstalledPluginNameConflictError(
                metadata.plugin.name, metadata.plugin.host, installed.name, installed.host, installed.path
            )
        if not request_upgrade:
            raise PluginAlreadyInstalledError(installed.name, installed.path)
        selected = parse_plugin_version(metadata.plugin.version)
        current = parse_plugin_version(installed.version)
        if selected > current:
            return "upgrade", []
        if selected == current:
            return "retain", []
        if pinned:
            raise PluginVersionDowngradeError(installed.name, installed.version, metadata.plugin.version)
        return "retain", [
            f"{installed.name} is installed at {installed.version}, newer than the available {metadata.plugin.version}; keeping it"
        ]

    def _select_root(self, request: RootRequest) -> Callable[[_Graph, DependencyEdge], PlannedNode]:
        def select(graph: _Graph, edge: DependencyEdge) -> PlannedNode:
            if isinstance(request, EditableRoot):
                from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

                directory = request.directory.resolve()
                metadata = expand_metadata_from_directory(directory)
                self._check_compatible(metadata)
                installed = self.context.find_installed(metadata.plugin.name)
                if installed is not None and normalize_plugin_host(installed.host) != normalize_plugin_host(
                    metadata.plugin.host
                ):
                    raise InstalledPluginNameConflictError(
                        metadata.plugin.name, metadata.plugin.host, installed.name, installed.host, installed.path
                    )
                return PlannedNode(
                    PluginIdentity.from_metadata(metadata),
                    metadata,
                    EditableSource(directory),
                    "editable",
                    installed,
                    edge,
                    is_root=True,
                )

            if isinstance(request, InstalledRoot):
                installed = self.context.find_installed(request.name)
                if installed is None:
                    raise PluginNotInstalledError(request.name)
                metadata = self.context.expand_installed(installed)
                return PlannedNode(
                    PluginIdentity.from_metadata(metadata),
                    metadata,
                    InstalledSource(installed.path),
                    "retain",
                    installed,
                    edge,
                    is_root=True,
                )

            if isinstance(request, ArchiveRoot):
                from hcli.lib.ida.plugin.components import find_root_manifest_in_archive
                from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

                if request.plugin_name is not None:
                    manifest_path = get_metadata_path_from_plugin_archive(request.zip_data, request.plugin_name)
                else:
                    manifest_path, _ = find_root_manifest_in_archive(request.zip_data)
                metadata = expand_metadata_from_archive(request.zip_data, manifest_path)
                self._check_compatible(metadata)
                installed = self.context.find_installed(metadata.plugin.name)
                operation, warnings = self._root_installed_state(request.upgrade, installed, metadata, None, True)
                archive_sha256 = self.context.artifacts.store(request.zip_data)
                return PlannedNode(
                    PluginIdentity.from_metadata(metadata),
                    metadata,
                    LocalArchiveSource(archive_sha256, manifest_path),
                    operation,
                    installed,
                    edge,
                    is_root=True,
                    warnings=warnings,
                )

            if isinstance(request, LocationRoot):
                location, repo, repo_name = request.location, request.repo, request.repo_name
                reference = edge.reference
            else:
                reference = request.reference
                repo_name = request.repo_name
                installed = self.context.find_installed(reference.name)
                host = reference.host
                if installed is not None:
                    installed_host = normalize_plugin_host(installed.host)
                    if host is not None and normalize_plugin_host(host) != installed_host:
                        raise InstalledPluginNameConflictError(
                            reference.name, host, installed.name, installed.host, installed.path
                        )
                    host = installed_host
                location, repo = self._find_location(request.repo, reference, host, edge)

            metadata, sha256 = self._ensure_expanded(location, repo, edge)
            installed = self.context.find_installed(metadata.plugin.name)
            operation, warnings = self._root_installed_state(
                request.upgrade, installed, metadata, reference.host, bool(reference.version_spec)
            )
            if repo_name is None:
                repo_name = repo.describe_location_source(location)
            return PlannedNode(
                PluginIdentity.from_metadata(metadata),
                metadata,
                LocationSource(location, repo, repo_name, sha256),
                operation,
                installed,
                edge,
                is_root=True,
                warnings=warnings,
            )

        return select

    def _root_edge(self, request: RootRequest) -> DependencyEdge:
        """The synthetic edge for a root request; local roots are named from their manifest."""
        if isinstance(request, RepositoryRoot):
            reference = request.reference
            spec = reference.name + reference.version_spec
            if reference.host:
                spec += f"@{reference.host}"
            return DependencyEdge(None, DependencySpec(plugin=spec), ())

        if isinstance(request, LocationRoot):
            plugin = request.location.metadata.plugin
        elif isinstance(request, ArchiveRoot):
            from hcli.lib.ida.plugin.components import find_root_manifest_in_archive

            if request.plugin_name is not None:
                _, descriptor = get_metadata_from_plugin_archive(request.zip_data, request.plugin_name)
            else:
                _, descriptor = find_root_manifest_in_archive(request.zip_data)
            plugin = descriptor.plugin
        elif isinstance(request, InstalledRoot):
            installed = self.context.find_installed(request.name)
            if installed is None:
                raise PluginNotInstalledError(request.name)
            plugin = installed.metadata.plugin
        else:
            from hcli.lib.ida.plugin.install import get_metadata_from_plugin_directory

            plugin = get_metadata_from_plugin_directory(request.directory).plugin
        return DependencyEdge(None, DependencySpec(plugin=f"{plugin.name}=={plugin.version}@{plugin.host}"), ())

    def _configuration_for(self, node: PlannedNode, branch: int | None) -> list[ConfigurationRequirement]:
        if not node.mutates:
            return []
        requirements: list[ConfigurationRequirement] = []
        for path, descriptor in node.iter_manifests():
            plugin_name = descriptor.plugin.name
            chain = (*node.selected_by.chain, _label(node.name, path))
            for setting in descriptor.plugin.settings:
                present, value = self.context.stored_setting(plugin_name, setting.key)
                if present:
                    assert value is not None
                    try:
                        setting.validate_value(value)
                    except ValueError:
                        requirements.append(
                            ConfigurationRequirement(
                                plugin_name, setting.key, setting, chain, path, node.is_root, branch, "invalid", value
                            )
                        )
                    continue
                if setting.required and setting.default is None:
                    requirements.append(
                        ConfigurationRequirement(
                            plugin_name, setting.key, setting, chain, path, node.is_root, branch, "missing"
                        )
                    )
        return requirements

    def plan(self, roots: Sequence[RootRequest]) -> InstallPlan:
        graph = _Graph()
        root_edges = [(self._root_edge(request), self._select_root(request)) for request in roots]
        closure = self.resolve_closure(graph, root_edges)

        root_identities: list[PluginIdentity] = []
        for diagnostic in closure.diagnostics:
            if diagnostic.edge.parent is None and diagnostic.target is not None:
                root_identities.append(diagnostic.target)

        configuration: list[ConfigurationRequirement] = []
        for identity in graph.order:
            configuration.extend(self._configuration_for(graph.nodes[identity], None))

        branches = self._plan_optional(graph, closure.optional_edges)

        targets: dict[str, list[_SettingsTarget]] = {}

        def register(node: PlannedNode, branch: int | None) -> None:
            for _, descriptor in node.iter_manifests():
                targets.setdefault(descriptor.plugin.name.lower(), []).append(
                    _SettingsTarget(descriptor.plugin.name, descriptor.plugin, branch)
                )

        for node in graph.nodes.values():
            register(node, None)
        for branch in branches:
            for node in branch.nodes.values():
                register(node, branch.index)

        return InstallPlan(
            roots=root_identities,
            nodes=dict(graph.nodes),
            order=list(graph.order),
            edges=closure.edges,
            diagnostics=closure.diagnostics,
            optional_branches=branches,
            configuration=configuration,
            warnings=closure.warnings,
            _settings_targets=targets,
        )

    def _plan_optional(self, required: _Graph, optional_edges: list[DependencyEdge]) -> list[OptionalBranch]:
        branches: list[OptionalBranch] = []
        seen: set[tuple[PluginIdentity | None, tuple[str, ...], str]] = set()
        worklist: list[tuple[DependencyEdge, _Graph, int | None]] = [(edge, required, None) for edge in optional_edges]

        while worklist:
            edge, parent_graph, prerequisite = worklist.pop(0)
            key = (edge.parent, edge.chain, edge.spec.plugin)
            if key in seen:
                continue
            seen.add(key)

            branch = OptionalBranch(len(branches), edge, prerequisite)
            branches.append(branch)

            if prerequisite is not None and not branches[prerequisite].available:
                branch.unavailable_reason = (
                    f"declaring optional dependency '{branches[prerequisite].edge.spec.plugin}' is unavailable"
                )
                branch.diagnostics.append(EdgeDiagnostic(edge, None, "parent-unavailable", branch.unavailable_reason))
                continue

            graph = _Graph(parent_graph)
            try:
                closure = self.resolve_closure(graph, [(edge, self._select_dependency)])
            except DependencyResolutionError as e:
                branch.unavailable_reason = str(e)
                kind: DiagnosticKind = "conflict" if isinstance(e, DependencyConflictError) else "unavailable"
                branch.diagnostics.append(EdgeDiagnostic(edge, None, kind, str(e)))
                continue
            except (PlatformIncompatibleError, IDAVersionIncompatibleError) as e:
                branch.unavailable_reason = str(e)
                branch.diagnostics.append(EdgeDiagnostic(edge, None, "unavailable", str(e)))
                continue

            branch.nodes = dict(graph.nodes)
            branch.order = list(graph.order)
            branch.edges = closure.edges
            branch.diagnostics = closure.diagnostics
            for identity in graph.order:
                branch.configuration.extend(self._configuration_for(graph.nodes[identity], branch.index))
            for nested in closure.optional_edges:
                worklist.append((nested, graph, branch.index))

        return branches


def plan_install(context: ResolutionContext, roots: Sequence[RootRequest]) -> InstallPlan:
    """Plan the installation of ``roots`` and their required and optional dependencies.

    The required closure of every root is resolved first and frozen. Each
    optional edge is then planned independently against that frozen plan; a
    branch that cannot be satisfied is recorded as unavailable with its cause
    rather than failing the operation.

    Raises:
        DependencyResolutionError: a required dependency is unavailable,
            conflicts with another selection, targets a suite component, or has
            incomplete published metadata.
        PluginAlreadyInstalledError: a root is installed and ``upgrade`` was not requested.
        PluginVersionDowngradeError: a pinned root is older than the installed version.
        InstalledPluginNameConflictError: a root's name is owned by a plugin from another host.
        PlatformIncompatibleError, IDAVersionIncompatibleError: a local root does not support this IDA.
        ValueError: a local archive or directory root is malformed.
    """
    return _Planner(context).plan(roots)
