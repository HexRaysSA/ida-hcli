"""Loose plugin dependency installation and management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hcli.lib.ida.plugin import IDAMetadataDescriptor, get_metadata_from_plugin_archive, parse_plugin_version
from hcli.lib.ida.plugin.components import collect_python_dependencies_from_archive
from hcli.lib.ida.plugin.context import InstallContext
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_metadata_from_plugin_directory,
    get_plugin_directory,
    install_plugin_archive,
    install_python_dependencies,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.reference import DependencyEntry
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)

MAX_DEPENDENCY_DEPTH = 10


@dataclass
class DependencyResult:
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped_optional: list[tuple[str, str]] = field(default_factory=list)
    failed_required: tuple[str, str] | None = None


def install_dependencies(
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    *,
    settings: dict[str, dict[str, str]] | None = None,
    _depth: int = 0,
) -> DependencyResult:
    """Install dependencies declared in a plugin's metadata, recursively.

    Each dependency is fetched from ``plugin_repo`` and installed as an
    independent top-level plugin. Already-installed dependencies are skipped
    when they satisfy the spec. Pinned dependencies that are installed at a
    lower version are upgraded; higher versions are left alone with a warning.

    After installing each dependency, its own declared dependencies are
    installed recursively, bounded by ``MAX_DEPENDENCY_DEPTH``. Cycles are
    detected by checking on-disk state: since parents are always installed
    before children, a dependency that already exists on disk is either
    already satisfied or part of a cycle.

    Required dependency failures stop sibling iteration and propagate up.
    Optional dependency failures are logged at info level and skipped.

    Returns:
        A summary of what happened per dependency.
    """
    result = DependencyResult()

    for entry in metadata.plugin.dependencies:
        assert isinstance(entry, DependencyEntry)
        ref = entry.reference
        dep_name = ref.name

        try:
            changed = _install_one_dependency(
                dep_name=dep_name,
                version_spec=ref.version_spec,
                host=ref.host,
                plugin_repo=plugin_repo,
                ctx=ctx,
                result=result,
                settings=(settings or {}).get(dep_name),
            )
        except Exception as e:
            logger.debug("failed to install dependency %s: %s", dep_name, e, exc_info=True)
            if entry.required:
                result.failed_required = (dep_name, str(e))
                result.failed.append((dep_name, str(e)))
                return result
            else:
                logger.info("Skipping optional dependency %s: %s", dep_name, e)
                result.skipped_optional.append((dep_name, str(e)))
                continue

        if changed and _depth < MAX_DEPENDENCY_DEPTH:
            dep_dir = get_plugin_directory(dep_name)
            try:
                dep_metadata = get_metadata_from_plugin_directory(dep_dir)
            except Exception as e:
                logger.debug("could not read metadata for dependency %s: %s", dep_name, e)
                continue
            if dep_metadata.plugin.dependencies:
                logger.debug("recursing into dependencies of %s (depth %d)", dep_name, _depth + 1)
                sub_result = install_dependencies(
                    dep_metadata,
                    plugin_repo,
                    ctx,
                    settings=settings,
                    _depth=_depth + 1,
                )
                result.installed.extend(sub_result.installed)
                result.skipped.extend(sub_result.skipped)
                result.upgraded.extend(sub_result.upgraded)
                result.failed.extend(sub_result.failed)
                result.skipped_optional.extend(sub_result.skipped_optional)

                if sub_result.failed_required is not None:
                    from hcli.lib.ida.plugin.install import uninstall_plugin

                    try:
                        uninstall_plugin(dep_name)
                    except Exception:
                        logger.debug("rollback of %s failed", dep_name, exc_info=True)

                    if entry.required:
                        reason = (
                            f"{dep_name} was removed because its required dependency "
                            f"{sub_result.failed_required[0]} failed: "
                            f"{sub_result.failed_required[1]}"
                        )
                        result.failed_required = (dep_name, reason)
                        return result
                    else:
                        reason = (
                            f"{dep_name} was removed because its required dependency "
                            f"{sub_result.failed_required[0]} failed"
                        )
                        logger.info("Skipping optional dependency %s: %s", dep_name, reason)
                        result.skipped_optional.append((dep_name, reason))

    return result


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


def _install_one_dependency(
    *,
    dep_name: str,
    version_spec: str,
    host: str | None,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    result: DependencyResult,
    settings: dict[str, str] | None = None,
) -> bool:
    """Install or upgrade a single dependency.

    Returns:
        True when an install or upgrade occurred, False when skipped.
    """
    try:
        installed = find_installed_plugin(dep_name)
    except PluginNotInstalledError:
        installed = None

    if installed is not None:
        if not version_spec:
            logger.info("dependency %s already installed (%s), skipping", dep_name, installed.version)
            result.skipped.append(dep_name)
            return False

        pinned_version = version_spec.lstrip("=")
        installed_ver = parse_plugin_version(installed.version)
        pinned_ver = parse_plugin_version(pinned_version)

        if installed_ver == pinned_ver:
            logger.info("dependency %s already at pinned version %s, skipping", dep_name, pinned_version)
            result.skipped.append(dep_name)
            return False

        if installed_ver > pinned_ver:
            logger.warning(
                "dependency %s is at %s which is newer than pin %s; not downgrading",
                dep_name,
                installed.version,
                pinned_version,
            )
            result.skipped.append(dep_name)
            return False

        logger.info("dependency %s at %s needs upgrade to %s", dep_name, installed.version, pinned_version)
        bare_spec = dep_name + version_spec
        _dep_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
            bare_spec, ctx.env.platform, ctx.env.ida_version, host=host
        )
        _install_dependency_python_deps(buf, _dep_name, ctx, excluded_plugins={dep_name})
        upgrade_plugin_archive(buf, _dep_name, ctx)
        _apply_settings(dep_name, settings)
        result.upgraded.append(dep_name)
        return True

    bare_spec = dep_name + version_spec
    _dep_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
        bare_spec, ctx.env.platform, ctx.env.ida_version, host=host
    )
    _install_dependency_python_deps(buf, _dep_name, ctx)
    install_plugin_archive(buf, _dep_name, ctx)
    _apply_settings(dep_name, settings)
    result.installed.append(dep_name)
    return True
