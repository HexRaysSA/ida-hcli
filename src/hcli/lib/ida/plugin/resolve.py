"""Select plugin versions whose whole dependency closure can install on a target cell.

Resolution has two phases. First, a viability fixpoint removes every location
that has a required dependency with no remaining candidate. Then a depth-first
search picks the newest viable version of each requirement, in root order, and
backtracks only when two requirements disagree on a version.

The module is free of click and rich, does not download archives, and must not
import `hcli.lib.ida.plugin.install`.
"""

from __future__ import annotations

import functools
import logging
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import semantic_version

from hcli.lib.ida.plugin import IDAMetadataDescriptor, parse_plugin_version
from hcli.lib.ida.plugin.exceptions import AmbiguousPluginReferenceError
from hcli.lib.ida.plugin.reference import DependencyEntry, normalize_plugin_host, parse_plugin_reference
from hcli.lib.ida.plugin.repo import (
    BasePluginRepo,
    Plugin,
    PluginArchiveLocation,
    PythonVersionSource,
    get_plugin_by_name,
    is_compatible_location,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 10_000


@dataclass(frozen=True)
class Cell:
    """The environment that a resolution targets.

    Attributes:
        platform: IDA platform, such as `linux-x86_64`.
        ida_version: IDA version to filter on, or None to accept every IDA version.
        python_version: Python version to test `requiresPython` against, or a function that
            returns it. The function is called at most once, and only when a candidate declares
            `requiresPython`. None skips the Python check.
        label: text that names the cell in messages, such as a bundle target ID.
    """

    platform: str
    ida_version: str | None = None
    python_version: PythonVersionSource = None
    label: str | None = None

    def __str__(self) -> str:
        if self.label is not None:
            return self.label
        parts = [self.platform]
        if self.ida_version is not None:
            parts.append(f"IDA {self.ida_version}")
        if isinstance(self.python_version, str):
            parts.append(f"Python {self.python_version}")
        return " ".join(parts)


@functools.cache
def _get_simple_spec(version_spec: str) -> semantic_version.SimpleSpec:
    return semantic_version.SimpleSpec(version_spec or ">=0")


@dataclass(frozen=True)
class Requirement:
    """A request for one plugin.

    Attributes:
        name: plugin name, matched case-insensitively.
        version_spec: version specifier with its operator, such as `==1.2.0` or `>=1.0`, or empty for any version.
        host: normalized code repository URL that the plugin must come from, or None for any host.
        required: whether a failure to resolve this requirement fails the resolution.
    """

    name: str
    version_spec: str
    host: str | None
    required: bool = True

    @classmethod
    def from_spec(cls, spec: str, *, required: bool = True) -> Requirement:
        """Parse a positional plugin spec like `name`, `name>=1.0`, or `name==1.0@host`.

        A `repo/` prefix is accepted and dropped: the caller picks the repository.

        Raises:
            ValueError: when the spec or its version specifier cannot be parsed.
        """
        ref = parse_plugin_reference(spec)
        _get_simple_spec(ref.version_spec)
        return cls(ref.name, ref.version_spec, ref.host, required)

    @classmethod
    def from_dependency_entry(cls, entry: DependencyEntry) -> Requirement:
        ref = entry.reference
        return cls(ref.name, ref.version_spec, ref.host, entry.required)

    def __str__(self) -> str:
        host = f"@{self.host}" if self.host else ""
        return f"{self.name}{self.version_spec}{host}"

    @property
    def pin(self) -> str | None:
        """The pinned version of an `==` requirement."""
        return self.version_spec[2:] if self.version_spec.startswith("==") else None

    def matches(self, version: str) -> bool:
        return parse_plugin_version(version) in _get_simple_spec(self.version_spec)


def get_requirements(metadata: IDAMetadataDescriptor) -> list[Requirement]:
    """Collect the plugin dependencies of a plugin and of every component it contains.

    Component entries that are only names, not expanded metadata, contribute nothing.
    """
    requirements = [
        Requirement.from_dependency_entry(entry)
        for entry in metadata.plugin.dependencies
        if isinstance(entry, DependencyEntry)
    ]
    for component in metadata.plugin.components:
        if isinstance(component, IDAMetadataDescriptor):
            requirements.extend(get_requirements(component))
    return requirements


@dataclass(frozen=True)
class SkippedRequirement:
    """An optional requirement that could not be resolved.

    Attributes:
        chain: the plugins that led to the requirement, ending with the requirement itself.
    """

    requirement: Requirement
    chain: tuple[str, ...]
    reason: str


@dataclass
class Resolution:
    """The plugins selected for one cell.

    Attributes:
        selected: repository location to install, by repository plugin name. Plugins satisfied
            by a fixed or an installed plugin are not included.
        order: names in `selected`, dependencies before their dependents.
        roots: the plugin name that each root requirement resolved to, including fixed and installed plugins.
        skipped: optional requirements that could not be resolved.
        warnings: installed plugins kept although they are newer than a pin.
    """

    selected: dict[str, PluginArchiveLocation]
    order: list[str]
    roots: dict[Requirement, str]
    skipped: list[SkippedRequirement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ResolutionError(Exception):
    """No selection satisfies the requirements on a cell.

    Attributes:
        chain: the plugins that led to the failing requirement, ending with the requirement itself.
        requirements: the requirements along `chain`, from a root or a requirement of a fixed
            plugin to the failing requirement. Empty when no single requirement failed.
        reasons: why each candidate for the failing requirement was rejected.
    """

    def __init__(
        self, cell: Cell, chain: Sequence[str], requirements: Sequence[Requirement], reasons: Sequence[str]
    ) -> None:
        self.cell = cell
        self.chain = tuple(chain)
        self.requirements = tuple(requirements)
        self.reasons = tuple(reasons)
        super().__init__(f"cannot resolve {' -> '.join(self.chain)} for {cell}: {'; '.join(self.reasons)}")


class AmbiguousRequirementError(ResolutionError):
    """A requirement without a host matches plugins from more than one host.

    Attributes:
        name: the plugin name of the ambiguous requirement.
        candidates: `(name, host)` of each matching plugin, each one time.
    """

    def __init__(
        self,
        cell: Cell,
        chain: Sequence[str],
        requirements: Sequence[Requirement],
        name: str,
        candidates: Sequence[tuple[str, str]],
    ) -> None:
        self.name = name
        self.candidates = list(dict.fromkeys(candidates))
        choices = ", ".join(f"{name}@{host}" for _, host in self.candidates)
        super().__init__(cell, chain, requirements, [f"{name} is ambiguous, use one of: {choices}"])


class StepLimitError(ResolutionError):
    """The search gave up after the step limit."""


_Source = Literal["repo", "fixed", "installed"]
_PluginId = tuple[str, str]


@dataclass(frozen=True)
class _Pending:
    """A requirement to satisfy.

    Attributes:
        parents: the plugins that led to the requirement.
        ancestors: the requirements that led to the requirement.
    """

    requirement: Requirement
    parents: tuple[str, ...]
    ancestors: tuple[Requirement, ...] = ()

    @property
    def chain(self) -> tuple[str, ...]:
        return (*self.parents, str(self.requirement))

    @property
    def requirements(self) -> tuple[Requirement, ...]:
        return (*self.ancestors, self.requirement)

    def get_dependency(self, requirement: Requirement, label: str) -> _Pending:
        return _Pending(requirement, (*self.parents, label), self.requirements)


@dataclass(frozen=True)
class _Choice:
    name: str
    version: str
    host: str | None
    source: _Source
    chain: tuple[str, ...]
    constraints: tuple[_Pending, ...] = ()
    location: PluginArchiveLocation | None = None
    metadata: IDAMetadataDescriptor | None = None

    @property
    def label(self) -> str:
        return f"{self.name} {self.version}"

    def render(self) -> str:
        if self.source == "fixed":
            return f"{self.label} is given"
        if self.source == "installed":
            return f"{self.label} is installed"
        return f"{self.label} is already selected for {' -> '.join(self.chain)}"


@dataclass(frozen=True)
class _Solution:
    chosen: dict[str, _Choice]
    skipped: tuple[SkippedRequirement, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Failure:
    """A failed required requirement."""

    pending: _Pending
    reasons: tuple[str, ...]


def _get_plugin_id(plugin: Plugin) -> _PluginId:
    return plugin.name.lower(), plugin.host


def _is_same_host(a: str, b: str) -> bool:
    try:
        return normalize_plugin_host(a) == normalize_plugin_host(b)
    except ValueError:
        return a == b


class _PythonProbeError(Exception):
    """IDA's Python version cannot be detected."""


class _PythonProbe:
    """Calls a Python version function at most once, and remembers its result or its failure."""

    def __init__(self, get_python_version: Callable[[], str]) -> None:
        self.get_python_version = get_python_version
        self.version: str | None = None
        self.error: Exception | None = None

    def __call__(self) -> str:
        if self.version is None and self.error is None:
            try:
                self.version = self.get_python_version()
            except Exception as e:
                logger.debug("cannot detect IDA's Python: %s", e)
                self.error = e
        if self.error is not None:
            raise _PythonProbeError(str(self.error)) from self.error
        assert self.version is not None
        return self.version


class _Resolver:
    def __init__(
        self,
        plugins: list[Plugin],
        cell: Cell,
        fixed: Mapping[str, IDAMetadataDescriptor],
        installed: Mapping[str, str],
        max_steps: int,
    ) -> None:
        self.plugins = plugins
        self.cell = cell
        python_version = cell.python_version
        self.python_version: PythonVersionSource = (
            _PythonProbe(python_version) if callable(python_version) else python_version
        )
        self.fixed = {name.lower(): metadata for name, metadata in fixed.items()}
        self.installed = {name.lower(): (name, version) for name, version in installed.items()}
        self.max_steps = max_steps
        self.steps = 0
        self.lookups: dict[tuple[str, str | None], Plugin | None] = {}
        self.compatible: dict[_PluginId, dict[str, PluginArchiveLocation]] = {}
        self.viable: dict[_PluginId, dict[str, PluginArchiveLocation]] = {}
        self.removed: dict[tuple[_PluginId, str], Requirement] = {}

    def get_plugin(self, requirement: Requirement) -> Plugin | None:
        """Find the repository plugin that a requirement names, or None when there is none.

        Raises:
            AmbiguousPluginReferenceError: when a requirement without a host matches plugins from
                several hosts. `check_reachable` finds these before the search starts.
        """
        key = (requirement.name.lower(), requirement.host)
        if key not in self.lookups:
            try:
                self.lookups[key] = get_plugin_by_name(self.plugins, requirement.name, host=requirement.host)
            except KeyError:
                self.lookups[key] = None
        return self.lookups[key]

    def get_compatible(self, plugin: Plugin) -> dict[str, PluginArchiveLocation]:
        """Map each version to its first location that is compatible with the cell, newest version first."""
        compatible: dict[str, PluginArchiveLocation] = {}
        for version in sorted(plugin.versions, key=parse_plugin_version, reverse=True):
            for location in plugin.versions[version]:
                if self.is_compatible(location):
                    compatible[version] = location
                    break
        return compatible

    def is_compatible(self, location: PluginArchiveLocation) -> bool:
        """Whether a location can install on the cell.

        A location that declares `requiresPython` is not compatible when IDA's Python cannot be detected.
        """
        try:
            return is_compatible_location(location, self.cell.platform, self.cell.ida_version, self.python_version)
        except _PythonProbeError:
            return False

    def check_reachable(self, pending: Sequence[_Pending]) -> None:
        """Find the plugins that the requirements can reach through compatible locations, breadth first.

        A requirement that a fixed plugin or an installed version satisfies does not reach the repository.

        Raises:
            AmbiguousRequirementError: when a reachable requirement, required or optional, has no
                host and matches plugins from several hosts.
        """
        queue = deque(pending)
        while queue:
            item = queue.popleft()
            requirement = item.requirement
            key = requirement.name.lower()
            if key in self.fixed:
                continue
            if key in self.installed and self.satisfies(self.get_installed_choice(requirement, ()), requirement)[0]:
                continue
            try:
                plugin = self.get_plugin(requirement)
            except AmbiguousPluginReferenceError as e:
                raise AmbiguousRequirementError(
                    self.cell, item.chain, item.requirements, requirement.name, e.candidates
                ) from None
            if plugin is None or _get_plugin_id(plugin) in self.compatible:
                continue
            compatible = self.get_compatible(plugin)
            self.compatible[_get_plugin_id(plugin)] = compatible
            for version, location in compatible.items():
                label = f"{plugin.name} {version}"
                queue.extend(item.get_dependency(dep, label) for dep in get_requirements(location.metadata))

    def compute_viability(self) -> None:
        """Remove the locations of reachable plugins until every remaining location is viable."""
        self.viable = {plugin_id: dict(versions) for plugin_id, versions in self.compatible.items()}
        changed = True
        while changed:
            changed = False
            for plugin_id, versions in self.viable.items():
                for version, location in list(versions.items()):
                    for requirement in get_requirements(location.metadata):
                        if requirement.required and not self.has_candidate(requirement):
                            self.removed[(plugin_id, version)] = requirement
                            del versions[version]
                            changed = True
                            break

    def has_candidate(self, requirement: Requirement) -> bool:
        key = requirement.name.lower()
        if key in self.fixed:
            return self.satisfies(self.get_fixed_choice(key), requirement)[0]
        if key in self.installed and self.satisfies(self.get_installed_choice(requirement, ()), requirement)[0]:
            return True
        plugin = self.get_plugin(requirement)
        if plugin is None:
            return False
        return any(requirement.matches(version) for version in self.viable[_get_plugin_id(plugin)])

    def get_fixed_choice(self, key: str) -> _Choice:
        metadata = self.fixed[key]
        return _Choice(
            name=metadata.plugin.name,
            version=metadata.plugin.version,
            host=metadata.plugin.host,
            source="fixed",
            chain=(metadata.plugin.name,),
            metadata=metadata,
        )

    def get_installed_choice(self, requirement: Requirement, chain: tuple[str, ...]) -> _Choice:
        name, version = self.installed[requirement.name.lower()]
        return _Choice(name=name, version=version, host=None, source="installed", chain=chain)

    def satisfies(self, choice: _Choice, requirement: Requirement) -> tuple[bool, str | None]:
        """Whether a chosen plugin satisfies a requirement, and a warning when it does so only by keeping a newer version."""
        if requirement.host and choice.host and not _is_same_host(requirement.host, choice.host):
            return False, None
        if requirement.matches(choice.version):
            return True, None
        pin = requirement.pin
        if (
            choice.source == "installed"
            and pin is not None
            and parse_plugin_version(pin) < parse_plugin_version(choice.version)
        ):
            return True, f"{choice.label} is installed, which is newer than {requirement}; not downgrading"
        return False, None

    def get_reasons(self, requirement: Requirement, visited: set[tuple[_PluginId, str]]) -> list[str]:
        """Explain why the versions that match a requirement are not viable.

        `visited` holds the removed versions already explained for the same failure.
        """
        key = requirement.name.lower()
        if key in self.fixed:
            return [self.get_fixed_choice(key).render()]
        plugin = self.get_plugin(requirement)
        if plugin is None:
            return [f"{requirement.name} is not in the repository"]

        plugin_id = _get_plugin_id(plugin)
        matching = [
            version
            for version in sorted(plugin.versions, key=parse_plugin_version, reverse=True)
            if requirement.matches(version)
        ]
        if not matching:
            return [f"no {plugin.name} version matches {requirement.version_spec}"]

        reasons: list[str] = []
        for version in matching:
            if version not in self.compatible.get(plugin_id, {}):
                reasons.append(f"{plugin.name} {version} {self.render_incompatibility(plugin.versions[version])}")
            elif (plugin_id, version) in self.removed:
                reasons.append(self.render_removal(plugin, version, visited))
        return reasons

    def get_conflict_reasons(self, existing: _Choice | None, constraints: tuple[_Pending, ...], name: str) -> list[str]:
        """Explain why no viable version satisfies every constraint on one plugin."""
        reasons = [existing.render()] if existing is not None else []
        chains = ", ".join(" -> ".join(constraint.chain) for constraint in constraints)
        reasons.append(f"no viable {name} version satisfies all of: {chains}")
        return reasons

    def render_incompatibility(self, locations: list[PluginArchiveLocation]) -> str:
        """Explain why the locations of one version cannot install on the cell, from the location closest to it."""
        location = max(
            locations,
            key=lambda location: (
                is_compatible_location(location, self.cell.platform),
                is_compatible_location(location, self.cell.platform, self.cell.ida_version),
            ),
        )
        plugin = location.metadata.plugin
        if not is_compatible_location(location, self.cell.platform):
            return f"does not support {self.cell.platform}"
        if not is_compatible_location(location, None, self.cell.ida_version):
            return f"does not support IDA {self.cell.ida_version}"
        try:
            python_version = self.python_version() if callable(self.python_version) else self.python_version
        except _PythonProbeError as e:
            return f"requires Python {plugin.requires_python}, and IDA's Python cannot be detected: {e}"
        return f"requires Python {plugin.requires_python}, and {self.cell} has Python {python_version}"

    def render_removal(self, plugin: Plugin, version: str, visited: set[tuple[_PluginId, str]]) -> str:
        node = (_get_plugin_id(plugin), version)
        requirement = self.removed[node]
        prefix = f"{plugin.name} {version} needs {requirement}"
        if node in visited:
            return f"{prefix} (see above)"
        visited.add(node)
        if requirement.name.lower() not in self.fixed and self.get_plugin(requirement) is None:
            return f"{prefix}, which is not in the repository"
        return f"{prefix}: {'; '.join(self.get_reasons(requirement, visited))}"

    def step(self, pending: _Pending) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise StepLimitError(
                self.cell, pending.chain, pending.requirements, [f"gave up after {self.max_steps} steps"]
            )

    def solve(
        self,
        pending: tuple[_Pending, ...],
        chosen: dict[str, _Choice],
        skipped: tuple[SkippedRequirement, ...],
        warnings: tuple[str, ...],
    ) -> _Solution | _Failure:
        """Satisfy the pending requirements in order, trying candidates newest first.

        Each candidate choice recurses, so the recursion depth is bounded by the number of plugin names.
        Required requirements stay ahead of optional ones in `pending` (see `_schedule`), so the
        failure of a required requirement never makes the search retry an optional one.

        Raises:
            StepLimitError: when the search takes more than `max_steps` steps.
        """
        chosen = dict(chosen)
        skipped_list = list(skipped)
        warnings_list = list(warnings)

        for index, item in enumerate(pending):
            self.step(item)
            requirement = item.requirement
            key = requirement.name.lower()

            existing = chosen.get(key)
            if existing is None and self.installed.get(key) is not None:
                existing = self.get_installed_choice(requirement, item.chain)
                chosen[key] = existing

            constraints: tuple[_Pending, ...] = (item,)
            if existing is not None:
                ok, warning = self.satisfies(existing, requirement)
                if ok:
                    if warning:
                        warnings_list.append(warning)
                    else:
                        chosen[key] = replace(existing, constraints=(*existing.constraints, item))
                    continue
                if existing.source != "installed":
                    failure = _fail(item, skipped_list, [existing.render()])
                    if failure is not None:
                        return failure
                    continue
                constraints = (*existing.constraints, item)

            plugin = self.get_plugin(requirement)
            if plugin is None:
                failure = _fail(item, skipped_list, [f"{requirement.name} is not in the repository"])
                if failure is not None:
                    return failure
                continue

            viable = self.viable[_get_plugin_id(plugin)]
            candidates = [
                (version, location)
                for version, location in viable.items()
                if all(constraint.requirement.matches(version) for constraint in constraints)
            ]
            if not candidates:
                reasons = self.get_reasons(requirement, set())
                if any(requirement.matches(version) for version in viable):
                    reasons.extend(self.get_conflict_reasons(existing, constraints, plugin.name))
                failure = _fail(item, skipped_list, reasons)
                if failure is not None:
                    return failure
                continue

            first_failure: _Failure | None = None
            for version, location in candidates:
                choice = _Choice(
                    name=plugin.name,
                    version=version,
                    host=plugin.host,
                    source="repo",
                    chain=item.chain,
                    constraints=constraints,
                    location=location,
                )
                dependencies = [item.get_dependency(dep, choice.label) for dep in get_requirements(location.metadata)]
                result = self.solve(
                    _schedule(dependencies, pending[index + 1 :]),
                    {**chosen, key: choice},
                    tuple(skipped_list),
                    tuple(warnings_list),
                )
                if isinstance(result, _Solution):
                    return result
                first_failure = first_failure or result

            assert first_failure is not None
            if requirement.required:
                return first_failure
            _fail(item, skipped_list, first_failure.reasons)

        return _Solution(chosen, tuple(skipped_list), tuple(warnings_list))


def _schedule(dependencies: Sequence[_Pending], rest: Sequence[_Pending]) -> tuple[_Pending, ...]:
    """Queue new dependencies ahead of the rest, with every required requirement ahead of every optional one."""
    required = [item for item in (*dependencies, *rest) if item.requirement.required]
    optional = [item for item in (*rest, *dependencies) if not item.requirement.required]
    return (*required, *optional)


def _fail(item: _Pending, skipped: list[SkippedRequirement], reasons: Sequence[str]) -> _Failure | None:
    """Fail a required requirement, or record an optional one as skipped and return None."""
    if item.requirement.required:
        return _Failure(item, tuple(reasons))
    skipped.append(SkippedRequirement(item.requirement, item.chain, "; ".join(reasons)))
    return None


def _get_order(chosen: dict[str, _Choice], starts: list[str]) -> list[str]:
    """List the repository selections so that each comes after the plugins it requires."""
    order: list[str] = []
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited or key not in chosen:
            return
        visited.add(key)
        choice = chosen[key]
        metadata = choice.location.metadata if choice.location is not None else choice.metadata
        if metadata is not None:
            for requirement in get_requirements(metadata):
                visit(requirement.name.lower())
        if choice.source == "repo":
            order.append(choice.name)

    for key in [*starts, *chosen]:
        visit(key)
    return order


def resolve(
    roots: list[Requirement],
    repo: BasePluginRepo,
    cell: Cell,
    *,
    fixed: Mapping[str, IDAMetadataDescriptor] | None = None,
    installed: Mapping[str, str] | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> Resolution:
    """Select the newest version of each root, in order, whose dependency closure installs on the cell.

    The caller checks that each fixed plugin is compatible with the cell.

    When `cell.python_version` is a function, it is called at most once, and only when a candidate
    declares `requiresPython`. When it raises, the candidates that declare `requiresPython` are not
    compatible, and the others stay candidates.

    Before the search, the requirements that the roots and the fixed plugins reach through every
    compatible location are checked for ambiguity, in all versions and not only the selected ones.
    A requirement that a fixed plugin or an installed version satisfies is not checked.

    Args:
        roots: requirements to satisfy, highest priority first.
        repo: repository to select from. `get_plugins()` is called one time.
        cell: target environment.
        fixed: plugins whose metadata cannot change, such as local directories, by name. Each is a
            root. A fixed plugin satisfies a requirement on its name only when its version matches.
            Fixed plugins that no root names are resolved before the roots.
        installed: installed plugin versions, by name. An installed plugin satisfies a requirement
            that its version matches, and a pin lower than its version, with a warning. Other
            requirements on its name select from the repository. The dependencies of an installed
            plugin are not resolved again.
        max_steps: number of requirement visits after which the search gives up.

    Raises:
        ResolutionError: when a required requirement cannot be satisfied.
        AmbiguousRequirementError: when a reachable requirement without a host matches plugins from
            several hosts.
        StepLimitError: when the search takes more than `max_steps` steps, or needs more selections
            than the recursion limit allows.
    """
    resolver = _Resolver(repo.get_plugins(), cell, fixed or {}, installed or {}, max_steps)

    root_keys = {root.name.lower() for root in roots}
    fixed_first = [key for key in resolver.fixed if key not in root_keys]

    def get_fixed_pending(key: str) -> list[_Pending]:
        choice = resolver.get_fixed_choice(key)
        assert choice.metadata is not None
        return [_Pending(requirement, (choice.label,)) for requirement in get_requirements(choice.metadata)]

    pending: list[_Pending] = []
    for key in fixed_first:
        pending.extend(get_fixed_pending(key))
    for root in roots:
        pending.append(_Pending(root, ()))
        if root.name.lower() in resolver.fixed:
            pending.extend(get_fixed_pending(root.name.lower()))

    resolver.check_reachable(pending)
    resolver.compute_viability()

    chosen = {key: resolver.get_fixed_choice(key) for key in resolver.fixed}
    starts = [*fixed_first, *(root.name.lower() for root in roots)]
    try:
        result = resolver.solve(_schedule([], pending), chosen, (), ())
        order = _get_order(result.chosen, starts) if isinstance(result, _Solution) else []
    except RecursionError:
        chain = [", ".join(str(root) for root in roots)]
        raise StepLimitError(cell, chain, (), ["too many plugins to select within the recursion limit"]) from None
    if isinstance(result, _Failure):
        raise ResolutionError(cell, result.pending.chain, result.pending.requirements, result.reasons)

    selected = {
        choice.name: choice.location
        for choice in result.chosen.values()
        if choice.source == "repo" and choice.location is not None
    }
    skipped: dict[tuple[str, str, str | None], SkippedRequirement] = {}
    for item in result.skipped:
        skipped.setdefault((item.requirement.name.lower(), item.requirement.version_spec, item.requirement.host), item)
    return Resolution(
        selected=selected,
        order=order,
        roots={root: result.chosen[root.name.lower()].name for root in roots if root.name.lower() in result.chosen},
        skipped=list(skipped.values()),
        warnings=list(dict.fromkeys(result.warnings)),
    )
