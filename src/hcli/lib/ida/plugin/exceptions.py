"""Exceptions for IDA plugin installation and management."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


class PluginInstallationError(Exception):
    """Base exception for plugin installation failures."""

    pass


class PluginAlreadyInstalledError(PluginInstallationError):
    """Plugin is already installed."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        super().__init__(
            f"Plugin '{name}' is already installed at {path}. "
            f"Use 'hcli plugin upgrade {name}' to update or 'hcli plugin uninstall {name}' first."
        )


class PlatformIncompatibleError(PluginInstallationError):
    """Current platform is not supported by the plugin."""

    def __init__(self, current: str, supported: Sequence[str]):
        self.current = current
        self.supported = supported
        platforms_str = ", ".join(sorted(supported))
        super().__init__(
            f"Plugin not compatible with current platform '{current}'. Supported platforms: {platforms_str}"
        )


class IDAVersionIncompatibleError(PluginInstallationError):
    """Current IDA version is not supported by the plugin."""

    def __init__(self, current: str, supported: Sequence[str]):
        self.current = current
        self.supported = supported
        # Show first 10 supported versions to avoid overwhelming output
        if len(supported) > 10:
            versions_str = ", ".join(supported[:10]) + f" (and {len(supported) - 10} more)"
        else:
            versions_str = ", ".join(supported)
        super().__init__(f"Plugin not compatible with IDA version '{current}'. Supported versions: {versions_str}")


class PipNotAvailableError(PluginInstallationError):
    """pip is not available in IDA's Python environment."""

    def __init__(self):
        super().__init__(
            "Cannot install plugin: pip is not available in IDA's Python environment. "
            "The plugin requires Python dependencies but pip cannot be found. "
            "Please ensure your IDA installation includes pip support."
        )


class DependencyInstallationError(PluginInstallationError):
    """Python dependencies cannot be installed."""

    def __init__(self, dependencies: Sequence[str], reason: str | None = None):
        self.dependencies = dependencies
        self.reason = reason
        deps_str = ", ".join(dependencies)
        msg = f"Cannot install required Python dependencies: {deps_str}"
        if reason:
            msg += f". Reason: {reason}"
        super().__init__(msg)


class InvalidPluginNameError(PluginInstallationError):
    """Plugin name is invalid."""

    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason
        super().__init__(f"Invalid plugin name '{name}': {reason}")


class PluginNotInstalledError(Exception):
    """Plugin is not installed."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Plugin '{name}' is not installed")


class PluginUpgradeError(Exception):
    """Base exception for plugin upgrade failures."""

    pass


class PluginVersionDowngradeError(PluginUpgradeError):
    """Attempted to upgrade to a version that is not newer."""

    def __init__(self, name: str, current_version: str, new_version: str):
        self.name = name
        self.current_version = current_version
        self.new_version = new_version
        super().__init__(
            f"Cannot upgrade plugin '{name}': new version {new_version} "
            f"is not greater than current version {current_version}"
        )
