"""Install context types for plugin operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from hcli.lib.ida import find_current_ida_platform, find_current_ida_version
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, PipOptions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IDAEnvironment:
    """Detected IDA installation: platform, version, and optionally the Python interpreter."""

    platform: str
    ida_version: str
    python_exe: Path | None = None
    python_version: str | None = None

    @classmethod
    def from_current(cls) -> IDAEnvironment:
        """Probe the current IDA installation once.

        Python fields are left unset; call ``resolve_python`` when dependencies
        require an interpreter.
        """
        return cls(
            platform=find_current_ida_platform(),
            ida_version=find_current_ida_version(),
        )

    def resolve_python(self) -> IDAEnvironment:
        """Return a copy with ``python_exe`` and ``python_version`` filled in.

        Raises:
            PythonNotFoundError: when the interpreter can't be detected.
        """
        from hcli.lib.ida.python import detect_current_python_version, resolve_current_python

        resolved = resolve_current_python()
        return IDAEnvironment(
            platform=self.platform,
            ida_version=self.ida_version,
            python_exe=resolved.exe,
            python_version=detect_current_python_version(),
        )


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
