"""IDA Pro utilities for installation and path management."""

import asyncio
import os
import re
import shutil
import stat
import tempfile
from functools import total_ordering
from pathlib import Path
from typing import List, NamedTuple, Optional

from hcli.lib.util.io import get_os


class DownloadResource(NamedTuple):
    """IDA download resource information."""

    id: str
    name: str
    description: str
    category: str
    version: str
    os: str
    arch: str


@total_ordering
class IdaVersion:
    def __init__(self, major: int, minor: int, suffix: str | None = None):
        self.major = major
        self.minor = minor
        self.suffix = suffix  # e.g., 'sp1'

    @classmethod
    def from_basename(cls, basename: str):
        if basename.startswith("ida8-") or basename.startswith("ida8_"):
            return cls(8, 4)

        match = re.search(r"_(\d{2})(sp\d+)?_", basename)
        if match:
            major = int(match.group(1)[0])
            minor = int(match.group(1)[1])
            suffix_match = match.group(2)
            suffix = suffix_match if suffix_match else None
            return cls(major, minor, suffix)

        return None  # unrecognized format

    def __str__(self):
        base = f"{self.major}.{self.minor}"
        return f"{base}.{self.suffix}" if self.suffix else base

    def __repr__(self):
        return f"IdaVersion(major={self.major}, minor={self.minor}, suffix={self.suffix!r})"

    def __eq__(self, other):
        if not isinstance(other, IdaVersion):
            return NotImplemented
        return (self.major, self.minor, self.suffix or "") == (other.major, other.minor, other.suffix or "")

    def __lt__(self, other):
        if not isinstance(other, IdaVersion):
            return NotImplemented
        return (self.major, self.minor, self.suffix or "") < (other.major, other.minor, other.suffix or "")


def is_installable(download: DownloadResource) -> bool:
    """Check if a download resource is installable on the current platform."""
    current_os = get_os()
    src = download.id

    return (
        (src.endswith(".app.zip") and current_os == "mac")
        or (src.endswith(".run") and current_os == "linux")
        or (src.endswith(".exe") and current_os == "windows")
    )


def get_ida_user_dir() -> Optional[str]:
    """Get the IDA Pro user directory."""
    if get_os() == "windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return str(Path(appdata) / "Hex-Rays" / "IDA Pro")
    else:
        home = os.environ.get("HOME")
        if home:
            return str(Path(home) / ".idapro")
    return None


def get_user_home_dir() -> Optional[str]:
    """Get the user home directory."""
    if get_os() == "windows":
        return os.environ.get("APPDATA")
    else:
        return os.environ.get("HOME")


def get_ida_install_default_prefix(ver: IdaVersion | None = None) -> str:
    """Get the default installation prefix for IDA Pro."""
    if get_os() == "windows":
        return os.environ.get("ProgramFiles", r"C:\Program Files")
    elif get_os() == "linux":
        import tempfile

        return get_user_home_dir() or tempfile.gettempdir()
    else:  # mac
        return f"/Applications/IDA Professional {ver or IdaVersion(9, 0)}"


async def get_ida_version(ida_dir: str) -> str:
    """Get the version of an IDA installation."""
    with tempfile.TemporaryDirectory(prefix="hcli_") as temp_dir:
        try:
            # Copy version.idc script to temp directory
            version_script = Path(__file__).parent.parent.parent.parent.parent / "include" / "version.idc"
            if not version_script.exists():
                return "Unknown"

            temp_script = Path(temp_dir) / "version.idc"
            shutil.copy2(version_script, temp_script)

            # Run ida with the version script
            idat_path = get_idat_path(ida_dir)
            if not idat_path or not Path(idat_path).exists():
                return "Unknown"

            process = await asyncio.create_subprocess_exec(
                idat_path,
                "-B",
                f"-S{temp_script}",
                str(temp_script),
                cwd=temp_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            await process.communicate()

            output_file = Path(temp_dir) / "ida_version"
            if output_file.exists():
                return output_file.read_text().strip()
            else:
                return "Unknown"

        except Exception:
            return "Unknown"


def get_ida_binary(ida_dir: str, suffix: str = "") -> str:
    """Get the IDA binary path."""
    if get_os() == "windows":
        return str(Path(get_ida_path(ida_dir)) / f"ida{suffix}.exe")
    else:
        return str(Path(get_ida_path(ida_dir)) / f"ida{suffix}")


def get_ida_path(ida_dir: str, suffix: str = "") -> str:
    """Get the IDA executable path."""
    if get_os() == "mac":
        return str(Path(ida_dir) / "Contents" / "MacOS")
    else:
        return str(Path(ida_dir))


def get_idat_path(ida_dir: str) -> str:
    """Get the IDA text-mode (idat) executable path."""
    return get_ida_path(ida_dir, "t")


def find_standard_installations() -> List[str]:
    """Find standard IDA Pro installations."""
    prefix = get_ida_install_default_prefix()
    prefix_path = Path(prefix)

    if not prefix_path.exists():
        return []

    ida_dirs = []
    try:
        for entry in prefix_path.iterdir():
            if entry.is_dir() and _is_ida_dir_entry(entry):
                if get_os() == "mac":
                    ida_dirs.append(str(entry / "Contents" / "MacOS"))
                else:
                    ida_dirs.append(str(entry))
    except PermissionError:
        pass

    return ida_dirs


def _is_ida_dir_entry(path: Path) -> bool:
    """Check if a directory entry looks like an IDA installation."""
    name = path.name.lower()
    return "ida" in name and "pro" in name


def is_ida_dir(ida_dir: str) -> bool:
    """Check if a directory contains a valid IDA installation."""
    binary_path = Path(get_ida_binary(ida_dir))
    return binary_path.exists()


async def install_license(license_file: str, target_path: str) -> None:
    """Install a license file to an IDA directory."""
    license_path = Path(license_file)
    target_file = str(Path(target_path) / license_path.name)
    shutil.copy2(license_path, target_file)


def get_license_dir(ida_dir: str) -> str:
    """Get the license directory for an IDA installation."""
    if get_os() == "mac":
        return str(Path(ida_dir) / "Contents" / "MacOS")
    else:
        return ida_dir


def accept_eula(install_dir: str) -> None:
    # Accept the EULA (to be persistent across runs - you need to mount $HOME/.idapro as a volume)
    os.environ["IDADIR"] = install_dir
    import ida_domain  # noqa: F401
    import ida_registry

    ida_registry.reg_write_int("EULA 90", 1)
    ida_registry.reg_write_int("EULA 91", 1)
    ida_registry.reg_write_int("EULA 92", 1)
    print("EULA accepted")


async def install_ida(installer: str, install_dir: Optional[str]) -> Optional[str]:
    """
    Install IDA Pro from an installer.

    Returns the path to the installed IDA directory, or None if installation failed.
    """
    # Determine installation prefix
    if not install_dir:
        prefix = get_ida_install_default_prefix(IdaVersion.from_basename(installer))
    else:
        prefix = install_dir

    prefix_path = Path(prefix)

    print(f"Installing IDA in {prefix}")

    # Create prefix directory if it doesn't exist
    prefix_path.mkdir(parents=True, exist_ok=True)

    # List directories before installation
    folders_before = set()
    if prefix_path.exists():
        try:
            folders_before = {item.name for item in prefix_path.iterdir() if item.is_dir()}
        except PermissionError:
            pass

    # Install based on OS
    try:
        current_os = get_os()
        if current_os == "mac":
            await _install_ida_mac(installer, prefix)
        elif current_os == "linux":
            await _install_ida_unix(installer, prefix)
        elif current_os == "windows":
            await _install_ida_windows(installer, prefix)
        else:
            print("Unsupported OS")
            return None
    except Exception as e:
        print(f"Installation failed: {e}")
        return None

    # Find newly created directories
    folders_after = set()
    if prefix_path.exists():
        try:
            folders_after = {item.name for item in prefix_path.iterdir() if item.is_dir()}
        except PermissionError:
            pass

    new_folders = folders_after - folders_before
    if new_folders:
        return str(prefix_path / next(iter(new_folders)))

    return None


async def _install_ida_mac(installer: str, prefix: str) -> None:
    """Install IDA on macOS."""
    if not shutil.which("unzip"):
        raise RuntimeError("unzip is required to install IDA on macOS")

    with tempfile.TemporaryDirectory(prefix="hcli_") as temp_unpack_dir:
        with tempfile.TemporaryDirectory(prefix="hcli_") as temp_install_dir:
            print(f"Unpacking installer to {temp_unpack_dir}...")

            # Unpack the installer
            process = await asyncio.create_subprocess_exec("unzip", "-qq", installer, "-d", temp_unpack_dir)
            await process.communicate()

            if process.returncode != 0:
                raise RuntimeError("Failed to unpack installer")

            # Find and run the installer
            app_name = Path(installer).stem  # Remove .zip extension
            installer_path = Path(temp_unpack_dir) / app_name / "Contents" / "MacOS" / "osx-arm64"

            if not installer_path.exists():
                raise RuntimeError("Installer executable not found")

            print(f"Running installer {app_name}...")
            args = _get_installer_args(temp_install_dir)

            process = await asyncio.create_subprocess_exec(str(installer_path), *args)
            await process.communicate()

            if process.returncode != 0:
                raise RuntimeError("Installer execution failed")

            # Find installed folder and copy to prefix
            temp_install_path = Path(temp_install_dir)
            installed_folders = list(temp_install_path.iterdir())

            if not installed_folders:
                raise RuntimeError("No installation found after running installer")

            install_folder = installed_folders[0]
            await _copy_dir(str(install_folder), prefix)


async def _install_ida_unix(installer: str, prefix: str) -> None:
    """Install IDA on Unix/Linux."""
    args = _get_installer_args(prefix)

    installer_path = Path(installer)

    # If installer is not absolute and has no directory component, prefix with './'
    if not installer_path.is_absolute() and installer_path.parent == Path("."):
        installer_path = Path(f"./{installer}")

    if not os.access(installer_path, os.X_OK):
        print(f"Setting executable permission on {installer_path}")
        current_mode = os.stat(installer_path).st_mode
        os.chmod(installer_path, current_mode | stat.S_IXUSR)

    home_dir = get_user_home_dir()
    if not home_dir:
        raise RuntimeError("Could not determine user home directory")
    share_dir = Path(home_dir) / ".local" / "share" / "applications"
    share_dir.mkdir(parents=True, exist_ok=True)

    process = await asyncio.create_subprocess_exec(
        installer_path, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


async def _install_ida_windows(installer: str, prefix: str) -> None:
    """Install IDA on Windows."""
    args = _get_installer_args(prefix)

    process = await asyncio.create_subprocess_exec("cmd", "/c", installer, *args)
    await process.communicate()

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


def _get_installer_args(prefix: str) -> List[str]:
    """Get installer arguments."""
    args = ["--mode", "unattended", "--debugtrace", "debug.log"]

    if get_os() == "windows":
        args.extend(["--install_python", "0"])

    if prefix:
        args.extend(["--prefix", prefix])

    return args


async def _copy_dir(src: str, dest: str) -> None:
    """Copy directory recursively."""
    src_path = Path(src)
    dest_path = Path(dest)

    if not src_path.exists():
        return

    dest_path.mkdir(parents=True, exist_ok=True)

    for item in src_path.rglob("*"):
        relative_path = item.relative_to(src_path)
        dest_item = dest_path / relative_path

        if item.is_dir():
            dest_item.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_item)
