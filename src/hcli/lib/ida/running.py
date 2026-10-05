"""Facts about the IDA process that hcli runs inside.

When an IDA plugin or an idalib script imports hcli as a library, the running
process already knows which IDA it is: its version, installation directory,
user directory, and platform. These values are authoritative, while the
configuration files and environment variables that the CLI consults can name a
different installation.

The `ida_*` modules are imported only when IDAPython already loaded them, never
speculatively: outside an initialized IDA kernel they may fail to load or crash.
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

_SYS_PLATFORM_NAMES = {"darwin": "macos", "linux": "linux", "win32": "windows"}
_MACHINE_NAMES = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}

# hcli's label for values taken from the running process, shown by explain-environment.
RUNNING_IDA_SOURCE = "running IDA process"


def is_running_in_ida() -> bool:
    """Whether this process is IDA (GUI or idat) or an idalib host.

    IDAPython's init.py imports all `ida_*` modules before plugins load, and
    `import idapro` does the same for idalib.
    """
    return "ida_kernwin" in sys.modules


def get_platform_name(sys_platform: str, machine: str) -> str:
    """Map `sys.platform` and `platform.machine()` to an hcli platform like `macos-aarch64`.

    Raises:
        ValueError: for an operating system or architecture that IDA doesn't support.
    """
    os_name = _SYS_PLATFORM_NAMES.get(sys_platform)
    arch = _MACHINE_NAMES.get(machine.lower())
    if os_name is None or arch is None:
        raise ValueError(f"unsupported platform: {sys_platform} {machine}")
    return f"{os_name}-{arch}"


def get_running_ida_platform() -> str:
    """The platform of the running IDA process.

    Under Rosetta or Windows x64 emulation, `platform.machine()` reports the
    emulated architecture, which is the one IDA was built for.

    Raises:
        ValueError: for an unsupported operating system or architecture.
    """
    return get_platform_name(sys.platform, platform.machine())


def get_running_ida_version() -> str:
    """The `major.minor` version of the running IDA kernel, like `9.4`.

    Raises:
        ValueError: when the kernel reports a version hcli can't parse.
    """
    import ida_kernwin

    raw = ida_kernwin.get_kernel_version()
    m = re.match(r"(\d+\.\d+)", raw)
    if not m:
        raise ValueError(f"unrecognized IDA kernel version: {raw!r}")
    return m.group(1)


def get_running_ida_install_dir() -> Path:
    """The directory that contains the running IDA's binaries.

    On macOS this is `Contents/MacOS` inside the app bundle.
    """
    import ida_diskio

    return Path(ida_diskio.idadir(""))


def get_running_ida_user_dir() -> Path:
    """The running IDA's user directory, the first entry of IDAUSR."""
    import ida_diskio

    return Path(ida_diskio.get_user_idadir())
