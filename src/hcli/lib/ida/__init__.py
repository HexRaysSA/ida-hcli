"""IDA Pro utilities for installation and path management."""

import asyncio
import asyncio.subprocess
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from functools import total_ordering
from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import AliasPath, BaseModel, Field

from hcli.lib.util.io import get_os

logger = logging.getLogger(__name__)


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

        raise ValueError("Unrecognized format")

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


def get_ida_user_dir() -> Path:
    """Get the IDA Pro user directory."""
    if "HCLI_IDAUSR" in os.environ:
        return Path(os.environ["HCLI_IDAUSR"])

    os_ = get_os()
    if os_ == "windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ValueError("Failed to determine %APPDATA% location: environment variable not set")

        return Path(appdata) / "Hex-Rays" / "IDA Pro"
    elif os_ in ("linux", "mac"):
        home = os.environ.get("HOME")
        if not home:
            raise ValueError("Failed to determine home directory: environment variable not set")
        return Path(home) / ".idapro"
    else:
        raise ValueError(f"Unsupported operating system: {os}")


def get_user_home_dir() -> Path:
    """Get the user home directory."""
    os_ = get_os()
    if os_ == "windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ValueError("Failed to determine %APPDATA% location: environment variable not set")

        return Path(appdata)
    elif os_ in ("linux", "mac"):
        home = os.environ.get("HOME")
        if not home:
            raise ValueError("Failed to determine home directory: environment variable not set")
        return Path(home)
    else:
        raise ValueError(f"Unsupported operating system: {os}")


def get_ida_install_default_prefix(ver: IdaVersion) -> Path:
    """Get the default installation prefix for IDA Pro."""
    if get_os() == "windows":
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    elif get_os() == "linux":
        return get_user_home_dir() or Path(tempfile.gettempdir())
    elif get_os() == "mac":
        return Path(f"/Applications/IDA Professional {ver}")
    else:
        raise ValueError(f"Unsupported operating system: {os}")


def get_ida_path(ida_dir: Path) -> Path:
    """Get the IDA application path from the installation directory."""
    if get_os() == "mac":
        return Path(ida_dir) / "Contents" / "MacOS"
    else:
        return Path(ida_dir)


def get_ida_binary_path(ida_dir: Path, suffix: str = "") -> Path:
    """Get the IDA binary path."""
    if get_os() == "windows":
        return Path(get_ida_path(ida_dir)) / f"ida{suffix}.exe"
    else:
        return Path(get_ida_path(ida_dir)) / f"ida{suffix}"


def get_idat_path(ida_dir: Path) -> Path:
    """Get the IDA text-mode (idat) executable path."""
    return get_ida_binary_path(ida_dir, "t")


def find_standard_windows_installations() -> list[Path]:
    """Find standard IDA Pro installations on Windows."""
    ret = []

    base_directory = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))

    # Check the base directory for IDA installations
    if base_directory.exists():
        for entry in base_directory.iterdir():
            if not entry.is_dir():
                continue

            if not entry.name.startswith("IDA Pro"):
                continue

            ret.append(entry)

    return ret


def find_standard_linux_installations() -> list[Path]:
    """Find standard IDA Pro installations on Linux."""
    # TODO: can also look in registered XDG applications, or maybe in /opt
    ret = []
    base_directory = get_user_home_dir() / ".local" / "share" / "applications"

    if base_directory.exists():
        for entry in base_directory.iterdir():
            if not entry.is_dir():
                continue

            if not entry.name.startswith("IDA Pro"):
                continue

            ret.append(entry)

    return ret


def find_standard_mac_installations() -> list[Path]:
    """Find standard IDA Pro installations on macOS."""
    ret = []

    base_directory = Path("/Applications")

    # Check the base directory for IDA installations
    if base_directory.exists():
        for entry in base_directory.iterdir():
            if not entry.is_dir():
                continue

            if not entry.name.startswith("IDA Pro"):
                continue

            ret.append(entry)

    return ret


def find_standard_installations() -> list[Path]:
    """Find standard IDA Pro installations."""
    ret = set()

    try:
        ret.add(find_current_ida_install_directory())
    except ValueError:
        pass

    if get_os() == "windows":
        ret.update(find_standard_windows_installations())
    elif get_os() == "linux":
        ret.update(find_standard_linux_installations())
    elif get_os() == "mac":
        ret.update(find_standard_mac_installations())
    else:
        raise ValueError(f"Unsupported operating system: {os}")

    return list(ret)


def is_ida_dir(ida_dir: Path) -> bool:
    """Check if a directory contains a valid IDA installation."""
    binary_path = Path(get_ida_binary_path(ida_dir))
    return binary_path.exists()


async def install_license(license_path: Path, target_path: Path) -> None:
    """Install a license file to an IDA directory."""
    target_file = target_path / license_path.name
    shutil.copy2(license_path, target_file)


def get_license_dir(ida_dir: Path) -> Path:
    """Get the license directory for an IDA installation."""
    return get_ida_path(ida_dir)


def accept_eula(install_dir: Path) -> None:
    # Accept the EULA (to be persistent across runs - you need to mount $HOME/.idapro as a volume)
    os.environ["IDADIR"] = str(install_dir)
    import ida_domain  # noqa: F401
    import ida_registry

    ida_registry.reg_write_int("EULA 90", 1)
    ida_registry.reg_write_int("EULA 91", 1)
    ida_registry.reg_write_int("EULA 92", 1)
    print("EULA accepted")


async def install_ida(installer: Path, install_dir: Optional[Path]) -> Optional[Path]:
    """
    Install IDA Pro from an installer.

    Returns the path to the installed IDA directory, or None if installation failed.
    """
    if not install_dir:
        prefix = get_ida_install_default_prefix(IdaVersion.from_basename(installer.name))
    else:
        prefix = install_dir

    prefix_path = prefix

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
        return prefix_path / next(iter(new_folders))

    return None


async def _install_ida_mac(installer: Path, prefix: Path) -> None:
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
            app_name = installer.stem  # Remove .zip extension
            installer_path = Path(temp_unpack_dir) / app_name / "Contents" / "MacOS" / "osx-arm64"

            if not installer_path.exists():
                raise RuntimeError("Installer executable not found")

            print(f"Running installer {app_name}...")
            temp_install_path = Path(temp_install_dir)
            args = _get_installer_args(temp_install_path)

            process = await asyncio.create_subprocess_exec(str(installer_path), *args)
            await process.communicate()

            if process.returncode != 0:
                raise RuntimeError("Installer execution failed")

            # Find installed folder and copy to prefix
            installed_folders = list(temp_install_path.iterdir())

            if not installed_folders:
                raise RuntimeError("No installation found after running installer")

            install_folder = installed_folders[0]
            await _copy_dir(install_folder, prefix)


async def _install_ida_unix(installer: Path, prefix: Path) -> None:
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
    share_dir = Path(home_dir) / ".local" / "share" / "applications"
    share_dir.mkdir(parents=True, exist_ok=True)

    process = await asyncio.create_subprocess_exec(
        installer_path, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


async def _install_ida_windows(installer: Path, prefix: Path) -> None:
    """Install IDA on Windows."""
    args = _get_installer_args(prefix)

    process = await asyncio.create_subprocess_exec("cmd", "/c", installer, *args)
    await process.communicate()

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


def _get_installer_args(prefix: Path) -> list[str]:
    """Get installer arguments."""
    args = ["--mode", "unattended", "--debugtrace", "debug.log"]

    if get_os() == "windows":
        args.extend(["--install_python", "0"])

    if prefix:
        args.extend(["--prefix", str(prefix)])

    return args


async def _copy_dir(src_path: Path, dest_path: Path) -> None:
    """Copy directory recursively."""
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


# describes contents of IDAUSR/ida-config.json
class IDAConfigJson(BaseModel):
    """IDA configuration $IDAUSR/ida-config.json"""

    # like: "/Applications/IDA Professional 9.1.app/Contents/MacOS"
    installation_directory: Path = Field(validation_alias=AliasPath("Paths", "ida-install-dir"))


def get_ida_config_path() -> Path:
    idausr = get_ida_user_dir()
    if not idausr:
        raise ValueError("$IDAUSR doesn't exist")

    return Path(idausr) / "ida-config.json"


def get_ida_config() -> IDAConfigJson:
    ida_config_path = get_ida_config_path()
    if not ida_config_path.exists():
        raise ValueError("ida-config.json doesn't exist")

    config = IDAConfigJson.model_validate_json(ida_config_path.read_text(encoding="utf-8"))

    if not config.installation_directory.exists():
        raise ValueError("ida-config.json invalid: ida-install-dir doesn't exist")

    return config


def find_current_ida_install_directory() -> Path:
    config = get_ida_config()
    logger.debug("current IDA installation: %s", config.installation_directory)
    return config.installation_directory


def find_current_idat_executable() -> Path:
    install_directory = find_current_ida_install_directory()

    os = get_os()
    if os == "windows":
        idat_path = install_directory / "idat.exe"
    elif os in ("linux", "mac"):
        idat_path = install_directory / "idat"
    else:
        raise NotImplementedError(f"os not supported: {os}")

    logger.debug("idat path: %s", idat_path)

    return idat_path


def run_py_in_current_idapython(src: str) -> str:
    idat_path = find_current_idat_executable()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        script_path = temp_path / "idat-script.py"
        log_path = temp_path / "ida.log"

        script_path.write_text(src)

        # invoke like:
        #
        #     idat -a -A -c -t -L"/absolute/path/to/ida.log" -S"/absolute/path/to/idat-script.py"
        #
        # -a disable auto analysis
        # -A autuonomous, no dialogs
        # -c delete old database
        # -t create an empty database
        # -L"/absolute/path/to/ida.log"
        # -S"/absolute/path/to/script.py"
        cmd = [
            str(idat_path),
            "-a",  # disable auto analysis
            "-A",  # autonomous, no dialogs
            "-c",  # delete old database
            "-t",  # create an empty database
            f"-L{str(log_path.absolute())}",
            f"-S{str(script_path.absolute())}",
        ]

        _ = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.debug(f"idat command: {' '.join(cmd)}")

        if not log_path.exists():
            raise RuntimeError(f"Log file was not created: {log_path}")

        for line in log_path.read_text().splitlines():
            if not line.startswith("__hcli__:"):
                continue

            return json.loads(line[len("__hcli__:") :])

        raise RuntimeError("Could not find __hcli__: prefix in log output")


FIND_PLATFORM_PY = """
# output like:
#
#     __hcli__:"windows-x86_64"
import sys
import json
import platform

system = platform.system()
if system == "Windows":
    plat = "windows-x86_64"
elif system == "Linux":
    plat = "linux-x86_64"
elif system == "Darwin":
    # via: https://stackoverflow.com/questions/7491391/
    version = platform.uname().version
    if "RELEASE_ARM64" in version:
        plat = "macos-aarch64"
    elif "RELEASE_X86_64" in version:
        plat = "macos-x86_64"
    else:
        raise ValueError(f"Unsupported macOS version: {version}")
else:
    raise ValueError(f"Unsupported OS: {os_}")
print("__hcli__:" + json.dumps(plat))
sys.exit()
"""


def find_current_ida_platform() -> str:
    """find the platform associated with the current IDA installation"""
    if "HCLI_CURRENT_PLATFORM" in os.environ:
        return os.environ["HCLI_CURRENT_PLATFORM"]

    return run_py_in_current_idapython(FIND_PLATFORM_PY)


FIND_VERSION_PY = """
# output like:
#
#     __hcli__:"windows-x86_64"
import sys
import json
import ida_kernwin
print("__hcli__:" + json.dumps(ida_kernwin.get_kernel_version()))
sys.exit()
"""


def find_current_ida_version() -> str:
    """find the version of the current IDA installation"""
    if "HCLI_CURRENT_VERSION" in os.environ:
        return os.environ["HCLI_CURRENT_VERSION"]

    return run_py_in_current_idapython(FIND_VERSION_PY)
