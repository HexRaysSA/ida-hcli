"""IDA Pro utilities for installation and path management."""

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from hcli.env import ENV
from hcli.lib.util.cache import get_cache_directory
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


@dataclass
@total_ordering
class IdaProduct:
    product: str
    major: int
    minor: int
    suffix: str | None = None

    @classmethod
    def from_installer_filename(cls, filename: str):
        """Parse IDA installer filename to extract version information.

        Args:
            filename: IDA installer filename (e.g., 'ida-pro_92_x64linux.run')

        Raises:
            ValueError: If filename format is not recognized
        """
        basename = filename
        for ext in [".app.zip", ".run", ".exe"]:
            if basename.endswith(ext):
                basename = basename[: -len(ext)]
                break

        # filename pattern: ida-{product}_{version}_{platform}
        match = re.match(r"^ida-([^_]+)_(\d{2})(sp\d+)?_", basename)
        if not match:
            raise ValueError(f"Unrecognized installer filename format: {filename}")

        product_part = match.group(1)  # like: pro, home-pc, essential
        version_major = int(match.group(2)[0])  # like: 9
        version_minor = int(match.group(2)[1])  # like: 1
        suffix = match.group(3) if match.group(3) else None  # like: sp1

        product_mapping = {
            "pro": "IDA Professional",
            "home-pc": "IDA Home",
            "home-arm": "IDA Home",
            "home-mips": "IDA Home",
            "home-ppc": "IDA Home",
            "home-riscv": "IDA Home",
            "free-pc": "IDA Free",
            "essential": "IDA Essential",
            "classroom-free": "IDA Classroom",
        }

        product = product_mapping.get(product_part, f"IDA {product_part.title()}")
        return cls(product, version_major, version_minor, suffix)

    def __str__(self):
        base = f"{self.product} {self.major}.{self.minor}"
        return f"{base}{self.suffix}" if self.suffix else base

    def __lt__(self, other):
        if not isinstance(other, IdaProduct):
            return NotImplemented
        return (self.product, self.major, self.minor, self.suffix or "") < (
            other.product,
            other.major,
            other.minor,
            other.suffix or "",
        )


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
    if ENV.HCLI_IDAUSR is not None:
        return Path(ENV.HCLI_IDAUSR)
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    idausr = os.environ.get("HCLI_IDAUSR")
    if idausr:
        return Path(idausr)

    # Check for standard IDAUSR environment variable
    idausr = os.environ.get("IDAUSR")
    if idausr is not None:
        return Path(idausr)

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


def get_default_ida_install_directory(ver: IdaProduct) -> Path:
    """Get the default installation directory for IDA Pro."""

    # like "IDA Professional 9.1sp1"
    app_directory_name = str(ver)

    if get_os() == "windows":
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / app_directory_name
    elif get_os() == "linux":
        return get_user_home_dir() / ".local" / "share" / "applications" / app_directory_name
    elif get_os() == "mac":
        return Path("/Applications/") / f"{app_directory_name}.app"
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


def install_license(license_path: Path, target_path: Path) -> None:
    """Install a license file to an IDA directory."""
    target_file = target_path / license_path.name
    shutil.copy2(license_path, target_file)


def get_license_dir(ida_dir: Path) -> Path:
    """Get the license directory for an IDA installation."""
    return get_ida_path(ida_dir)


def accept_eula(install_dir: Path) -> None:
    # Accept the EULA (to be persistent across runs - you need to mount $HOME/.idapro as a volume)
    os.environ["IDADIR"] = str(install_dir)
    try:
        if True:
            # force this to be imported first and not reordered by ruff
            import idapro  # noqa: F401

        import ida_registry
    except Exception:
        raise RuntimeError("idalib not available")

    ida_registry.reg_write_int("EULA 90", 1)
    ida_registry.reg_write_int("EULA 91", 1)
    ida_registry.reg_write_int("EULA 92", 1)
    logger.info("EULA accepted")


def install_ida(installer: Path, install_dir: Path):
    """
    Install IDA Pro from an installer.

    Args:
      installer: path to the installer downloaded from the Hex-Rays portal.
      install_dir: path to the installation directory, which should not already exist.

    Installation directory should look like:
      - %Program Files%\\IDA Professional 9.1\\
      - /Applications/IDA Professional 9.1.app/
      - /opt/ida-9.1/
      - /tmp/ida-9.1/
      - ...
    """
    if install_dir.exists():
        raise ValueError("failed to install: destination directory already exists")

    logger.info(f"Installing IDA in {install_dir}")
    install_dir.mkdir(parents=True, exist_ok=False)

    try:
        current_os = get_os()
        if current_os == "mac":
            _install_ida_mac(installer, install_dir)
        elif current_os == "linux":
            _install_ida_unix(installer, install_dir)
        elif current_os == "windows":
            _install_ida_windows(installer, install_dir)
        else:
            raise ValueError(f"unsupported OS: {current_os}")
    except Exception as e:
        logger.error(f"Installation failed: {e}")
        raise

    contents = list(install_dir.iterdir())
    logger.debug("installed contents: %s", contents)
    if not len(contents):
        raise RuntimeError("installation failed: installation directory contents not created")

    has_ida_hlp = False
    for _, _, files in os.walk(install_dir):
        if "ida.hlp" in files:
            has_ida_hlp = True

    if not has_ida_hlp:
        raise RuntimeError("installation failed: ida.hlp not created")


def _install_ida_mac(installer: Path, prefix: Path) -> None:
    """Install IDA on macOS."""
    if not shutil.which("unzip"):
        raise RuntimeError("unzip is required to install IDA on macOS")

    with tempfile.TemporaryDirectory(prefix="hcli_") as temp_unpack_dir:
        with tempfile.TemporaryDirectory(prefix="hcli_") as temp_install_dir:
            logger.info(f"Unpacking installer to {temp_unpack_dir}...")

            # Unpack the installer
            process = subprocess.run(["unzip", "-qq", str(installer), "-d", temp_unpack_dir], capture_output=True)

            if process.returncode != 0:
                raise RuntimeError("Failed to unpack installer")

            entries = list(Path(temp_unpack_dir).iterdir())
            if len(entries) != 1:
                raise ValueError(f"unexpected contents of zip archive: {len(entries)} root directories")

            # typically this is the app name, like `ida-pro_90_armmac.app`
            # however the directory name might not be precisely the same as the zip archive filename
            # such as in SP releases.
            app_name = entries[0]

            installer_path = None
            for platform in ("osx-arm64", "osx-x86_64"):
                candidate_path = Path(temp_unpack_dir) / app_name / "Contents" / "MacOS" / platform
                if candidate_path.exists():
                    installer_path = candidate_path
                    break

            if not installer_path:
                raise RuntimeError("Installer executable not found")

            logger.info(f"Running installer {app_name}...")
            temp_install_path = Path(temp_install_dir)
            args = _get_installer_args(temp_install_path)

            process = subprocess.run([str(installer_path)] + args, capture_output=True)

            if process.returncode != 0:
                raise RuntimeError("Installer execution failed")

            # Find installed folder and copy to prefix
            installed_folders = list(temp_install_path.iterdir())

            if not installed_folders:
                raise RuntimeError("No installation found after running installer")

            install_folder = installed_folders[0]
            _copy_dir(install_folder, prefix)


def _install_ida_unix(installer: Path, prefix: Path) -> None:
    """Install IDA on Unix/Linux."""
    args = _get_installer_args(prefix)

    installer_path = Path(installer)

    # If installer is not absolute and has no directory component, prefix with './'
    if not installer_path.is_absolute() and installer_path.parent == Path("."):
        installer_path = Path(f"./{installer}")

    if not os.access(installer_path, os.X_OK):
        logger.info(f"Setting executable permission on {installer_path}")
        current_mode = os.stat(installer_path).st_mode
        os.chmod(installer_path, current_mode | stat.S_IXUSR)

    home_dir = get_user_home_dir()
    share_dir = Path(home_dir) / ".local" / "share" / "applications"
    share_dir.mkdir(parents=True, exist_ok=True)

    process = subprocess.run([str(installer_path)] + args, capture_output=True)

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


def _install_ida_windows(installer: Path, prefix: Path) -> None:
    """Install IDA on Windows."""
    args = _get_installer_args(prefix)

    process = subprocess.run(["cmd", "/c", str(installer)] + args, capture_output=True)

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


def _copy_dir(src_path: Path, dest_path: Path) -> None:
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


class PathsConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    # like: "/Applications/IDA Professional 9.1.app/Contents/MacOS"
    installation_directory: Path | None = Field(alias="ida-install-dir", default=None)


class PluginRepositoryConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    url: str = Field(
        default="https://raw.githubusercontent.com/HexRaysSA/plugin-repository/refs/heads/v1/plugin-repository.json",
    )


class SettingsConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    # plugin name -> key -> value
    plugins: dict[str, dict[str, str | int]] = Field(default_factory=dict)
    plugin_repository: PluginRepositoryConfig = Field(
        alias="plugin-repository", default_factory=lambda: PluginRepositoryConfig()
    )


# describes contents of IDAUSR/ida-config.json
class IDAConfigJson(BaseModel):
    """IDA configuration $IDAUSR/ida-config.json"""

    paths: PathsConfig = Field(alias="Paths", default_factory=lambda: PathsConfig())
    settings: SettingsConfig = Field(alias="Settings", default_factory=lambda: SettingsConfig())


def get_ida_config_path() -> Path:
    idausr = get_ida_user_dir()

    return Path(idausr) / "ida-config.json"


def get_ida_config() -> IDAConfigJson:
    ida_config_path = get_ida_config_path()
    if not ida_config_path.exists():
        logger.debug("using default ida-config.json contents")
        return IDAConfigJson()

    return IDAConfigJson.model_validate_json(ida_config_path.read_text(encoding="utf-8"))


def find_current_ida_install_directory() -> Path:
    if ENV.HCLI_CURRENT_IDA_INSTALL_DIR is not None:
        return Path(ENV.HCLI_CURRENT_IDA_INSTALL_DIR)
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR")
    if env:
        return Path(env)

    config = get_ida_config()
    if not config.paths.installation_directory:
        raise ValueError("failed to determine current IDA installation directory")

    if not config.paths.installation_directory.exists():
        raise ValueError("ida-config.json invalid: ida-install-dir doesn't exist")

    logger.debug("current IDA installation: %s", config.paths.installation_directory)
    return config.paths.installation_directory


def find_current_idat_executable() -> Path:
    install_directory = find_current_ida_install_directory()
    current_os = get_os()
    if current_os == "mac" and install_directory.name == "MacOS":
        # strip off trailing Contents/MacOS
        install_directory = install_directory.parent.parent

    return get_idat_path(install_directory)


def run_py_in_current_idapython(src: str) -> str:
    idat_path = find_current_idat_executable()
    if not idat_path.exists():
        raise ValueError(f"can't find idat: {idat_path}")

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

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        logger.debug(f"idat command: {' '.join(cmd)}")
        logger.debug(f"idat exit code: {result.returncode}")
        if result.stdout:
            logger.debug(f"idat stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"idat stderr: {result.stderr}")

        if not log_path.exists():
            raise RuntimeError(f"Log file was not created: {log_path}")

        for line in log_path.read_text().splitlines():
            if not line.startswith("__hcli__:"):
                continue

            return json.loads(line[len("__hcli__:") :])

        raise RuntimeError("Could not find __hcli__: prefix in log output")


def get_current_ida_platform_cache_path() -> Path:
    return get_cache_directory("current-ida") / "platform.json"


def set_current_ida_platform_cache(ida_path: Path, platform: str) -> None:
    cache_path = get_current_ida_platform_cache_path()
    if cache_path.exists():
        doc = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        doc = {}
    doc[str(ida_path.absolute())] = platform
    cache_path.write_text(json.dumps(doc), encoding="utf-8")


def get_current_ida_platform_cache(ida_path: Path) -> str:
    cache_path = get_current_ida_platform_cache_path()
    if not cache_path.exists():
        raise KeyError(f"No platform cache found for {ida_path}")

    doc = json.loads(cache_path.read_text(encoding="utf-8"))
    return doc[str(ida_path.absolute())]


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
    raise ValueError(f"Unsupported OS: {system}")
print("__hcli__:" + json.dumps(plat))
sys.exit()
"""


def find_current_ida_platform() -> str:
    """find the platform associated with the current IDA installation"""
    if ENV.HCLI_CURRENT_IDA_PLATFORM is not None:
        return ENV.HCLI_CURRENT_IDA_PLATFORM
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_PLATFORM")
    if env:
        return env

    os_ = get_os()
    if os_ == "windows":
        return "windows-x86_64"
    elif os_ == "linux":
        return "linux-x86_64"
    elif os_ == "mac":
        ida_dir = find_current_ida_install_directory()
        try:
            return get_current_ida_platform_cache(ida_dir)
        except KeyError:
            pass
        platform = run_py_in_current_idapython(FIND_PLATFORM_PY)
        set_current_ida_platform_cache(ida_dir, platform)
        return platform
    else:
        raise ValueError(f"Unsupported OS: {os_}")


def get_current_ida_version_cache_path() -> Path:
    return get_cache_directory("current-ida") / "version.json"


def set_current_ida_version_cache(ida_path: Path, version: str) -> None:
    cache_path = get_current_ida_version_cache_path()
    if cache_path.exists():
        doc = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        doc = {}
    doc[str(ida_path.absolute())] = version
    cache_path.write_text(json.dumps(doc), encoding="utf-8")


def get_current_ida_version_cache(ida_path: Path) -> str:
    cache_path = get_current_ida_version_cache_path()
    if not cache_path.exists():
        raise KeyError(f"No version cache found for {ida_path}")

    doc = json.loads(cache_path.read_text(encoding="utf-8"))
    return doc[str(ida_path.absolute())]


FIND_VERSION_PY = """
# output like:
#
#     __hcli__:"9.1"
import sys
import json
import ida_kernwin
print("__hcli__:" + json.dumps(ida_kernwin.get_kernel_version()))
sys.exit()
"""


def find_current_ida_version() -> str:
    """find the version of the current IDA installation, like '9.1'"""
    if ENV.HCLI_CURRENT_IDA_VERSION is not None:
        return ENV.HCLI_CURRENT_IDA_VERSION
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_VERSION")
    if env:
        return env

    ida_dir = find_current_ida_install_directory()
    try:
        return get_current_ida_version_cache(ida_dir)
    except KeyError:
        pass
    version = run_py_in_current_idapython(FIND_VERSION_PY)
    set_current_ida_version_cache(ida_dir, version)
    return version


def generate_instance_name(path: Path) -> str:
    """Generate a reasonable instance name from installation path."""
    # For macOS: "IDA Professional 9.2.app" -> "ida-pro-9.2"
    # For others: "IDA Professional 9.2" -> "ida-pro-9.2"
    name = path.name

    # Remove .app extension for macOS
    if name.endswith(".app"):
        name = name[:-4]

    # Convert to lowercase and replace spaces with dashes
    name = name.lower().replace(" ", "-")

    # Shorten common patterns
    name = name.replace("ida-professional", "ida-pro")
    name = name.replace("ida-home", "ida-home")
    name = name.replace("ida-free", "ida-free")

    return name


def add_instance_to_config(name: str, path: Path) -> bool:
    """Add an IDA instance to the configuration.

    Returns:
        True if the instance was added, False if it already exists.
    """
    from hcli.lib.config import config_store

    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}

    if name in instances:
        return False  # Already exists

    # Store the absolute path as string
    instances[name] = str(path.absolute())

    # Save back to config
    config_store.set_object("ke.ida.instances", instances)

    return True
