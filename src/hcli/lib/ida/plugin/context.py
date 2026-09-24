"""Install context types for plugin operations."""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions, detect_current_python_version


@dataclass(frozen=True)
class IDAEnvironment:
    """Detected IDA installation: platform and version."""

    platform: str
    ida_version: str

    @classmethod
    def from_current(cls) -> IDAEnvironment:
        """Probe the current IDA installation once."""
        return cls(
            platform=find_current_ida_platform(),
            ida_version=find_current_ida_version(),
        )

    @functools.cached_property
    def python_version(self) -> str:
        """major.minor of IDA's Python, probed on first access and cached.

        Probing runs idat and the Python interpreter as subprocesses, which is
        slow and not needed by most installs, so it's deferred until a plugin
        actually asks for it (e.g. via `requiresPython`).

        Raises:
            PythonNotFoundError: if IDA's Python can't be found or probed.
        """
        return detect_current_python_version()


@dataclass(frozen=True)
class InstallOptions:
    """User-specified options that affect how a plugin is installed."""

    pip_options: PipOptions = field(default_factory=lambda: PIP_OPTIONS_DEFAULT)
    check_environment: bool = True


@dataclass(frozen=True)
class InstallContext:
    """Bundle of environment and options threaded through install operations."""

    env: IDAEnvironment
    options: InstallOptions = field(default_factory=InstallOptions)
