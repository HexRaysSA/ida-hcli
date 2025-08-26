"""Python detection and utility functions."""

import asyncio
import asyncio.subprocess
import tempfile
from pathlib import Path


async def get_python_lib() -> str | None:
    """Get the Python library path using the find_libpython.py script."""
    python_bin = await get_python_bin()
    if not python_bin:
        return None

    with tempfile.TemporaryDirectory(prefix="hcli_") as temp_dir:
        try:
            # Get the find_libpython.py script path
            script_path = _get_find_libpython_script()
            if not script_path or not script_path.exists():
                return None

            # Run the script
            process = await asyncio.create_subprocess_exec(
                python_bin,
                str(script_path),
                cwd=temp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return stdout.decode("utf-8").strip()

            return None

        except Exception:
            return None


async def get_python_bin() -> str | None:
    """Get the available Python binary."""
    if await _is_python_available("python"):
        return "python"
    elif await _is_python_available("python3"):
        return "python3"
    return None


async def _is_python_available(binary: str = "python") -> bool:
    """Check if a Python binary is available and working."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()
        return process.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _get_find_libpython_script() -> Path | None:
    """Get the path to the find_libpython.py script."""
    # Try to find the script in the include directory
    # This assumes the script is in the include directory relative to the project root
    current_dir = Path(__file__).parent

    # Navigate up to find the include directory
    for parent in [current_dir] + list(current_dir.parents):
        include_dir = parent / "include"
        if include_dir.exists():
            script_path = include_dir / "find_libpython.py"
            if script_path.exists():
                return script_path

    # Try alternative locations
    script_locations = [
        Path(__file__).parent.parent.parent.parent.parent / "include" / "find_libpython.py",
        Path.cwd() / "include" / "find_libpython.py",
        Path.cwd() / "src" / "include" / "find_libpython.py",
    ]

    for location in script_locations:
        if location.exists():
            return location

    return None


async def get_python_version(binary: str = "python") -> str | None:
    """Get the version of a Python binary."""
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            # Python version is usually printed to stdout, but some versions use stderr
            version_output = stdout.decode("utf-8").strip()
            if not version_output:
                version_output = stderr.decode("utf-8").strip()

            # Extract version number (e.g., "Python 3.9.1" -> "3.9.1")
            if version_output.startswith("Python "):
                return version_output[7:]

            return version_output

        return None

    except (FileNotFoundError, OSError):
        return None


async def check_python_requirements(min_version: str = "3.7") -> bool:
    """Check if Python meets minimum version requirements."""
    python_bin = await get_python_bin()
    if not python_bin:
        return False

    version = await get_python_version(python_bin)
    if not version:
        return False

    try:
        # Parse version strings and compare
        def parse_version(v: str) -> tuple:
            return tuple(map(int, v.split(".")[:3]))  # Take first 3 components

        current_version = parse_version(version)
        required_version = parse_version(min_version)

        return current_version >= required_version

    except (ValueError, IndexError):
        return False


async def get_python_info() -> dict:
    """Get comprehensive Python information."""
    python_bin = await get_python_bin()

    info = {
        "binary": python_bin,
        "version": None,
        "library": None,
        "available": python_bin is not None,
    }

    if python_bin:
        info["version"] = await get_python_version(python_bin)
        info["library"] = await get_python_lib()

    return info


async def find_python_executables() -> list:
    """Find all available Python executables on the system."""
    candidates = [
        "python",
        "python3",
        "python3.9",
        "python3.10",
        "python3.11",
        "python3.12",
    ]
    available = []

    for candidate in candidates:
        if await _is_python_available(candidate):
            version = await get_python_version(candidate)
            available.append({"binary": candidate, "version": version})

    return available


async def get_python_lib_for_binary(binary: str) -> str | None:
    """Get the Python library path for a specific Python binary."""
    if not await _is_python_available(binary):
        return None

    with tempfile.TemporaryDirectory(prefix="hcli_") as temp_dir:
        try:
            # Get the find_libpython.py script path
            script_path = _get_find_libpython_script()
            if not script_path or not script_path.exists():
                return None

            # Run the script with the specific binary
            process = await asyncio.create_subprocess_exec(
                binary,
                str(script_path),
                cwd=temp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return stdout.decode("utf-8").strip()

            return None

        except Exception:
            return None
