"""Exceptions for IDA plugin installation and management."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


class PluginInstallationError(Exception):
    """Base exception for plugin installation failures."""


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

    def __init__(self, python_exe: Path):
        super().__init__(
            "Cannot install plugin: pip is not available in IDA's Python environment. "
            "The plugin requires Python dependencies but pip cannot be found. "
            "Please ensure your IDA installation includes pip support."
            f"You can try installing pip manually by running: {python_exe} -m ensurepip"
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


class AmbiguousPluginReferenceError(Exception):
    """A bare plugin reference matches multiple repository plugins.

    Commands should render this to the user along with the qualified
    ``name@repo`` form of each candidate so the user can rerun unambiguously.
    """

    def __init__(
        self,
        name: str,
        candidates: Sequence[tuple[str, str]],
        version_spec: str = "",
    ):
        from hcli.lib.ida.plugin.reference import PluginReference

        self.name = name
        self.candidates = list(candidates)
        self.version_spec = version_spec
        self.candidate_refs: list[PluginReference] = [
            PluginReference(name=cname, version_spec=version_spec, host=chost) for cname, chost in candidates
        ]
        super().__init__(f"ambiguous plugin reference: {name!r} matches {len(self.candidates)} plugins")


class InstalledPluginNameConflictError(PluginInstallationError):
    """Installing a plugin would collide with another already-installed same-name plugin.

    Raised when the user asks to install ``foo@repo-a`` but ``foo@repo-b`` is
    already installed. The install layout is ``$IDAUSR/plugins/<name>``, so
    only one plugin with a given bare name can be installed at a time.
    """

    def __init__(
        self,
        requested_name: str,
        requested_host: str,
        installed_name: str,
        installed_host: str,
        installed_path: Path,
    ):
        self.requested_name = requested_name
        self.requested_host = requested_host
        self.installed_name = installed_name
        self.installed_host = installed_host
        self.installed_path = installed_path
        super().__init__(
            f"cannot install plugin '{requested_name}@{requested_host}' because "
            f"'{installed_name}@{installed_host}' is already installed at {installed_path}"
        )
