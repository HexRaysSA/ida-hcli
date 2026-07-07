"""IDA Pro utilities for installation and path management."""

import errno
import json
import logging
import ntpath
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any, Literal, NamedTuple

import rich.console
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field

from hcli.env import ENV
from hcli.lib.ida.version import parse_version_from_ida_binary
from hcli.lib.util.io import NoSpaceError, check_free_space, get_os
from hcli.lib.venv import resolve_user_virtual_env

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


class _WindowsRegistryInstallation(NamedTuple):
    """IDA installation metadata from the Windows uninstall registry."""

    path: Path
    display_name: str
    display_version: str | None


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
            "classroom": "IDA Classroom",
            "essential": "IDA Essential",
            "free": "IDA Free",
            "home-arm": "IDA Home (ARM)",
            "home-mips": "IDA Home (MIPS)",
            "home-pc": "IDA Home (PC)",
            "home-ppc": "IDA Home (PPC)",
            "home-riscv": "IDA Home (RISC-V)",
            # Backwards-compatible aliases used by older portal assets.
            "classroom-free": "IDA Classroom",
            "free-pc": "IDA Free",
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
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    idausr = os.environ.get("HCLI_IDAUSR")
    if idausr:
        return Path(idausr)
    if ENV.HCLI_IDAUSR is not None:
        return Path(ENV.HCLI_IDAUSR)

    if ENV.IDAUSR is not None:
        return Path(ENV.IDAUSR)

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
        raise ValueError(f"Unsupported operating system: {os_}")


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
        raise ValueError(f"Unsupported operating system: {os_}")


def get_default_ida_install_directory(ver: IdaProduct) -> Path:
    """Get the default installation directory for IDA Pro."""

    # like "IDA Professional 9.1sp1"
    app_directory_name = str(ver)

    os_ = get_os()
    if os_ == "windows":
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / app_directory_name
    elif os_ == "linux":
        # workaround for #99: idat from IDA 9.2 on Linux fails to start if the path contains a space.
        # so we avoid using the path component "IDA Professional 9.2" and instead use "IDA-Professional-9.2"
        # which is ugly but works.
        #
        # idat is only used to discover the path to IDA's Python interpreter (when pip
        # dependencies need to be installed). Version and platform are detected statically.
        #
        # see also the warnings in commands/ida/install.py.
        #
        # this is confirmed to be fixed in IDA 9.3 for Linux
        if ver.major == 9 and ver.minor == 2 and " " in app_directory_name:
            # "IDA Professional 9.2" -> "IDA-Professional-9.2"
            sanitized_name = app_directory_name.replace(" ", "-")
            logger.info(
                f"Sanitized installation directory name for IDA 9.2 on Linux: '{app_directory_name}' -> '{sanitized_name}'"
            )
            app_directory_name = sanitized_name

        return get_user_home_dir() / ".local" / "share" / "applications" / app_directory_name
    elif os_ == "mac":
        return Path("/Applications/") / f"{app_directory_name}.app"
    else:
        raise ValueError(f"Unsupported operating system: {os_}")


def _normalize_install_dir(ida_dir: Path) -> Path:
    """Normalize an IDA install directory to the bundle root on macOS.

    The ida-config.json ``ida-install-dir`` may contain either the ``.app``
    bundle path or the inner ``Contents/MacOS`` path (IDA itself writes the
    latter).  Every consumer expects the bundle root, so strip the suffix.
    """
    if get_os() == "mac" and ida_dir.name == "MacOS" and ida_dir.parent.name == "Contents":
        return ida_dir.parent.parent
    return ida_dir


def get_ida_path(ida_dir: Path) -> Path:
    """Get the IDA application path from the installation directory."""
    if get_os() == "mac":
        ida_dir = _normalize_install_dir(ida_dir)
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


def get_idalib_path(ida_dir: Path) -> Path:
    """Get the expected idalib library path for an IDA installation."""
    os_ = get_os()
    if os_ == "windows":
        filename = "idalib.dll"
    elif os_ == "linux":
        filename = "libidalib.so"
    elif os_ == "mac":
        filename = "libidalib.dylib"
    else:
        raise ValueError(f"Unsupported operating system: {os_}")
    return Path(get_ida_path(ida_dir)) / filename


# Edition names as they appear on disk, per the IDA installer (../ida/ida/build/ida.xml).
# Windows install directories and macOS app bundles use ``IDA ${ida_edition} ${version}``;
# Linux install directories use ``ida-${edition}-${version}``.
_IDA_INSTALLER_EDITIONS: dict[str, str] = {
    "pro": "Professional",
    "classroom": "Classroom",
    "essential": "Essential",
    "free": "Free",
    "home-arm": "Home (ARM)",
    "home-mips": "Home (MIPS)",
    "home-pc": "Home (PC)",
    "home-ppc": "Home (PPC)",
    "home-riscv": "Home (RISC-V)",
}

_IDA_VERSION_RE = r"\d+\.\d+(?:sp\d+)?"
_IDA_DISPLAY_DIR_NAME_RE = re.compile(
    r"^IDA (?:" + "|".join(re.escape(e) for e in _IDA_INSTALLER_EDITIONS.values()) + rf") {_IDA_VERSION_RE}$",
)
_IDA_LINUX_DIR_NAME_RE = re.compile(
    r"^ida-(?:" + "|".join(re.escape(e) for e in _IDA_INSTALLER_EDITIONS) + rf")-{_IDA_VERSION_RE}$",
)
# hcli <= 0.12 installed Linux IDA under display-style directory names below
# ~/.local/share/applications; IDA 9.2 used a space-free variant as a workaround.
_IDA_LEGACY_LINUX_DIR_NAME_RE = re.compile(
    r"^IDA[- ](?:"
    + "|".join(re.escape(e).replace(r"\ ", r"[- ]") for e in _IDA_INSTALLER_EDITIONS.values())
    + rf")[- ]{_IDA_VERSION_RE}$",
)


def _is_ida_install_dir_name(name: str) -> bool:
    """Match installer-produced install dir / app-bundle names."""
    name = name.removesuffix(".app")
    return bool(
        _IDA_DISPLAY_DIR_NAME_RE.match(name)
        or _IDA_LINUX_DIR_NAME_RE.match(name)
        or _IDA_LEGACY_LINUX_DIR_NAME_RE.match(name)
    )


def is_idalib_capable_installation(ida_dir: Path) -> bool:
    """Whether an IDA installation has idalib available."""
    return get_idalib_path(ida_dir).exists()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    """Deduplicate by resolved path, preserving first-seen order."""
    seen: set[Path] = set()
    ret: list[Path] = []
    for p in paths:
        try:
            key = p.resolve()
        except OSError:
            key = p
        if key in seen:
            continue
        seen.add(key)
        ret.append(p)
    return ret


def _find_windows_registry_installations() -> list[_WindowsRegistryInstallation]:
    """Read IDA installation metadata from the Add/Remove Programs registry under HKLM.

    The installer writes a key per install under
    ``HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\IDA <edition> <version>``.
    """
    try:
        import winreg as _winreg  # type: ignore[import-not-found]
    except ImportError:
        return []

    winreg: Any = _winreg
    ret: list[_WindowsRegistryInstallation] = []
    uninstall_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall_path, 0, winreg.KEY_READ)
    except OSError:
        return ret

    try:
        i = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, subkey_name, 0, winreg.KEY_READ) as subkey:
                    try:
                        display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                    except OSError:
                        continue
                    if not isinstance(display_name, str) or not _is_ida_install_dir_name(display_name):
                        continue
                    try:
                        install_location, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                    except OSError:
                        continue
                    if not install_location:
                        continue
                    display_version = None
                    try:
                        raw_display_version, _ = winreg.QueryValueEx(subkey, "DisplayVersion")
                    except OSError:
                        pass
                    else:
                        if isinstance(raw_display_version, str):
                            display_version = raw_display_version
                    path = Path(install_location)
                    if is_ida_dir(path):
                        ret.append(_WindowsRegistryInstallation(path, display_name, display_version))
            except OSError:
                continue
    finally:
        winreg.CloseKey(root)
    return ret


def _find_windows_installs_from_registry() -> list[Path]:
    """Read InstallLocation from the Add/Remove Programs registry under HKLM."""
    return [installation.path for installation in _find_windows_registry_installations()]


def find_standard_windows_installations() -> list[Path]:
    """Find standard IDA installations on Windows."""
    ret: list[Path] = list(_find_windows_installs_from_registry())

    base_directory = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    if base_directory.exists():
        for entry in base_directory.iterdir():
            if not entry.is_dir():
                continue
            if not _is_ida_install_dir_name(entry.name):
                continue
            if not is_ida_dir(entry):
                continue
            ret.append(entry)

    return _dedupe_paths(ret)


def _find_linux_installs_from_desktop_files() -> list[Path]:
    """Parse ``com.hex_rays.IDA.*.desktop`` shortcut files to recover install dirs.

    The installer writes a desktop entry whose ``Exec=`` points at ``<installdir>/ida``,
    so the parent of that path is the install directory regardless of where the user
    chose to install.
    """
    ret: list[Path] = []

    search_dirs: list[Path] = []
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        search_dirs.append(Path(xdg) / "applications")
    search_dirs.append(get_user_home_dir() / ".local" / "share" / "applications")
    search_dirs.append(Path("/usr/share/applications"))

    for app_dir in search_dirs:
        if not app_dir.exists():
            continue
        for desktop in app_dir.glob("com.hex_rays.IDA.*.desktop"):
            try:
                text = desktop.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.startswith("Exec="):
                    continue
                # Exec= can include arguments; the binary is the first whitespace-separated token.
                exec_value = line[len("Exec=") :].strip()
                if not exec_value:
                    break
                exec_path = Path(exec_value.split()[0])
                install_dir = exec_path.parent
                if is_ida_dir(install_dir):
                    ret.append(install_dir)
                break
    return ret


def _find_ida_installs_in_directory(base: Path) -> list[Path]:
    """Find direct child directories whose names match IDA installer naming."""
    ret: list[Path] = []
    try:
        entries = list(base.iterdir()) if base.exists() else []
    except OSError:
        return ret

    for entry in entries:
        if not entry.is_dir():
            continue
        if not _is_ida_install_dir_name(entry.name):
            continue
        if not is_ida_dir(entry):
            continue
        ret.append(entry)
    return ret


def find_standard_linux_installations() -> list[Path]:
    """Find standard IDA installations on Linux."""
    ret: list[Path] = list(_find_linux_installs_from_desktop_files())

    # ida.xml's Linux default is ${platform_install_prefix}/ida-${edition}-${version}.
    # Depending on whether the installer runs per-user or as root, common prefixes
    # are the user's home directory or system locations like /opt. Also retain the
    # legacy hcli prefix used before we matched installer naming.
    for base in (
        get_user_home_dir(),
        Path("/opt"),
        Path("/usr/local"),
        get_user_home_dir() / ".local" / "share" / "applications",
    ):
        ret.extend(_find_ida_installs_in_directory(base))

    return _dedupe_paths(ret)


def _find_mac_installs_from_spotlight() -> list[Path]:
    """Use Spotlight to locate IDA app bundles by CFBundleIdentifier.

    All editions share ``com.hexrays.ida`` (per ui/ida/qt/Info.plist.ida in ci-ida).
    """
    if not shutil.which("mdfind"):
        return []
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemCFBundleIdentifier == 'com.hexrays.ida'"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    ret: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        path = Path(line)
        if not _is_ida_install_dir_name(path.name):
            continue
        if not is_ida_dir(path):
            continue
        ret.append(path)
    return ret


def find_standard_mac_installations() -> list[Path]:
    """Find standard IDA installations on macOS."""
    ret: list[Path] = list(_find_mac_installs_from_spotlight())

    for base in (Path("/Applications"), get_user_home_dir() / "Applications"):
        if not base.exists():
            continue
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            if not _is_ida_install_dir_name(entry.name):
                continue
            if not is_ida_dir(entry):
                continue
            ret.append(entry)

    return _dedupe_paths(ret)


def find_standard_installations() -> list[Path]:
    """Find standard IDA installations."""
    ret: list[Path] = []

    try:
        ret.append(find_current_ida_install_directory())
    except MissingCurrentInstallationDirectory:
        pass

    os_ = get_os()
    if os_ == "windows":
        ret.extend(find_standard_windows_installations())
    elif os_ == "linux":
        ret.extend(find_standard_linux_installations())
    elif os_ == "mac":
        ret.extend(find_standard_mac_installations())
    else:
        raise ValueError(f"Unsupported operating system: {os_}")

    return _dedupe_paths(ret)


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
    old_idadir = os.environ.get("IDADIR")
    os.environ["IDADIR"] = str(install_dir)
    try:
        try:
            # force this to be imported first and not reordered by ruff
            import idapro  # noqa: F401
            import ida_registry  # isort: skip
        except Exception:
            raise RuntimeError("idalib not available")

        ida_registry.reg_write_int("EULA 90", 1)
        ida_registry.reg_write_int("EULA 91", 1)
        ida_registry.reg_write_int("EULA 92", 1)
        ida_registry.reg_write_int("EULA 93", 1)
        ida_registry.reg_write_int("EULA 94", 1)
        logger.info("EULA accepted")
    finally:
        if old_idadir is None:
            os.environ.pop("IDADIR", None)
        else:
            os.environ["IDADIR"] = old_idadir


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
        raise FileExistsError(
            f"Installation directory already exists: {install_dir}\n"
            f"Please remove the existing directory first or choose a different location."
        )

    logger.info(f"Installing IDA in {install_dir}")

    installer_size = installer.stat().st_size
    check_free_space(install_dir.parent, installer_size * 3)

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

    with (
        tempfile.TemporaryDirectory(prefix="hcli_") as temp_unpack_dir,
        tempfile.TemporaryDirectory(prefix="hcli_") as temp_install_dir,
    ):
        logger.info(f"Unpacking installer to {temp_unpack_dir}...")

        # Unpack the installer
        process = subprocess.run(
            ["unzip", "-qq", str(installer), "-d", temp_unpack_dir], capture_output=True, check=False
        )

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

        process = subprocess.run([str(installer_path)] + args, capture_output=True, check=False)

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
        installer_path = Path(f"./{installer_path}")

    if not os.access(installer_path, os.X_OK):
        logger.info(f"Setting executable permission on {installer_path}")
        current_mode = os.stat(installer_path).st_mode
        os.chmod(installer_path, current_mode | stat.S_IXUSR)

    home_dir = get_user_home_dir()
    share_dir = Path(home_dir) / ".local" / "share" / "applications"
    share_dir.mkdir(parents=True, exist_ok=True)

    process = subprocess.run([str(installer_path)] + args, capture_output=True, check=False)

    if process.returncode != 0:
        raise RuntimeError("Installer execution failed")


def _install_ida_windows(installer: Path, prefix: Path) -> None:
    """Install IDA on Windows."""
    args = _get_installer_args(prefix)

    process = subprocess.run(["cmd", "/c", str(installer)] + args, capture_output=True, check=False)

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
            try:
                shutil.copy2(item, dest_item)
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    if dest_item.exists():
                        dest_item.unlink()
                    raise NoSpaceError(dest_path) from e
                raise


class PathsConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    # like: "/Applications/IDA Professional 9.1.app" (macOS)
    # Note: IDA itself may write the inner "Contents/MacOS" path here;
    # callers normalize via _normalize_install_dir().
    installation_directory: Path | None = Field(alias="ida-install-dir", default=None)


class PluginRepositoryConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    url: str = Field(
        default="https://raw.githubusercontent.com/HexRaysSA/plugin-repository/refs/heads/v1/plugin-repository.json",
    )


class SettingsConfig(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    plugin_repository: PluginRepositoryConfig = Field(
        alias="plugin-repository", default_factory=lambda: PluginRepositoryConfig()
    )


class PluginConfig(BaseModel):
    # `ida-plugin.json` `.plugin.settings` describes the schema for these settings.
    settings: dict[str, str | bool] = Field(default_factory=dict)


# describes contents of IDAUSR/ida-config.json
class IDAConfigJson(BaseModel):
    """IDA configuration $IDAUSR/ida-config.json"""

    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    version: Literal[1] | None = Field(alias="Version", default=1)
    paths: PathsConfig = Field(alias="Paths", default_factory=lambda: PathsConfig())
    settings: SettingsConfig = Field(alias="Settings", default_factory=lambda: SettingsConfig())
    # from plugin name to config.
    # NOTE: keyed by bare plugin name for now. Two plugins with the same bare
    # name but different repository URLs will share the same settings entry;
    # revisiting this is out of scope for the initial collision fix.
    plugins: dict[str, PluginConfig] = Field(alias="Plugins", default_factory=dict)


def get_ida_config_path() -> Path:
    idausr = get_ida_user_dir()

    return Path(idausr) / "ida-config.json"


def get_ida_config() -> IDAConfigJson:
    ida_config_path = get_ida_config_path()
    if not ida_config_path.exists():
        logger.debug("using default ida-config.json contents")
        return IDAConfigJson()

    return IDAConfigJson.model_validate_json(ida_config_path.read_text(encoding="utf-8"))


def set_ida_config(config: IDAConfigJson):
    ida_config_path = get_ida_config_path()
    if not ida_config_path.exists():
        logger.debug("creating $IDAUSR directory")
        ida_config_path.parent.mkdir(parents=True, exist_ok=True)

    _ = ida_config_path.write_text(config.model_dump_json(), encoding="utf-8")


class MissingCurrentInstallationDirectory(ValueError):
    def __init__(self, msg):
        super().__init__(f"failed to determine current IDA installation directory: {msg}")


class FailedToDetectIDAVersion(RuntimeError):
    def __init__(self, msg: str | None = None):
        if msg:
            super().__init__(f"failed to determine current IDA version: {msg}")
        else:
            super().__init__("failed to determine current IDA version")


def find_hcli_default_ida_install_directory() -> Path | None:
    """Return hcli's selected default IDA installation, if configured and valid."""
    from hcli.lib.config import config_store

    default_instance = config_store.get_string("ida.default", "")
    instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}
    if not default_instance or default_instance not in instances:
        return None

    install_dir = _normalize_install_dir(Path(instances[default_instance]))
    if not install_dir.exists():
        logger.warning("configured hcli default IDA installation does not exist: %s", install_dir)
        return None
    if not is_ida_dir(install_dir):
        logger.warning("configured hcli default IDA installation is invalid: %s", install_dir)
        return None
    return install_dir


def find_current_ida_install_directory() -> Path:
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR")
    if env:
        return _normalize_install_dir(Path(env))
    if ENV.HCLI_CURRENT_IDA_INSTALL_DIR is not None:
        return _normalize_install_dir(Path(ENV.HCLI_CURRENT_IDA_INSTALL_DIR))

    if ENV.IDADIR is not None:
        return _normalize_install_dir(Path(ENV.IDADIR))

    hcli_default = find_hcli_default_ida_install_directory()
    if hcli_default is not None:
        logger.debug("current IDA installation from hcli default: %s", hcli_default)
        return hcli_default

    config = get_ida_config()
    if not config.paths.installation_directory:
        raise MissingCurrentInstallationDirectory("directory doesn't exist")

    install_dir = _normalize_install_dir(config.paths.installation_directory)

    if not install_dir.exists():
        raise MissingCurrentInstallationDirectory("ida-config.json invalid: ida-install-dir doesn't exist")

    logger.debug("current IDA installation from ida-config.json: %s", install_dir)
    return install_dir


def explain_missing_current_installation_directory(console: rich.console.Console):
    console.print("[red]Error[/red]: failed to find the current IDA Pro installation directory.")
    console.print("")
    console.print("You can configure this in two ways:")
    console.print("")
    console.print("1. set the default value in $IDAUSR/ida-config.json, which you can do via:")
    console.print("")
    console.print(f"     [grey69]{ENV.HCLI_BINARY_NAME} ida set-default /path/to/IDA/installation/[/grey69]")
    console.print("")
    console.print("2. provide the HCLI_CURRENT_IDA_INSTALL_DIR environment variable, like:")
    console.print("")
    console.print("     [grey69]export HCLI_CURRENT_IDA_INSTALL_DIR=/path/to/IDA/installation/[/grey69] # Linux, or")
    console.print(
        '     [grey69]export HCLI_CURRENT_IDA_INSTALL_DIR="/Applications/IDA Professional 9.2.app/"[/grey69] # macOS, or'
    )
    console.print(
        '     [grey69]set HCLI_CURRENT_IDA_INSTALL_DIR="C:\\Program Files\\IDA Professional 9.2"[/grey69]  # Windows'
    )
    console.print("")


def explain_failed_to_detect_ida_version(console: rich.console.Console):
    console.print("[red]Error[/red]: failed to determine current IDA version.")
    console.print("")
    console.print("hcli needs to run IDA to detect its version, but this failed.")
    console.print("")
    console.print("You can work around this by explicitly setting the version:")
    console.print("")
    console.print("1. set the HCLI_CURRENT_IDA_VERSION environment variable:")
    console.print("")
    console.print("     [grey69]export HCLI_CURRENT_IDA_VERSION=9.2[/grey69]")
    console.print("")
    console.print("2. also ensure IDA installation directory is configured:")
    console.print("")
    console.print(f"     [grey69]{ENV.HCLI_BINARY_NAME} ida set-default /path/to/IDA/installation/[/grey69]")
    console.print("")
    console.print("   or via environment variable:")
    console.print("")
    console.print("     [grey69]export HCLI_CURRENT_IDA_INSTALL_DIR=/path/to/IDA/installation/[/grey69]")
    console.print("")


def find_current_ida_executable(suffix: str = "") -> Path:
    install_directory = find_current_ida_install_directory()
    return get_ida_binary_path(install_directory, suffix)


def find_current_idat_executable() -> Path:
    return find_current_ida_executable("t")


def _prepare_headless_ida_user_dir(source_dir: Path, target_dir: Path) -> None:
    """Copy the minimal IDAUSR state needed for headless idat invocations.

    For Python detection we need the Python selection state and startup context from
    ``ida.reg``, optional ``cfg/idapython.cfg``, optional ``idapythonrc.py``, plus the
    license files required for IDA to start. We intentionally omit ``plugins/`` and
    other user content so third-party plugins cannot interfere with ``idat``.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    idapython_cfg = source_dir / "cfg" / "idapython.cfg"
    if idapython_cfg.is_file():
        (target_dir / "cfg").mkdir(parents=True, exist_ok=True)
        shutil.copy2(idapython_cfg, target_dir / "cfg" / "idapython.cfg")

    ida_reg = source_dir / "ida.reg"
    if ida_reg.is_file():
        shutil.copy2(ida_reg, target_dir / "ida.reg")

    idapythonrc = source_dir / "idapythonrc.py"
    if idapythonrc.is_file():
        shutil.copy2(idapythonrc, target_dir / "idapythonrc.py")

    for license_file in source_dir.glob("*.hexlic"):
        if license_file.is_file():
            shutil.copy2(license_file, target_dir / license_file.name)


def _run_ida_batch_script(idat_path: Path, src: str, env: dict[str, str] | None = None) -> dict:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        script_path = temp_path / "idat-script.py"
        log_path = temp_path / "ida.log"

        script_path.write_text(src, encoding="utf-8")

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
            f"-L{log_path.absolute()!s}",
            f"-S{script_path.absolute()!s}",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        logger.debug(f"idat command: {' '.join(cmd)}")
        if env and env.get("IDAUSR"):
            logger.debug(f"idat IDAUSR: {env['IDAUSR']}")
        logger.debug(f"idat exit code: {result.returncode}")
        if result.stdout:
            logger.debug(f"idat stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"idat stderr: {result.stderr}")

        if not log_path.exists():
            raise RuntimeError(f"failed to invoke idat: log file was not created: {log_path}")

        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        for line in log_text.splitlines():
            if not line.startswith("__hcli__:"):
                continue

            return json.loads(line[len("__hcli__:") :])

        log_tail = "\n".join(log_text.splitlines()[-20:])
        if log_tail:
            raise RuntimeError(f"failed to invoke idat: could not find expected lines in log output:\n{log_tail}")

        raise RuntimeError("failed to invoke idat: could not find expected lines in log output")


def _clean_env_for_idat() -> dict[str, str]:
    # When HCLI runs under `uv run --with ida-hcli`, uv replaces `$VIRTUAL_ENV`
    #  with its own ephemeral virtual environment.
    # So, we try to resolve the *real* virtual environment, if possible.
    user_venv = resolve_user_virtual_env()

    env = os.environ.copy()
    for key in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "PATH"):
        env.pop(key, None)

    if user_venv is not None:
        # we pass this along so idat can recognize the current virtualenv
        # if VIRTUAL_ENV is set, such as users that run HCLI and IDA with
        # a virtualenv activated.
        #
        # note that there can be false positives here:
        # if the user runs HCLI when a venv is activated,
        # but its not the venv that IDA would use,
        # then it may install plugin dependencies into it.
        #
        # we don't have a good way of differentiating IDA's venv from a random venv.
        env["VIRTUAL_ENV"] = str(user_venv)

    return env


def run_py_in_current_idapython(src: str) -> dict:
    idat_path = find_current_idat_executable()
    if not idat_path.exists():
        raise ValueError(f"can't find idat: {idat_path}")

    if get_os() == "linux" and "9.2" in str(idat_path.absolute()) and " " in str(idat_path.absolute()):
        logger.warning(
            "invoking idat on IDA 9.2/Linux with a space in the full path, you might encounter HCLI GitHub issue #99"
        )

    idausr = get_ida_user_dir()

    # First, try with the real IDAUSR so idapythonrc.py runs and activates any user venv.
    # IDA_IS_INTERACTIVE=1 is needed because most idapythonrc.py scripts guard venv
    # activation behind that flag.
    # If this fails (e.g. a broken plugin crashes idat on startup), fall back to a
    # minimal isolated IDAUSR that omits plugins and other user content.
    if idausr.exists() and idausr.is_dir():
        env = _clean_env_for_idat()
        env["IDAUSR"] = str(idausr)
        env["IDA_IS_INTERACTIVE"] = "1"
        try:
            return _run_ida_batch_script(idat_path, src, env=env)
        except RuntimeError:
            logger.debug("idat probe with real IDAUSR failed, retrying with isolated IDAUSR")

        with tempfile.TemporaryDirectory() as temp_dir:
            isolated_idausr = Path(temp_dir) / "idausr"
            _prepare_headless_ida_user_dir(idausr, isolated_idausr)

            env = _clean_env_for_idat()
            env["IDAUSR"] = str(isolated_idausr)
            return _run_ida_batch_script(idat_path, src, env=env)

    env = _clean_env_for_idat()
    env["IDAUSR"] = str(idausr)
    return _run_ida_batch_script(idat_path, src, env=env)


def detect_binary_arch(path: Path) -> str | None:
    """Detect the CPU architecture of a native binary by reading its header.

    Supports ELF (Linux) and Mach-O (macOS) formats.
    Returns "x86_64" or "aarch64", or None if unrecognized.
    """
    with open(path, "rb") as f:
        header = f.read(20)

    if len(header) < 20:
        return None

    # ELF: magic = 0x7f 'E' 'L' 'F'
    if header[:4] == b"\x7fELF":
        e_machine = struct.unpack_from("<H", header, 18)[0]
        return {0x3E: "x86_64", 0xB7: "aarch64"}.get(e_machine)

    # Mach-O 64-bit
    magic = struct.unpack_from("<I", header, 0)[0]
    if magic == 0xFEEDFACF:  # little-endian
        cpu_type = struct.unpack_from("<I", header, 4)[0]
        return {0x01000007: "x86_64", 0x0100000C: "aarch64"}.get(cpu_type)
    elif magic == 0xCFFAEDFE:  # big-endian
        cpu_type = struct.unpack_from(">I", header, 4)[0]
        return {0x01000007: "x86_64", 0x0100000C: "aarch64"}.get(cpu_type)

    return None


def find_current_ida_platform() -> str:
    """find the platform associated with the current IDA installation"""
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_PLATFORM")
    if env:
        return env
    if ENV.HCLI_CURRENT_IDA_PLATFORM is not None:
        return ENV.HCLI_CURRENT_IDA_PLATFORM

    os_ = get_os()
    if os_ == "windows":
        return "windows-x86_64"
    elif os_ == "linux":
        return "linux-x86_64"
    elif os_ == "mac":
        ida_path = find_current_ida_executable()
        if not ida_path.exists():
            raise RuntimeError(f"failed to determine current IDA platform: can't find ida: {ida_path}")

        arch = detect_binary_arch(ida_path)
        if arch == "x86_64":
            return "macos-x86_64"
        elif arch == "aarch64":
            return "macos-aarch64"
        else:
            raise RuntimeError(f"failed to determine current IDA platform: unrecognized architecture in {ida_path}")
    else:
        raise ValueError(f"Unsupported OS: {os_}")


def parse_version_from_ida_pro_py(ida_dir: Path) -> str | None:
    """Parse the IDA version from the python/ida_pro.py SDK version docstring.

    The SWIG-generated ida_pro.py always contains a docstring like:
        IDA SDK v9.2.
    This is present in all IDA editions that include IDAPython
    (Pro, Essential, Home, Classroom).
    """
    ida_pro_path = get_ida_path(ida_dir) / "python" / "ida_pro.py"
    if not ida_pro_path.exists():
        return None

    try:
        content = ida_pro_path.read_text(encoding="utf-8")
    except OSError:
        return None

    m = re.search(r"IDA SDK v(\d+\.\d+)", content)
    if m:
        return m.group(1)
    return None


def parse_version_from_dir_name(ida_dir: Path) -> str | None:
    """Parse the IDA version from the installation directory name.

    Handles standard names like:
        "IDA Professional 9.2"
        "IDA-Professional-9.2"
        "IDA Professional 9.2.app"
    """
    name = ida_dir.name
    name = name.removesuffix(".app")
    m = re.search(r"(\d+\.\d+)", name)
    if m:
        return m.group(1)
    return None


def _windows_path_key(path: Path) -> str:
    """Normalize a path for Windows registry comparisons."""
    return ntpath.normcase(ntpath.normpath(str(path)))


def _find_windows_registry_installation(ida_dir: Path) -> _WindowsRegistryInstallation | None:
    """Find the Windows registry metadata for an IDA installation path."""
    target = _windows_path_key(ida_dir)
    for installation in _find_windows_registry_installations():
        if _windows_path_key(installation.path) == target:
            return installation
    return None


def parse_version_from_windows_registry(ida_dir: Path) -> str | None:
    """Parse the IDA version for an installation from the Windows uninstall registry."""
    installation = _find_windows_registry_installation(ida_dir)
    if installation is None:
        return None

    version = parse_version_from_dir_name(Path(installation.display_name))
    if version:
        return version

    if installation.display_version:
        m = re.search(r"(\d+\.\d+)", installation.display_version)
        if m:
            return m.group(1)
    return None


def parse_instance_version(name: str, ida_dir: Path) -> Version | None:
    """Parse an IDA instance version for ordering.

    Prefer Windows registry metadata when available, then the SDK version
    embedded in python/ida_pro.py, then the main IDA executable. Fall back to
    the installation path and configured instance name, so non-standard paths
    still work when the user names the instance with a version.
    """
    version_sources: tuple[Callable[[], str | None], ...] = (
        lambda: parse_version_from_windows_registry(ida_dir),
        lambda: parse_version_from_ida_pro_py(ida_dir),
        lambda: parse_version_from_ida_binary(get_ida_binary_path(ida_dir)),
        lambda: parse_version_from_dir_name(ida_dir),
        lambda: parse_version_from_dir_name(Path(name)),
    )

    for get_raw_version in version_sources:
        raw_version = get_raw_version()
        if raw_version is None:
            continue
        try:
            return Version(raw_version)
        except InvalidVersion:
            logger.debug("ignoring invalid IDA version %r for instance %s at %s", raw_version, name, ida_dir)
    return None


def select_default_ida_instance(instances: Iterable[tuple[str, Path]]) -> str | None:
    """Select the default IDA instance by highest parsed version.

    Instances without a parsed version sort below instances with a version. Name
    ordering is retained as a deterministic fallback and tie-breaker.
    """
    candidates = list(instances)
    if not candidates:
        return None

    def sort_key(instance: tuple[str, Path]) -> tuple[bool, Version, str]:
        name, ida_dir = instance
        version = parse_instance_version(name, ida_dir)
        return (version is not None, version or Version("0"), name)

    return max(candidates, key=sort_key)[0]


def find_current_ida_version() -> str:
    """find the version of the current IDA installation, like '9.1'"""
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    env = os.environ.get("HCLI_CURRENT_IDA_VERSION")
    if env:
        return env
    if ENV.HCLI_CURRENT_IDA_VERSION is not None:
        return ENV.HCLI_CURRENT_IDA_VERSION

    ida_dir = find_current_ida_install_directory()

    version = parse_version_from_windows_registry(ida_dir)
    if version:
        return version

    version = parse_version_from_ida_pro_py(ida_dir)
    if version:
        return version

    version = parse_version_from_ida_binary(get_ida_binary_path(ida_dir))
    if version:
        return version

    version = parse_version_from_dir_name(ida_dir)
    if version:
        return version

    raise FailedToDetectIDAVersion(
        "could not determine IDA version from python/ida_pro.py, the IDA executable, or the installation directory name"
    )


def generate_instance_name(path: Path) -> str:
    """Generate a reasonable instance name from installation metadata or path."""
    # Prefer the Windows registry display name because InstallLocation may be a
    # custom directory such as C:\IDA91 while DisplayName is "IDA Professional 9.1".
    registry_installation = _find_windows_registry_installation(path)
    name = registry_installation.display_name if registry_installation else path.name

    # Remove .app extension for macOS
    name = name.removesuffix(".app")

    # Convert to lowercase and replace spaces with dashes
    name = name.lower().replace(" ", "-")

    # Shorten common patterns
    name = name.replace("ida-professional", "ida-pro")
    name = name.replace("ida-home", "ida-home")
    return name.replace("ida-free", "ida-free")


def add_instance_to_config(name: str, path: Path) -> bool:
    """Add an IDA instance to the configuration.

    Returns:
        True if the instance was added, False if it already exists.
    """
    from hcli.lib.config import config_store

    # Get existing instances
    instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}

    if name in instances:
        return False  # Already exists

    # Store the resolved path as string for consistent normalization
    instances[name] = str(path.resolve())

    # Save back to config
    config_store.set_object("ida.instances", instances)

    return True
