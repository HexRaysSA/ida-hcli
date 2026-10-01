"""Loose plugin dependency installation and management."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from hcli.lib.ida.plugin import IDAMetadataDescriptor, get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.components import collect_python_dependencies_from_archive
from hcli.lib.ida.plugin.context import InstallContext
from hcli.lib.ida.plugin.install import (
    get_installed_plugin_records,
    get_metadata_from_plugin_directory,
    get_plugin_directory,
    install_plugin_archive,
    install_python_dependencies,
    upgrade_plugin_archive,
    validate_python_version,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, PluginArchiveLocation
from hcli.lib.ida.plugin.resolve import (
    Cell,
    InstalledVersion,
    Requirement,
    Resolution,
    ResolutionError,
    get_requirements,
    resolve,
)

logger = logging.getLogger(__name__)


@dataclass
class DependencyResult:
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped_optional: list[tuple[str, str]] = field(default_factory=list)
    required_failure: tuple[str, str] | None = None


def get_install_cell(ctx: InstallContext) -> Cell:
    """The resolver cell of the IDA installation.

    IDA's Python is probed only when a candidate declares `requiresPython`.
    """
    return Cell(ctx.env.platform, ctx.env.ida_version, python_version=lambda: ctx.env.python_version)


def get_installed_versions(*, exclude: str | None = None) -> dict[str, InstalledVersion]:
    """The version and host of each installed plugin, by name, without the plugin named `exclude`."""
    return {
        record.name: InstalledVersion(record.version, record.host)
        for record in get_installed_plugin_records()
        if exclude is None or record.name.lower() != exclude.lower()
    }


def plan_dependencies(metadata: IDAMetadataDescriptor, plugin_repo: BasePluginRepo, ctx: InstallContext) -> Resolution:
    """Resolve the dependencies of a plugin that is about to be installed, without writing anything.

    Components in `metadata` contribute their dependencies when they are expanded to metadata.
    An installed copy of the plugin itself does not count as installed.

    Raises:
        ResolutionError: when a required dependency cannot be satisfied.
    """
    return resolve(
        [],
        plugin_repo,
        get_install_cell(ctx),
        fixed={metadata.plugin.name: metadata},
        installed=get_installed_versions(exclude=metadata.plugin.name),
    )


def install_dependencies(
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    *,
    settings: dict[str, dict[str, str]] | None = None,
    plan: Resolution | None = None,
) -> DependencyResult:
    """Install the dependencies of a plugin as independent top-level plugins, dependencies first.

    `plan` is the result of `plan_dependencies` for `metadata`, which is computed when it is not
    given. Each planned plugin is fetched from `plugin_repo`, checked against `requiresPython`, and
    installed with its Python dependencies, or upgraded when an older version is installed.
    Installed plugins that satisfy a requirement are reported as skipped.

    A failure of a plugin that the root requires, directly or through required dependencies, stops
    the installation. Other failures are logged at info level and reported as skipped optional.

    Returns:
        A summary of what happened per dependency.
    """
    result = DependencyResult()
    if plan is None:
        try:
            plan = plan_dependencies(metadata, plugin_repo, ctx)
        except ResolutionError as e:
            logger.debug("failed to resolve dependencies of %s: %s", metadata.plugin.name, e, exc_info=True)
            name = e.requirements[0].name if e.requirements else metadata.plugin.name
            result.required_failure = (name, str(e))
            result.failed.append((name, str(e)))
            return result

    for warning in plan.warnings:
        logger.warning(warning)

    installed = {name.lower() for name in get_installed_versions(exclude=metadata.plugin.name)}
    skipped_names = {item.requirement.name.lower() for item in plan.skipped}
    selected = {name.lower() for name in plan.selected}
    for requirement in _get_reachable_requirements(metadata, plan, required_only=False):
        key = requirement.name.lower()
        if (
            key in installed
            and key not in selected
            and key not in skipped_names
            and requirement.name not in result.skipped
        ):
            logger.info("dependency %s already installed, skipping", requirement.name)
            result.skipped.append(requirement.name)

    for item in plan.skipped:
        logger.info("Skipping optional dependency %s: %s", item.requirement.name, item.reason)
        result.skipped_optional.append((item.requirement.name, item.reason))

    required = {
        requirement.name.lower() for requirement in _get_reachable_requirements(metadata, plan, required_only=True)
    }
    # Settings are keyed by the name that a plugin declares, which can differ in case from the repository name.
    settings_by_key = {key.lower(): value for key, value in (settings or {}).items()}
    failed: set[str] = set()
    for name in plan.order:
        location = plan.selected[name]
        is_upgrade = name.lower() in installed
        try:
            blocked = [
                requirement.name
                for requirement in get_requirements(location.metadata)
                if requirement.required and requirement.name.lower() in failed
            ]
            if blocked:
                raise RuntimeError(f"{name} was not installed because its required dependency {blocked[0]} failed")
            _install_planned_dependency(
                name, location, plugin_repo, ctx, upgrade=is_upgrade, settings=settings_by_key.get(name.lower())
            )
        except Exception as e:
            logger.debug("failed to install dependency %s: %s", name, e, exc_info=True)
            failed.add(name.lower())
            if name.lower() in required:
                result.required_failure = (name, str(e))
                result.failed.append((name, str(e)))
                return result
            logger.info("Skipping optional dependency %s: %s", name, e)
            result.skipped_optional.append((name, str(e)))
            continue

        (result.upgraded if is_upgrade else result.installed).append(name)

    return result


def _get_reachable_requirements(
    metadata: IDAMetadataDescriptor, plan: Resolution, *, required_only: bool
) -> list[Requirement]:
    """The requirements of the root and of the planned plugins that the root reaches."""
    selected = {name.lower(): location for name, location in plan.selected.items()}
    reachable: list[Requirement] = []
    visited: set[str] = set()
    queue = deque(get_requirements(metadata))
    while queue:
        requirement = queue.popleft()
        if required_only and not requirement.required:
            continue
        reachable.append(requirement)
        key = requirement.name.lower()
        if key in visited or key not in selected:
            continue
        visited.add(key)
        queue.extend(get_requirements(selected[key].metadata))
    return reachable


def _install_planned_dependency(
    name: str,
    location: PluginArchiveLocation,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    *,
    upgrade: bool,
    settings: dict[str, str] | None,
) -> None:
    """Fetch a planned dependency and install or upgrade it, after its `requiresPython` check.

    Raises:
        PythonVersionIncompatibleError: when the archive's `requiresPython` excludes IDA's Python.
        Exception: when the fetch, the Python dependencies, or the archive install fail.
    """
    plugin_name, buf = plugin_repo.fetch_plugin_location(location)
    validate_python_version(get_metadata_from_plugin_archive(buf, plugin_name)[1], ctx)
    if upgrade:
        _install_dependency_python_deps(buf, plugin_name, ctx, excluded_plugins={name})
        upgrade_plugin_archive(buf, plugin_name, ctx)
    else:
        _install_dependency_python_deps(buf, plugin_name, ctx)
        install_plugin_archive(buf, plugin_name, ctx)
    _apply_settings(name, settings)


def _apply_settings(dep_name: str, settings: dict[str, str] | None) -> None:
    if not settings:
        return

    from hcli.lib.ida.plugin.settings import apply_resolved_settings

    dep_dir = get_plugin_directory(dep_name)
    try:
        dep_metadata = get_metadata_from_plugin_directory(dep_dir)
    except Exception as e:
        logger.warning("cannot read metadata for dependency %s to apply settings: %s", dep_name, e)
        return

    if not apply_resolved_settings(dep_name, dep_metadata, settings):
        logger.warning("failed to apply settings to dependency %s", dep_name)


def _install_dependency_python_deps(
    zip_data: bytes,
    plugin_name: str,
    ctx: InstallContext,
    *,
    excluded_plugins: set[str] | None = None,
) -> None:
    """Collect and install Python dependencies for a dependency plugin archive."""
    metadata_path, dep_meta = get_metadata_from_plugin_archive(zip_data, plugin_name)
    python_deps = collect_python_dependencies_from_archive(zip_data, metadata_path, dep_meta)
    install_python_dependencies(python_deps, ctx, excluded_plugins=excluded_plugins)
