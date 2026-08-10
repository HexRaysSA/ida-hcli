"""File I/O and system utilities."""

import platform
import shutil
import sys
from pathlib import Path


class NoSpaceError(Exception):
    """Exception raised when there is no space left on device."""

    def __init__(
        self,
        path: str | Path,
        required_bytes: int | None = None,
        available_bytes: int | None = None,
    ):
        self.path = str(path)
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        message = f"No space left on device at {self.path}"
        if required_bytes and available_bytes:
            message += f" (Required: {required_bytes}, Available: {available_bytes})"
        super().__init__(message)


def check_free_space(path: str | Path, required_bytes: int) -> None:
    """Check if there is enough free space at the given path."""
    path_obj = Path(path)
    check_path = path_obj
    while not check_path.exists() and check_path.parent != check_path:
        check_path = check_path.parent

    try:
        usage = shutil.disk_usage(check_path)
        if usage.free < required_bytes:
            raise NoSpaceError(path, required_bytes, usage.free)
    except OSError:
        # If we can't check disk usage (e.g. permission error on parent),
        # we skip the check rather than failing, as the subsequent IO
        # will fail anyway if there's a real problem.
        pass


def get_executable_path() -> Path:
    """Get the path of the current executable (works with PyInstaller)"""
    if getattr(sys, "frozen", False):
        # Running as PyInstaller executable
        return Path(sys.executable)
    else:
        # Running as Python script
        return Path(__file__)


def get_hcli_command() -> list[str]:
    """Return the argv tokens that invoke hcli.

    The result is an *unquoted* list of arguments (executable first), e.g.
    ``["/usr/bin/hcli"]`` or ``["/usr/bin/uv", "run", "hcli"]``. Callers that need a
    single command string must render it with quoting appropriate for the target
    (``subprocess.list2cmdline`` for a Windows command line, ``shlex.join`` for a
    POSIX shell or a macOS/Linux URL-handler template) — never by concatenating the
    tokens raw, which would word-split install paths that contain spaces.
    """
    # Running from a frozen binary: sys.executable is the hcli executable itself.
    if getattr(sys, "frozen", False):
        return [sys.executable]

    # hcli on PATH.
    hcli_path = shutil.which("hcli")
    if hcli_path:
        return [hcli_path]

    # Development environment: run via uv.
    uv_path = shutil.which("uv")
    if uv_path:
        return [uv_path, "run", "hcli"]

    # Fallback: run the module with the active interpreter.
    python_path = shutil.which("python") or shutil.which("python3")
    if python_path:
        return [python_path, "-m", "hcli"]

    raise RuntimeError("Could not find hcli executable")


def get_os() -> str:
    """Get the normalized OS name."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Linux":
        return "linux"
    elif system == "Darwin":
        return "mac"
    else:
        return system.lower()


def get_arch() -> str:
    """Get the system architecture."""
    machine = platform.machine().lower()
    # Normalize common architecture names
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    elif machine in ("arm64", "aarch64", "arm"):
        return "arm64"
    else:
        return platform.machine()


def get_tag_os() -> str:
    """Get the current OS in the format used by asset tags.

    Returns OS identifier in format: {arch}{os}
    Examples: x64win, x64linux, x64mac, armmac, armwin, armlinux
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    # Determine architecture
    is_arm = machine in ("arm64", "aarch64", "arm")
    arch_prefix = "arm" if is_arm else "x64"

    # Determine OS
    if system == "darwin":
        os_suffix = "mac"
    elif system == "linux":
        os_suffix = "linux"
    elif system == "windows":
        os_suffix = "win"
    else:
        # Default to linux if unknown
        os_suffix = "linux"

    return f"{arch_prefix}{os_suffix}"
