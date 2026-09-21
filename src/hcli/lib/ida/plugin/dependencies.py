"""Loose plugin dependency installation and management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

from hcli.lib.ida.plugin import IDAMetadataDescriptor, parse_plugin_version
from hcli.lib.ida.plugin.context import InstallContext
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.install import (
    find_installed_plugin,
    get_metadata_from_plugin_directory,
    get_plugin_directory,
    install_plugin_archive,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.reference import parse_dependency_spec
from hcli.lib.ida.plugin.repo import BasePluginRepo

logger = logging.getLogger(__name__)

MAX_DEPENDENCY_DEPTH = 10


@dataclass
class DependencyResult:
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def install_dependencies(
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    *,
    dependency_settings: dict[str, dict[str, str]] | None = None,
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

    Failures on individual dependencies do not abort the remaining installs.

    Returns:
        A summary of what happened per dependency.
    """
    result = DependencyResult()

    for spec in metadata.plugin.dependencies:
        ref = parse_dependency_spec(spec)
        dep_name = ref.name

        try:
            changed = _install_one_dependency(
                dep_name=dep_name,
                version_spec=ref.version_spec,
                host=ref.host,
                plugin_repo=plugin_repo,
                ctx=ctx,
                result=result,
                find_installed=find_installed_plugin,
                do_install=install_plugin_archive,
                do_upgrade=upgrade_plugin_archive,
                settings=(dependency_settings or {}).get(dep_name),
            )
        except Exception as e:
            logger.debug("failed to install dependency %s: %s", dep_name, e, exc_info=True)
            result.failed.append((dep_name, str(e)))
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
                    dependency_settings=dependency_settings,
                    _depth=_depth + 1,
                )
                result.installed.extend(sub_result.installed)
                result.skipped.extend(sub_result.skipped)
                result.upgraded.extend(sub_result.upgraded)
                result.failed.extend(sub_result.failed)

    return result


def _apply_dependency_settings(dep_name: str, settings: dict[str, str] | None) -> None:
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


def _install_one_dependency(
    *,
    dep_name: str,
    version_spec: str,
    host: str | None,
    plugin_repo: BasePluginRepo,
    ctx: InstallContext,
    result: DependencyResult,
    find_installed: Callable[[str], Any],
    do_install: Callable[..., None],
    do_upgrade: Callable[..., None],
    settings: dict[str, str] | None = None,
) -> bool:
    """Install or upgrade a single dependency.

    Returns:
        True when an install or upgrade occurred, False when skipped.
    """
    try:
        installed = find_installed(dep_name)
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
        do_upgrade(buf, _dep_name, ctx)
        _apply_dependency_settings(dep_name, settings)
        result.upgraded.append(dep_name)
        return True

    bare_spec = dep_name + version_spec
    _dep_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
        bare_spec, ctx.env.platform, ctx.env.ida_version, host=host
    )
    do_install(buf, _dep_name, ctx)
    _apply_dependency_settings(dep_name, settings)
    result.installed.append(dep_name)
    return True
