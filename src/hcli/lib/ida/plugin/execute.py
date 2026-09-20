"""Verify a plan's artifacts, then apply it through a journaled transaction.

``prepare_install`` fetches and validates every selected artifact without
mutating anything. ``execute_install`` installs Python requirements, publishes
plugin directories, links editable sources, and records settings, rolling
everything back on a required failure. Optional branches run inside savepoints
so their failure never disturbs the accepted plan.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

if sys.version_info >= (3, 11):
    from typing import Self
else:
    from typing_extensions import Self

import httpx

from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    get_metadata_from_plugin_archive,
    is_binary_plugin_archive,
    is_source_plugin_archive,
    iter_dependency_specs,
    iter_expanded_components,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    BundleTargetUnavailableError,
    DependencyInstallationError,
    DependencyResolutionError,
    DependencyUnavailableError,
    InstallExecutionError,
    InvalidPluginNameError,
    MissingConfigurationError,
    PlanMetadataMismatchError,
    PluginAccessDeniedError,
    PluginInstallationError,
)
from hcli.lib.ida.plugin.install import (
    extract_zip_subdirectory_into,
    get_metadata_from_plugin_directory,
    get_plugin_directory,
    get_plugins_directory,
    is_valid_plugin_directory,
    resolve_python_for_dependencies,
    validate_archive_subdirectory,
    validate_metadata_in_plugin_directory,
)
from hcli.lib.ida.plugin.reference import normalize_plugin_host
from hcli.lib.ida.plugin.resolve import (
    EdgeDiagnostic,
    EditableSource,
    InstalledSource,
    InstallPlan,
    LocalArchiveSource,
    LocationSource,
    OptionalBranch,
    PhysicalArtifactCache,
    PlannedNode,
    PluginIdentity,
    ResolutionContext,
)
from hcli.lib.ida.plugin.transaction import (
    InstallTransaction,
    PreconditionChangedError,
    RollbackError,
    Savepoint,
)
from hcli.lib.ida.python import (
    PIP_OPTIONS_DEFAULT,
    CantInstallPackagesError,
    PipOptions,
    PythonNotFoundError,
    pip_install_packages,
    verify_pip_can_install_packages,
)

logger = logging.getLogger(__name__)

NodeOutcome = Literal["installed", "upgraded", "editable", "present", "unavailable", "rolled_back"]

ArtifactKey = tuple[PluginIdentity, str, str | None]

BOUNDARY_ERRORS: tuple[type[BaseException], ...] = (
    DependencyResolutionError,
    PluginInstallationError,
    PreconditionChangedError,
    PluginAccessDeniedError,
    httpx.HTTPError,
)


@dataclass(frozen=True)
class VerifiedArtifact:
    """Archive bytes whose digest and metadata were checked against the plan."""

    zip_data: bytes
    manifest_path: Path
    sha256: str


@dataclass
class NodeResult:
    identity: PluginIdentity
    name: str
    version: str
    operation: str
    outcome: NodeOutcome
    branch: int | None
    previous_version: str | None = None
    source: str | None = None

    @property
    def display(self) -> str:
        return f"{self.name}=={self.version}"


@dataclass
class InstallResult:
    """What execution did. Only committed operations appear as installed or upgraded."""

    plan: InstallPlan
    nodes: list[NodeResult] = field(default_factory=list)
    unavailable_optionals: list[tuple[OptionalBranch, str]] = field(default_factory=list)
    execution_diagnostics: list[EdgeDiagnostic] = field(default_factory=list)
    pip_attempted: bool = False
    recovery: RollbackError | None = None

    @property
    def committed_operations(self) -> list[NodeResult]:
        return [n for n in self.nodes if n.outcome in ("installed", "upgraded", "editable")]

    @property
    def present(self) -> list[NodeResult]:
        return [n for n in self.nodes if n.outcome == "present"]

    @property
    def diagnostics(self) -> list[EdgeDiagnostic]:
        diagnostics = list(self.plan.diagnostics)
        for branch in self.plan.optional_branches:
            diagnostics.extend(branch.diagnostics)
        diagnostics.extend(self.execution_diagnostics)
        return diagnostics

    def mark_rolled_back(self) -> None:
        for node in self.nodes:
            if node.outcome in ("installed", "upgraded", "editable"):
                node.outcome = "rolled_back"


@dataclass
class PreparedInstall:
    """A plan whose artifacts were fetched and verified, ready to execute."""

    plan: InstallPlan
    current_platform: str
    current_version: str | None
    artifacts: dict[ArtifactKey, VerifiedArtifact]
    branch_failures: dict[int, str]
    pip_options: PipOptions
    python_exe: Path | None
    check_environment: bool
    bundle_target_error: str | None = None
    _stack: ExitStack = field(default_factory=ExitStack, repr=False)

    def close(self) -> None:
        self._stack.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _canonical_settings(descriptor: IDAMetadataDescriptor) -> list[tuple[str, str, bool, str | bool | None]]:
    return sorted((s.key, s.type, s.required, s.default) for s in descriptor.plugin.settings)


def _canonical(descriptor: IDAMetadataDescriptor) -> dict[str, object]:
    plugin = descriptor.plugin
    python_deps = plugin.python_dependencies if isinstance(plugin.python_dependencies, list) else None
    return {
        "name": plugin.name,
        "version": plugin.version,
        "host": normalize_plugin_host(plugin.host),
        "entry point": plugin.entry_point,
        "platforms": sorted(plugin.platforms),
        "IDA versions": sorted(plugin.ida_versions),
        "components": sorted((("/".join(p)), d.plugin.version) for p, d in iter_expanded_components(descriptor)),
        "dependencies": sorted((("/".join(p)), d.plugin, d.required) for p, d in iter_dependency_specs(descriptor)),
        "Python dependencies": sorted(python_deps) if python_deps is not None else None,
        "settings": _canonical_settings(descriptor),
    }


def compare_planned_metadata(planned: IDAMetadataDescriptor, actual: IDAMetadataDescriptor) -> list[str]:
    """Human-readable differences between planned and physically observed metadata."""
    left, right = _canonical(planned), _canonical(actual)
    return [f"{key}: planned {left[key]!r}, found {right[key]!r}" for key in left if left[key] != right[key]]


def _describe_source(node: PlannedNode) -> str:
    source = node.source
    if isinstance(source, LocationSource):
        return source.repo_name or source.url
    if isinstance(source, LocalArchiveSource):
        return "local archive"
    if isinstance(source, EditableSource):
        return str(source.directory)
    assert isinstance(source, InstalledSource)
    return str(source.path)


def verify_planned_archive(node: PlannedNode, zip_data: bytes, label: str) -> VerifiedArtifact:
    """Run every archive check and compare the expanded manifest with the plan.

    Raises:
        ValueError: when the archive is malformed or unsafe.
        PlanMetadataMismatchError: when the archive's expanded metadata differs from the plan.
    """
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_archive

    name = node.name
    if not is_source_plugin_archive(zip_data, name) and not is_binary_plugin_archive(zip_data, name):
        raise ValueError(f"invalid plugin archive for {name} from {label}")
    manifest_path, raw = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, manifest_path, raw)
    validate_archive_subdirectory(zip_data, manifest_path.parent)
    expanded = expand_metadata_from_archive(zip_data, manifest_path)
    differences = compare_planned_metadata(node.metadata, expanded)
    if differences:
        raise PlanMetadataMismatchError(name, node.version, label, differences)
    return VerifiedArtifact(zip_data, manifest_path, hashlib.sha256(zip_data).hexdigest())


def _verify_node(node: PlannedNode, cache: PhysicalArtifactCache) -> VerifiedArtifact | None:
    """Fetch (once) and verify the artifact behind ``node``; ``None`` for retained or editable nodes.

    Raises:
        DependencyUnavailableError: when a repository artifact cannot be fetched.
        PlanMetadataMismatchError, ValueError: when the artifact does not match the plan.
    """
    from hcli.lib.ida.plugin.enrich import expand_metadata_from_directory

    source = node.source
    if isinstance(source, InstalledSource):
        return None
    if isinstance(source, EditableSource):
        validate_metadata_in_plugin_directory(source.directory)
        differences = compare_planned_metadata(node.metadata, expand_metadata_from_directory(source.directory))
        if differences:
            raise PlanMetadataMismatchError(node.name, node.version, str(source.directory), differences)
        return None
    if isinstance(source, LocalArchiveSource):
        return verify_planned_archive(node, cache.get(source.sha256), "local archive")

    label = source.repo_name or source.url
    if source.artifact_sha256 is not None and source.artifact_sha256 in cache:
        zip_data = cache.get(source.artifact_sha256)
    else:
        try:
            zip_data = source.repo.fetch_location(source.location)
        except (httpx.HTTPError, OSError, PluginAccessDeniedError, ValueError) as e:
            raise DependencyUnavailableError(
                node.selected_by.spec.plugin, f"failed to fetch {source.url}: {e}", node.selected_by.chain
            ) from e
    digest = hashlib.sha256(zip_data).hexdigest()
    if digest != source.location.sha256:
        raise DependencyUnavailableError(
            node.selected_by.spec.plugin,
            f"hash mismatch for {source.url}: expected {source.location.sha256}, found {digest}",
            node.selected_by.chain,
        )
    try:
        return verify_planned_archive(node, zip_data, label)
    except ValueError as e:
        raise DependencyUnavailableError(
            node.selected_by.spec.plugin, f"invalid archive at {source.url}: {e}", node.selected_by.chain
        ) from e


def _selection_key(node: PlannedNode) -> tuple[str, str, str | None]:
    source = node.source
    artifact: str | None = None
    if isinstance(source, LocationSource):
        artifact = source.location.sha256
    elif isinstance(source, LocalArchiveSource):
        artifact = source.sha256
    return node.version, node.identity.host, artifact


def _artifact_key(node: PlannedNode) -> ArtifactKey:
    """Identity plus the version and digest that distinguish one selection of it from another."""
    version, _, artifact = _selection_key(node)
    return node.identity, version, artifact


def _owned_names(node: PlannedNode) -> list[str]:
    return [node.name.lower(), *(name.lower() for name in node.component_names())]


def is_same_selection(left: PlannedNode, right: PlannedNode) -> bool:
    """Whether two planned nodes name the same version, host, and artifact."""
    lv, lh, la = _selection_key(left)
    rv, rh, ra = _selection_key(right)
    if (lv, lh) != (rv, rh):
        return False
    return la is None or ra is None or la == ra


def _all_nodes(plan: InstallPlan) -> list[PlannedNode]:
    nodes = list(plan.ordered_nodes())
    for branch in plan.optional_branches:
        nodes.extend(branch.nodes[i] for i in branch.order)
    return nodes


def _is_bundle_sourced(node: PlannedNode) -> bool:
    from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo

    return isinstance(node.source, LocationSource) and isinstance(
        node.source.repo.get_location_owner(node.source.location), PluginBundleRepo
    )


def _required_bundle_python_requirements(plan: InstallPlan) -> list[str]:
    """Requirements of required nodes that a bundle supplies, whose wheels only a bundle wheelhouse carries."""
    seen: dict[str, None] = {}
    for node in plan.ordered_nodes():
        if node.mutates and _is_bundle_sourced(node):
            for requirement in node.python_requirements():
                seen.setdefault(requirement, None)
    return list(seen)


def _bundle_owners(plan: InstallPlan) -> list[object]:
    from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo

    owners: list[object] = []
    for node in _all_nodes(plan):
        if not node.mutates or not isinstance(node.source, LocationSource):
            continue
        owner = node.source.repo.get_location_owner(node.source.location)
        if isinstance(owner, PluginBundleRepo) and all(owner is not o for o in owners):
            owners.append(owner)
    return owners


def _all_remote_from_bundles(plan: InstallPlan) -> bool:
    from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo

    for node in _all_nodes(plan):
        if (
            node.mutates
            and isinstance(node.source, LocationSource)
            and not isinstance(node.source.repo.get_location_owner(node.source.location), PluginBundleRepo)
        ):
            return False
    return True


def _effective_pip_options(
    plan: InstallPlan, user_options: PipOptions, current_platform: str, stack: ExitStack
) -> tuple[PipOptions, str | None]:
    """Combine bundle wheelhouses into pip options; user sources always win untouched.

    The second element explains why no bundle wheelhouse matches this IDA and
    Python, when that is the case. Optional branches that need Python packages
    become unavailable for that reason; the required branch raises instead.

    Raises:
        BundleTargetUnavailableError: a required node supplied by a bundle needs
            Python packages, and no bundle has a wheelhouse for this platform.
    """
    from hcli.lib.ida.plugin.bundle import bundle_dependency_source
    from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
    from hcli.lib.ida.python import detect_current_python_version, merge_bundle_pip_options

    if user_options.has_custom_sources:
        return user_options, None
    owners = _bundle_owners(plan)
    if not owners or not plan.mutating_python_requirements(plan.all_branches()):
        return user_options, None

    python_version = detect_current_python_version()
    find_links: list[Path | str] = []
    matched = False
    for owner in owners:
        assert isinstance(owner, PluginBundleRepo)
        bundle_options = stack.enter_context(bundle_dependency_source(owner, current_platform, python_version))
        if bundle_options is None:
            continue
        matched = True
        find_links.extend(bundle_options.find_links)
    if not matched:
        targets: list[str] = []
        for owner in owners:
            assert isinstance(owner, PluginBundleRepo)
            targets.extend(owner.target_ids)
        error = BundleTargetUnavailableError(current_platform, python_version, targets)
        if _required_bundle_python_requirements(plan):
            raise error
        return user_options, str(error)
    combined = PipOptions(
        find_links=tuple(find_links),
        offline=user_options.offline or _all_remote_from_bundles(plan),
        isolated=True,
        no_cache_dir=True,
        disable_pip_version_check=True,
    )
    return merge_bundle_pip_options(user_options, combined), None


def prepare_install(
    plan: InstallPlan,
    context: ResolutionContext,
    *,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    check_environment: bool = True,
) -> PreparedInstall:
    """Fetch and verify every artifact the plan needs, and preflight Python requirements.

    ``context`` is the same snapshot the plan was built from; its artifact
    cache supplies local archives and anything already fetched. Nothing is mutated. Optional branches whose artifacts fail verification
    are recorded in ``branch_failures`` instead of failing the operation.

    Raises:
        DependencyUnavailableError: a required artifact cannot be fetched or its digest differs.
        PlanMetadataMismatchError: a required artifact's contents differ from the planned metadata.
        ValueError: a required archive or directory is malformed.
        PipNotAvailableError, DependencyInstallationError: Python requirements cannot be installed.
    """
    artifacts: dict[ArtifactKey, VerifiedArtifact] = {}
    for node in plan.ordered_nodes():
        artifact = _verify_node(node, context.artifacts)
        if artifact is not None:
            artifacts[_artifact_key(node)] = artifact

    branch_failures: dict[int, str] = {}
    for branch in plan.optional_branches:
        if not branch.available:
            continue
        for identity in branch.order:
            node = branch.nodes[identity]
            key = _artifact_key(node)
            if key in artifacts:
                continue
            try:
                artifact = _verify_node(node, context.artifacts)
            except BOUNDARY_ERRORS as e:
                branch_failures[branch.index] = str(e)
                break
            if artifact is not None:
                artifacts[key] = artifact

    stack = ExitStack()
    try:
        effective, bundle_target_error = _effective_pip_options(plan, pip_options, context.current_platform, stack)
        python_exe: Path | None = None
        requirements: list[str] = []
        if plan.mutating_python_requirements():
            requirements = plan.combined_python_requirements()
            python_exe = resolve_python_for_dependencies(requirements, check_environment=check_environment)
            try:
                verify_pip_can_install_packages(python_exe, requirements, pip_options=effective)
            except CantInstallPackagesError as e:
                raise DependencyInstallationError(requirements, str(e)) from e
    except BaseException:
        stack.close()
        raise

    return PreparedInstall(
        plan=plan,
        current_platform=context.current_platform,
        current_version=context.current_version,
        artifacts=artifacts,
        branch_failures=branch_failures,
        pip_options=effective,
        python_exe=python_exe,
        check_environment=check_environment,
        bundle_target_error=bundle_target_error,
        _stack=stack,
    )


class _DestinationChangedError(PreconditionChangedError):
    """A planned destination changed; ``identity`` names the node whose check failed."""

    def __init__(self, identity: PluginIdentity, message: str) -> None:
        super().__init__(message)
        self.identity = identity


class _Executor:
    def __init__(self, prepared: PreparedInstall, txn: InstallTransaction, *, require_configuration: bool = True):
        self.prepared = prepared
        self.plan = prepared.plan
        self.txn = txn
        self.require_configuration = require_configuration
        self.result = InstallResult(self.plan)
        self.present: set[PluginIdentity] = set()
        self.accepted_nodes: dict[PluginIdentity, PlannedNode] = {}
        self.owned_names: dict[str, PluginIdentity] = {}
        self.accepted_branches: list[int] = []
        self._site_dir: Path | None = None
        self._site_dir_checked = False

    def _pth_path(self, plugin_name: str) -> Path | None:
        from hcli.lib.ida.plugin.install import _editable_pth_filename, _get_ida_site_packages_dir

        if not self._site_dir_checked:
            self._site_dir_checked = True
            try:
                self._site_dir = _get_ida_site_packages_dir()
            except Exception as e:
                logger.debug("could not locate IDA site-packages: %s", e)
                self._site_dir = None
        if self._site_dir is None:
            return None
        return self._site_dir / _editable_pth_filename(plugin_name)

    def _python(self, requirements: list[str]) -> Path:
        """IDA's Python for ``requirements``; resolved once during preparation when the required plan needed it.

        Raises:
            PythonNotFoundError, PluginInstallationError: as for ``resolve_python_for_dependencies``.
        """
        if self.prepared.python_exe is not None:
            return self.prepared.python_exe
        return resolve_python_for_dependencies(requirements, check_environment=self.prepared.check_environment)

    def _pip(self, requirements: list[str], python_exe: Path) -> None:
        self.result.pip_attempted = True
        try:
            pip_install_packages(python_exe, requirements, pip_options=self.prepared.pip_options)
        except CantInstallPackagesError as e:
            raise DependencyInstallationError(requirements, str(e)) from e

    def _check_destination(self, node: PlannedNode, destination: Path) -> None:
        exists = destination.is_symlink() or destination.exists()
        if node.installed is None:
            if not exists:
                return
            if is_valid_plugin_directory(destination):
                raise PreconditionChangedError(
                    f"{node.name} was installed at {destination} after planning; rerun the command"
                )
            raise BrokenPluginInstallationError(node.name, destination)
        if not exists:
            raise PreconditionChangedError(f"{node.name} was removed from {destination} after planning")
        try:
            current = get_metadata_from_plugin_directory(destination)
        except ValueError as e:
            raise PreconditionChangedError(f"{node.name} at {destination} is no longer readable: {e}") from e
        if current.plugin.name != node.installed.name or current.plugin.version != node.installed.version:
            raise PreconditionChangedError(
                f"{node.name} at {destination} is now {current.plugin.name} {current.plugin.version}, "
                f"not {node.installed.name} {node.installed.version} as planned"
            )

    def _destination(self, node: PlannedNode) -> Path:
        try:
            return get_plugin_directory(node.name)
        except ValueError as e:
            raise InvalidPluginNameError(node.name, str(e)) from e

    def _check_destinations(self, nodes: list[PlannedNode]) -> None:
        """Recheck every destination before pip runs, since Python changes are never rolled back.

        Raises:
            _DestinationChangedError: naming the first node whose destination no longer matches the plan.
        """
        for node in nodes:
            if node.operation == "editable" and node.installed is None:
                continue
            try:
                self._check_destination(node, self._destination(node))
            except PreconditionChangedError as e:
                raise _DestinationChangedError(node.identity, str(e)) from e

    def _apply_node(self, node: PlannedNode, branch: int | None) -> None:
        previous = node.installed.version if node.installed is not None else None
        entry = NodeResult(
            node.identity, node.name, node.version, node.operation, "present", branch, previous, _describe_source(node)
        )
        destination = self._destination(node)

        if node.operation == "retain":
            self._check_destination(node, destination)
        elif node.operation == "editable":
            assert isinstance(node.source, EditableSource)
            if node.installed is not None:
                self._check_destination(node, destination)
            self.txn.link_directory(node.source.directory, destination)
            pth = self._pth_path(node.name)
            src_dir = node.source.directory / "src"
            if src_dir.is_dir():
                if pth is None:
                    raise ValueError("could not locate IDA's site-packages to register the editable src layout")
                self.txn.write_pth(pth, f"{src_dir}\n")
            elif pth is not None:
                self.txn.remove_pth(pth)
            entry.outcome = "editable"
        else:
            self._check_destination(node, destination)
            artifact = self.prepared.artifacts[_artifact_key(node)]
            staging = self.txn.make_staging_directory(node.name)
            extract_zip_subdirectory_into(artifact.zip_data, artifact.manifest_path.parent, staging)
            pth = self._pth_path(node.name)
            if pth is not None:
                self.txn.remove_pth(pth)
            if node.operation == "upgrade":
                self.txn.replace_directory(staging, destination)
                entry.outcome = "upgraded"
            else:
                self.txn.publish_directory(staging, destination)
                entry.outcome = "installed"
        self.result.nodes.append(entry)
        self.present.add(node.identity)
        self.accepted_nodes[node.identity] = node
        for name in _owned_names(node):
            self.owned_names[name] = node.identity

    def _forget_node(self, identity: PluginIdentity) -> None:
        self.present.discard(identity)
        self.accepted_nodes.pop(identity, None)
        for name in [n for n, owner in self.owned_names.items() if owner == identity]:
            del self.owned_names[name]

    def _find_branch_conflict(self, branch: OptionalBranch, nodes: list[PlannedNode]) -> tuple[PlannedNode, str] | None:
        """A node of ``branch`` that cannot coexist with what earlier steps installed, and why."""
        for node in nodes:
            accepted = self.accepted_nodes.get(node.identity)
            if accepted is not None:
                if not is_same_selection(accepted, node):
                    return node, (
                        f"{node.name} is already present at {accepted.version}"
                        f" but this branch selected {node.version}; version reselection is unsupported"
                    )
                continue
            for name in _owned_names(node):
                owner = self.owned_names.get(name)
                if owner is not None and owner != node.identity:
                    return node, (
                        f"{node.name} claims the plugin name '{name}', which {owner.name} already owns;"
                        " only one plugin can own a name"
                    )
        return None

    def _apply_configuration(self, nodes: list[PlannedNode]) -> None:
        """Write the supplied values for settings of ``nodes``, including plugins the plan keeps as they are.

        Each write checks that the stored value is still what planning observed.

        Raises:
            PreconditionChangedError: a setting changed since planning.
        """
        targets: dict[str, tuple[str, IDAMetadataDescriptor]] = {}
        for node in nodes:
            for _, descriptor in node.iter_manifests():
                targets.setdefault(descriptor.plugin.name.lower(), (descriptor.plugin.name, descriptor))
        for (target, key), value in self.plan.configuration_values.items():
            found = targets.get(target.lower())
            if found is None:
                continue
            display_name, descriptor = found
            existed, current = self.plan.observed_settings.get((display_name, key), (False, None))
            self.txn.set_config_key(
                display_name, key, value, descriptor, expected_existing=existed, expected_value=current
            )

    def _run_required(self) -> None:
        missing = self.plan.missing_configuration()
        if missing and self.require_configuration:
            raise MissingConfigurationError([r.argument() for r in missing])
        self._check_destinations(self.plan.ordered_nodes())
        if self.plan.mutating_python_requirements():
            requirements = self.plan.combined_python_requirements()
            self._pip(requirements, self._python(requirements))
        for node in self.plan.ordered_nodes():
            self._apply_node(node, None)
        self._apply_configuration(self.plan.ordered_nodes())

    def _drop_skipped_entries(self, nodes: list[PlannedNode]) -> None:
        """Forget entries an earlier skipped branch recorded for nodes this branch is about to handle."""
        identities = {node.identity for node in nodes}
        self.result.nodes[:] = [entry for entry in self.result.nodes if entry.identity not in identities]

    def _skip_branch(self, branch: OptionalBranch, reason: str, *, failed: PluginIdentity | None = None) -> None:
        """Record the branch as unavailable; ``failed`` is the retained node whose destination check failed."""
        self.result.unavailable_optionals.append((branch, reason))
        for identity in branch.order:
            node = branch.nodes[identity]
            if identity in self.present or any(r.identity == identity for r in self.result.nodes):
                continue
            outcome: NodeOutcome = "present" if not node.mutates and identity != failed else "unavailable"
            self.result.nodes.append(
                NodeResult(identity, node.name, node.version, node.operation, outcome, branch.index)
            )

    def _run_branch(self, branch: OptionalBranch) -> None:
        if not branch.available:
            self._skip_branch(branch, branch.unavailable_reason or "unavailable")
            return
        if branch.prerequisite is not None and branch.prerequisite not in self.accepted_branches:
            self._skip_branch(
                branch,
                f"declaring optional dependency '{self.plan.optional_branches[branch.prerequisite].edge.spec.plugin}' is unavailable",
            )
            return
        if branch.edge.parent is not None and branch.edge.parent not in self.present:
            self._skip_branch(branch, f"declaring plugin '{branch.edge.parent.name}' is not present")
            return
        failure = self.prepared.branch_failures.get(branch.index)
        if failure is not None:
            self._skip_branch(branch, failure)
            return
        missing = [r for r in self.plan.missing_configuration([branch.index]) if r.branch == branch.index]
        if missing and self.require_configuration:
            self._skip_branch(branch, str(MissingConfigurationError([r.argument() for r in missing])))
            return

        nodes = [branch.nodes[i] for i in branch.order]
        conflict = self._find_branch_conflict(branch, nodes)
        if conflict is not None:
            node, message = conflict
            self.result.execution_diagnostics.append(EdgeDiagnostic(branch.edge, node.identity, "conflict", message))
            self._skip_branch(branch, message)
            return
        requirements: list[str] = []
        for node in nodes:
            if not node.mutates:
                continue
            for requirement in node.python_requirements():
                if requirement not in requirements:
                    requirements.append(requirement)
        if requirements and self.prepared.bundle_target_error is not None:
            self._skip_branch(branch, self.prepared.bundle_target_error)
            return
        python_exe: Path | None = None
        if requirements:
            try:
                python_exe = self._python(requirements)
            except (PythonNotFoundError, PluginInstallationError) as e:
                self._skip_branch(branch, str(e))
                return
        pending = [node for node in nodes if node.identity not in self.present]
        self._drop_skipped_entries(pending)
        savepoint: Savepoint = self.txn.savepoint()
        before = len(self.result.nodes)
        try:
            self._check_destinations(pending)
            if python_exe is not None:
                combined = self.plan.combined_python_requirements([*self.accepted_branches, branch.index])
                try:
                    verify_pip_can_install_packages(python_exe, combined, pip_options=self.prepared.pip_options)
                except CantInstallPackagesError as e:
                    raise DependencyInstallationError(requirements, str(e)) from e
                self._pip(requirements, python_exe)
            for node in pending:
                self._apply_node(node, branch.index)
            self._apply_configuration(pending)
        except BOUNDARY_ERRORS as e:
            logger.debug("optional branch %s failed: %s", branch.edge.spec.plugin, e)
            try:
                self.txn.rollback_to(savepoint)
            except RollbackError as rollback_error:
                raise RollbackError(e, rollback_error.failures, rollback_error.retained_paths) from e
            rolled_back = [entry.display for entry in self.result.nodes[before:]]
            for entry in self.result.nodes[before:]:
                self._forget_node(entry.identity)
            del self.result.nodes[before:]
            reason = str(e)
            if rolled_back:
                reason += f"; rolled back {', '.join(rolled_back)}"
            failed = e.identity if isinstance(e, _DestinationChangedError) else None
            self._skip_branch(branch, reason, failed=failed)
            return
        self.accepted_branches.append(branch.index)

    def run(self) -> InstallResult:
        self._run_required()
        for branch in self.plan.optional_branches:
            self._run_branch(branch)
        return self.result


def _merge_rollback_errors(first: RollbackError | None, second: RollbackError) -> RollbackError:
    if first is None:
        return second
    return RollbackError(
        first.original, [*first.failures, *second.failures], [*first.retained_paths, *second.retained_paths]
    )


def execute_install(
    prepared: PreparedInstall,
    *,
    transaction: InstallTransaction | None = None,
    config_values: Mapping[tuple[str, str], str | bool] | None = None,
    require_configuration: bool = True,
) -> InstallResult:
    """Apply a prepared plan. Without ``transaction`` one is created and committed here.

    Failures that happen before anything was changed propagate as themselves.
    Once a step has mutated state, any failure is reported as
    ``InstallExecutionError`` after rollback; ``KeyboardInterrupt`` and
    ``SystemExit`` propagate unchanged with the rolled-back result attached as
    ``install_result``.

    With ``require_configuration`` false, required settings without a value do
    not block; only supplied values are written and the caller collects the rest.

    Raises:
        MissingConfigurationError: a required setting has no value; nothing was mutated.
        ValueError: ``config_values`` names an unknown target, key, or invalid value.
        InstallExecutionError: a required step failed; plugin directories, links, and
            settings were rolled back. Python packages installed by pip are not.
    """
    plan = prepared.plan
    if config_values:
        plan.apply_configuration_values(config_values)
    missing = plan.missing_configuration()
    if missing and require_configuration:
        raise MissingConfigurationError([r.argument() for r in missing])

    own = transaction is None
    txn = transaction or InstallTransaction(get_plugins_directory())
    savepoint = txn.savepoint()
    executor = _Executor(prepared, txn, require_configuration=require_configuration)
    try:
        result = executor.run()
        if own:
            txn.commit()
        return result
    except BaseException as e:
        result = executor.result
        mutated = len(txn.journal) > savepoint.position or result.pip_attempted or isinstance(e, RollbackError)
        failure: BaseException = e
        recovery: RollbackError | None = None
        if isinstance(e, RollbackError) and e.original is not None:
            failure, recovery = e.original, e
        try:
            if own:
                txn.rollback(e)
            else:
                txn.rollback_to(savepoint)
        except RollbackError as rollback_error:
            recovery = _merge_rollback_errors(recovery, rollback_error)
        except BaseException as interrupt:
            result.mark_rolled_back()
            result.recovery = recovery
            setattr(interrupt, "install_result", result)  # noqa: B010
            raise
        result.mark_rolled_back()
        result.recovery = recovery
        if not mutated:
            raise
        if not isinstance(e, Exception):
            setattr(e, "install_result", result)  # noqa: B010
            raise
        message = f"installation failed: {failure}"
        if result.pip_attempted:
            message += ". Python packages that pip installed were not rolled back"
        if recovery is not None:
            message += f". {recovery}"
        raise InstallExecutionError(message, failure, result, recovery) from e
