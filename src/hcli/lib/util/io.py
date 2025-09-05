"""File I/O and system utilities."""

import asyncio
import asyncio.subprocess
import os
import platform
import shutil
import sys
import webbrowser
from pathlib import Path

from hcli.env import ENV


async def open_url(url: str) -> None:
    """Open a URL in the default browser."""
    if platform.system() == "Windows":
        # Use cmd on Windows to handle special characters
        escaped_url = url.replace("&", "^&")
        process = await asyncio.create_subprocess_exec("cmd", "/c", "start", escaped_url)
        await process.communicate()
    else:
        webbrowser.open(url)


async def is_cmd_available(cmd: str) -> bool:
    """Check if a command is available in the system PATH."""
    try:
        process = await asyncio.create_subprocess_exec(
            cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await process.communicate()
        return True
    except (FileNotFoundError, OSError):
        return False


def file_exists(path: str) -> bool:
    """Check if a file exists and is a file."""
    try:
        path_obj = Path(path)
        return path_obj.exists() and path_obj.is_file()
    except (OSError, ValueError):
        return False


def dir_exists(path: str) -> bool:
    """Check if a directory exists and is a directory."""
    try:
        path_obj = Path(path)
        return path_obj.exists() and path_obj.is_dir()
    except (OSError, ValueError):
        return False


def get_executable_path() -> Path:
    """Get the path of the current executable (works with PyInstaller)"""
    if getattr(sys, "frozen", False):
        # Running as PyInstaller executable
        return Path(sys.executable)
    else:
        # Running as Python script
        return Path(__file__)


def get_binary_name() -> str:
    """Get the binary name for the current platform."""
    if platform.system() == "Windows":
        return f"{ENV.HCLI_BINARY_NAME}.exe"
    else:
        return ENV.HCLI_BINARY_NAME


def get_hcli_executable_path() -> str:
    """Get the path to the hcli executable."""
    # Check if we're running from a frozen binary
    if getattr(sys, "frozen", False):
        return sys.executable

    # Check if hcli is in PATH
    hcli_path = shutil.which("hcli")
    if hcli_path:
        return hcli_path

    # Check if uv is available (development environment)
    uv_path = shutil.which("uv")
    if uv_path:
        return f'"{uv_path}" run hcli'

    # Fallback to python -m hcli
    python_path = shutil.which("python") or shutil.which("python3")
    if python_path:
        return f'"{python_path}" -m hcli'

    raise RuntimeError("Could not find hcli executable")


async def remove_dir(path: str) -> bool:
    """Remove a directory and all its contents."""
    try:
        path_obj = Path(path)
        if path_obj.exists():
            shutil.rmtree(path_obj)
        return not path_obj.exists()
    except (OSError, ValueError):
        return False


async def copy_dir(src: str, dest: str) -> None:
    """Copy a directory recursively."""
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.exists():
        return

    dest_path.mkdir(parents=True, exist_ok=True)

    for item in src_path.rglob("*"):
        try:
            relative_path = item.relative_to(src_path)
            dest_item = dest_path / relative_path

            if item.is_dir():
                dest_item.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                dest_item.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest_item)
        except (OSError, ValueError):
            continue  # Skip problematic files


async def move_dir(src: str, dest: str) -> None:
    """Move a directory from src to dest."""
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.exists():
        return

    try:
        # Check if we can do a simple rename
        if src_path.parent == dest_path.parent:
            src_path.rename(dest_path)
        else:
            # On Windows, check if we're moving across drive boundaries
            if platform.system() == "Windows" and src_path.anchor != dest_path.anchor:
                # Copy then remove for cross-drive moves
                await copy_dir(src, dest)
                await remove_dir(src)
            else:
                src_path.rename(dest_path)
    except OSError:
        # Fallback: copy then remove
        await copy_dir(src, dest)
        await remove_dir(src)


async def read_text_file(path: str) -> str:
    """Read a text file, handling potential BOM and encoding issues."""
    path_obj = Path(path)

    try:
        # Try to read as bytes first
        data = path_obj.read_bytes()

        # Check for UTF-16 LE BOM
        if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xFE:
            # Remove BOM and decode as UTF-16 LE
            return data[2:].decode("utf-16le")
        else:
            # Try UTF-8 first, then fall back to other encodings
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                # Try UTF-16 LE without BOM
                try:
                    return data.decode("utf-16le")
                except UnicodeDecodeError:
                    # Final fallback to latin-1 (will never fail)
                    return data.decode("latin-1")
    except (OSError, ValueError):
        return ""


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
    system = platform.system()
    if system == "Windows":
        return "x86_64"
    elif system == "Linux":
        return platform.machine()
    elif system == "Darwin":
        return "arm64"
    else:
        return platform.machine()


def get_file_size(path: str) -> int:
    """Get the size of a file in bytes."""
    try:
        return Path(path).stat().st_size
    except (OSError, ValueError):
        return 0


def ensure_dir(path: str) -> None:
    """Ensure that a directory exists, creating it if necessary."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        pass


def get_temp_dir() -> str:
    """Get a temporary directory path."""
    import tempfile

    return tempfile.gettempdir()


async def write_text_file(path: str, content: str, encoding: str = "utf-8") -> bool:
    """Write content to a text file."""
    try:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(content, encoding=encoding)
        return True
    except (OSError, ValueError):
        return False


async def write_binary_file(path: str, content: bytes) -> bool:
    """Write binary content to a file."""
    try:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_bytes(content)
        return True
    except (OSError, ValueError):
        return False


def normalize_path(path: str) -> str:
    """Normalize a path for the current platform."""
    return str(Path(path).resolve())


def join_path(*parts: str) -> str:
    """Join path parts using the current platform's separator."""
    return str(Path(*parts))


def get_path_separator() -> str:
    """Get the path separator for the current platform."""
    return os.sep


def get_home_dir() -> str | None:
    """Get the user's home directory."""
    return str(Path.home()) if Path.home() else None


def get_current_dir() -> str:
    """Get the current working directory."""
    return str(Path.cwd())


async def create_temp_file(suffix: str = "", content: str = "") -> str:
    """Create a temporary file and return its path."""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as tmp_file:
        if content:
            tmp_file.write(content)
        return tmp_file.name


async def create_temp_dir(prefix: str = "hcli_") -> str:
    """Create a temporary directory and return its path."""
    import tempfile

    return tempfile.mkdtemp(prefix=prefix)


def is_absolute_path(path: str) -> bool:
    """Check if a path is absolute."""
    return Path(path).is_absolute()


def get_file_extension(path: str) -> str:
    """Get the file extension from a path."""
    return Path(path).suffix


def get_file_name(path: str) -> str:
    """Get the filename from a path."""
    return Path(path).name


def get_file_stem(path: str) -> str:
    """Get the filename without extension from a path."""
    return Path(path).stem


def get_parent_dir(path: str) -> str:
    """Get the parent directory of a path."""
    return str(Path(path).parent)
