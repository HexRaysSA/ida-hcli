"""Loose plugin dependency installation and management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from hcli.lib.ida.plugin import IDAMetadataDescriptor, parse_plugin_version
from hcli.lib.ida.plugin.exceptions import PluginNotInstalledError
from hcli.lib.ida.plugin.reference import parse_dependency_spec
from hcli.lib.ida.plugin.repo import BasePluginRepo
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

logger = logging.getLogger(__name__)


@dataclass
class DependencyResult:
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    upgraded: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def install_dependencies(
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo,
    current_platform: str,
    current_version: str,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
    check_environment: bool = True,
) -> DependencyResult:
    """Install dependencies declared in a plugin's metadata.

    Each dependency is fetched from ``plugin_repo`` and installed as an
    independent top-level plugin. Already-installed dependencies are skipped
    when they satisfy the spec. Pinned dependencies that are installed at a
    lower version are upgraded; higher versions are left alone with a warning.

    Failures on individual dependencies do not abort the remaining installs.

    Returns:
        A summary of what happened per dependency.
    """
    from hcli.lib.ida.plugin.install import (
        find_installed_plugin,
        install_plugin_archive,
        upgrade_plugin_archive,
    )

    result = DependencyResult()

    for spec in metadata.plugin.dependencies:
        ref = parse_dependency_spec(spec)
        dep_name = ref.name
        try:
            _install_one_dependency(
                dep_name=dep_name,
                version_spec=ref.version_spec,
                host=ref.host,
                plugin_repo=plugin_repo,
                current_platform=current_platform,
                current_version=current_version,
                pip_options=pip_options,
                check_environment=check_environment,
                result=result,
                find_installed=find_installed_plugin,
                do_install=install_plugin_archive,
                do_upgrade=upgrade_plugin_archive,
            )
        except Exception as e:
            logger.debug("failed to install dependency %s: %s", dep_name, e, exc_info=True)
            result.failed.append((dep_name, str(e)))

    return result


def _install_one_dependency(
    *,
    dep_name: str,
    version_spec: str,
    host: str | None,
    plugin_repo: BasePluginRepo,
    current_platform: str,
    current_version: str,
    pip_options: PipOptions,
    check_environment: bool,
    result: DependencyResult,
    find_installed,
    do_install,
    do_upgrade,
) -> None:
    try:
        installed = find_installed(dep_name)
    except PluginNotInstalledError:
        installed = None

    if installed is not None:
        if not version_spec:
            logger.info("dependency %s already installed (%s), skipping", dep_name, installed.version)
            result.skipped.append(dep_name)
            return

        pinned_version = version_spec.lstrip("=")
        installed_ver = parse_plugin_version(installed.version)
        pinned_ver = parse_plugin_version(pinned_version)

        if installed_ver == pinned_ver:
            logger.info("dependency %s already at pinned version %s, skipping", dep_name, pinned_version)
            result.skipped.append(dep_name)
            return

        if installed_ver > pinned_ver:
            logger.warning(
                "dependency %s is at %s which is newer than pin %s; not downgrading",
                dep_name,
                installed.version,
                pinned_version,
            )
            result.skipped.append(dep_name)
            return

        logger.info("dependency %s at %s needs upgrade to %s", dep_name, installed.version, pinned_version)
        bare_spec = dep_name + version_spec
        _dep_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
            bare_spec, current_platform, current_version, host=host
        )
        do_upgrade(buf, _dep_name, pip_options=pip_options, check_environment=check_environment)
        result.upgraded.append(dep_name)
        return

    bare_spec = dep_name + version_spec
    _dep_name, buf = plugin_repo.fetch_compatible_plugin_from_spec(
        bare_spec, current_platform, current_version, host=host
    )
    do_install(buf, _dep_name, pip_options=pip_options, check_environment=check_environment)
    result.installed.append(dep_name)
