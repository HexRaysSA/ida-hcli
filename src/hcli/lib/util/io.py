"""File I/O and system utilities."""

import functools
import os
import platform
import re
import shlex
import shutil
import subprocess
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


# Names of the uv cache buckets that hold the throwaway environments `uvx` (and
# `uv run --with`) execute from, e.g. ~/.cache/uv/archive-v0/<hash>.
_UV_CACHE_BUCKET = re.compile(r"^(archive|environments|builds)-v\d+$")

# Console scripts ida-hcli installs (see [project.scripts] in pyproject.toml).
_CONSOLE_SCRIPT_NAMES = frozenset({"hcli", "ida-hcli"})


def is_uvx_environment() -> bool:
    """Whether hcli runs from a uv cache environment, as created by `uvx ida-hcli`.

    Such an environment is not on PATH and may be garbage-collected by uv at any time,
    so neither a bare `hcli` (which may resolve to some other, older install) nor the
    path of the running script is a durable way to invoke this hcli again.
    """
    return any(_UV_CACHE_BUCKET.match(part) for part in Path(sys.prefix).parts)


def _running_script() -> Path | None:
    """The hcli executable or console script this process was started through, if any."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0:
        return None
    path = Path(argv0)
    name = path.stem if path.suffix.lower() == ".exe" else path.name
    # Rules out `python -m hcli` (argv0 is .../hcli/__main__.py) and hcli imported as a
    # library by some other program, whose argv0 says nothing about how to run hcli.
    if name not in _CONSOLE_SCRIPT_NAMES:
        return None
    # Windows launchers may report argv0 without the .exe suffix.
    for candidate in (path, path.with_name(path.name + ".exe")):
        if candidate.is_file():
            return candidate.absolute()
    return None


def _same_file(a: str | Path, b: str | Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def get_hcli_command() -> list[str]:
    """Return the argv tokens that invoke hcli.

    The result is an *unquoted* list of arguments (executable first), e.g.
    ``["/usr/bin/hcli"]`` or ``["/usr/bin/uv", "run", "hcli"]``. Callers that need a
    single command string must render it with quoting appropriate for the target
    (``subprocess.list2cmdline`` for a Windows command line, ``shlex.join`` for a
    POSIX shell or a macOS/Linux URL-handler template) — never by concatenating the
    tokens raw, which would word-split install paths that contain spaces.

    The hcli that is running wins over whatever `hcli` comes first on PATH: that may
    be an older install than the one the user just invoked.
    """
    # Running from a frozen binary: sys.executable is the hcli executable itself.
    if getattr(sys, "frozen", False):
        return [sys.executable]

    # Running via uvx: the cache environment is ephemeral, so go back through uvx.
    if is_uvx_environment():
        uvx_path = shutil.which("uvx")
        if uvx_path:
            return [uvx_path, "ida-hcli"]

    # The console script this process was launched through.
    script = _running_script()
    if script is not None:
        return [str(script)]

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


def _quote_for_shell(arg: str) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline([arg])
    return shlex.quote(arg)


@functools.cache
def get_hcli_display_command() -> str:
    """How to spell hcli in instructions the user is told to run, e.g. "hcli" or "uvx ida-hcli".

    Mirrors the way the user actually launched hcli, so that suggested follow-up
    commands run this hcli rather than some other (possibly stale) install on PATH,
    or fail with "command not found" because hcli was never installed at all.
    """
    from hcli.env import ENV

    if is_uvx_environment():
        return "uvx ida-hcli"

    script = _running_script()
    if script is None:
        if sys.argv and Path(sys.argv[0]).name == "__main__.py":
            return f"{_quote_for_shell(sys.executable)} -m hcli"
        return ENV.HCLI_BINARY_NAME

    # Prefer the short name when it resolves to this very executable.
    name = script.stem if script.suffix.lower() == ".exe" else script.name
    on_path = shutil.which(name)
    if on_path and _same_file(on_path, script):
        return name
    return _quote_for_shell(str(script))


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
