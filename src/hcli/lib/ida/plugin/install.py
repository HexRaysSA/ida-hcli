from __future__ import annotations

import errno
import io
import logging
import os
import pathlib
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import rich.status

if TYPE_CHECKING:
    from hcli.lib.ida.plugin.repo import BasePluginRepo

from hcli.lib.console import stderr_console
from hcli.lib.ida import get_ida_user_dir
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    MinimalIDAPluginMetadata,
    get_metadata_from_plugin_archive,
    get_metadata_path_from_plugin_archive,
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_source_plugin_archive,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
    validate_path,
)
from hcli.lib.ida.plugin.components import (
    collect_python_dependencies_from_archive,
    collect_python_dependencies_from_directory,
    validate_components_for_install,
)
from hcli.lib.ida.plugin.context import InstallContext
from hcli.lib.ida.plugin.exceptions import (
    BrokenPluginInstallationError,
    DependencyInstallationError,
    IDAVersionIncompatibleError,
    InvalidPluginNameError,
    PipNotAvailableError,
    PlatformIncompatibleError,
    PluginAlreadyInstalledError,
    PluginInUseError,
    PluginNotInstalledError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.reference import normalize_plugin_host, parse_dependency_spec
from hcli.lib.ida.plugin.result import InstallResult, InstallStatus
from hcli.lib.ida.python import (
    PIP_OPTIONS_DEFAULT,
    CantInstallPackagesError,
    PipOptions,
    find_current_python_executable,
    has_pip,
    pip_install_packages,
    resolve_current_python,
    verify_pip_can_install_packages,
)
from hcli.lib.ida.python.environment import PythonEnvironmentError, validate_python_environment
from hcli.lib.util.io import NoSpaceError

logger = logging.getLogger(__name__)


def get_plugins_directory() -> Path:
    """$IDAUSR/plugins/<name>"""
    ida_user_dir = get_ida_user_dir()
    if not ida_user_dir:
        raise ValueError("Could not determine IDA user directory")

    plugins_dir = Path(ida_user_dir) / "plugins"
    if not plugins_dir.exists():
        plugins_dir.mkdir(parents=True, exist_ok=True)

    return plugins_dir


def validate_path_component(name: str):
    if not name or name in {".", ".."}:
        raise ValueError(f"Invalid path component: '{name}'.")

    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"Invalid path component: '{name}'. Must contain only ASCII characters")

    if "\t" in name or "\n" in name or "\r" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain tabs or newlines")

    if "/" in name or "\\" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain slashes")


def get_plugin_directory(name: str) -> Path:
    """$IDAUSR/plugins/<name>"""
    plugins_dir = get_plugins_directory()
    validate_path_component(name)
    return plugins_dir / name


# Trash/staging area next to the plugins directory. Because it (normally)
# lives on the same filesystem as the plugin directories, moves in and out are
# atomic renames on all platforms. It sits outside $IDAUSR/plugins, so neither
# IDA's plugin scan nor our own enumeration ever sees its contents.
TRASH_DIR_NAME = ".plugins-trash"


def get_trash_directory(plugins_dir: Path | None = None) -> Path:
    """$IDAUSR/.plugins-trash (or .plugins-trash next to the given directory)

    The plugins directory is resolved first so that when it is a symlink to
    another filesystem, the trash lands next to the real directory and moves
    in and out remain same-filesystem renames.
    """
    if plugins_dir is None:
        plugins_dir = get_plugins_directory()
    return plugins_dir.resolve().parent / TRASH_DIR_NAME


def is_file_in_use_error(e: OSError) -> bool:
    """Does this error indicate a file locked by another process?

    On Windows, deleting a DLL mapped by a running process fails with
    ERROR_ACCESS_DENIED, and renaming any ancestor directory of an open file
    fails the same way. POSIX doesn't lock mapped files like this, but
    EBUSY/ETXTBSY can surface in similar situations.
    """
    if getattr(e, "winerror", None) in (5, 32):  # ACCESS_DENIED, SHARING_VIOLATION
        return True
    return e.errno in (errno.EACCES, errno.EPERM, errno.EBUSY, errno.ETXTBSY)


def move_plugin_directory_to_trash(path: Path, label: str = "") -> Path:
    """Atomically rename a plugin directory into the trash area.

    Either the whole directory moves or nothing changes: when a file inside is
    locked (e.g. a plugin DLL loaded by a running IDA on Windows), the rename
    fails without modifying the installation.

    The optional label is included in the trashed name (e.g. ".rollback") so
    a human inspecting the trash can tell what a leftover was.

    Raises:
        PluginInUseError: when the rename fails because files are in use.
    """
    trash_dir = get_trash_directory(path.parent)
    trash_dir.mkdir(exist_ok=True)
    destination = trash_dir / f"{path.name}{label}-{uuid.uuid4().hex[:8]}"
    try:
        os.rename(path, destination)
    except OSError as e:
        if is_file_in_use_error(e):
            raise PluginInUseError(path.name, path) from e
        raise
    return destination


def remove_plugin_directory(path: Path) -> None:
    """Remove a plugin directory transactionally.

    First atomically rename the directory into the trash area, then delete the
    trashed copy. If deletion fails, the plugin is already logically removed;
    the leftover is swept by a later plugin command.

    Raises:
        PluginInUseError: when files are in use; the installation is untouched.
    """
    trashed = move_plugin_directory_to_trash(path)
    try:
        shutil.rmtree(trashed)
    except OSError as e:
        logger.debug("could not delete trashed directory %s: %s (leaving for later sweep)", trashed, e)


def sweep_trash() -> None:
    """Best-effort cleanup of leftovers in the trash area.

    Leftovers accumulate from interrupted operations: partially deleted
    uninstalls, staging directories, stale upgrade rollbacks. Call this only
    between plugin operations, never while one is in flight: an active upgrade
    keeps its rollback copy in the trash.
    """
    try:
        trash_dir = get_trash_directory()
        if not trash_dir.is_dir():
            return
        entries = list(trash_dir.iterdir())
    except Exception as e:
        logger.debug("could not enumerate trash directory: %s", e)
        return

    for entry in entries:
        logger.debug("sweeping trash: %s", entry)
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as e:
            logger.debug("could not sweep %s: %s", entry, e)


def get_metadata_from_plugin_directory(plugin_path: Path) -> IDAMetadataDescriptor:
    metadata_file = plugin_path / "ida-plugin.json"
    if not metadata_file.exists():
        raise ValueError(f"ida-plugin.json not found in {plugin_path}")

    try:
        content = metadata_file.read_text(encoding="utf-8")
        return IDAMetadataDescriptor.model_validate_json(content)
    except Exception as e:
        logger.debug("failed to validate ida-plugin.json: %s", e)
        raise ValueError(f"Failed to parse ida-plugin.json in {plugin_path}: {e}")


# TODO: keep this in sync with validate_metadata_in_plugin_archive
def validate_metadata_in_plugin_directory(plugin_path: Path):
    """validate the `ida-plugin.json` metadata within the given plugin directory.

    The following things must be checked:
    - the following paths must contain relative paths, no paths like ".." or similar escapes:
      - entry point
      - logo path
    - the file paths must exist in the directory:
      - entry point
      - logo path
    """
    metadata = get_metadata_from_plugin_directory(plugin_path)

    validate_path(metadata.plugin.entry_point, "entry point")
    if metadata.plugin.logo_path:
        validate_path(metadata.plugin.logo_path, "logo path")

    entry_point_path = plugin_path / metadata.plugin.entry_point

    if metadata.plugin.entry_point.endswith(".py"):
        # source plugin
        if not entry_point_path.exists():
            logger.debug(f"Entry point file not found in directory: '{metadata.plugin.entry_point}'")
            raise ValueError(f"Entry point file not found in directory: '{metadata.plugin.entry_point}'")
    else:
        # binary plugin - check for various extensions
        if not entry_point_path.exists():
            found = False
            for extension in (".so", ".dll", ".dylib"):
                if (plugin_path / (metadata.plugin.entry_point + extension)).exists():
                    found = True
                    break
            if not found:
                logger.debug(f"Entry point file not found in directory: '{metadata.plugin.entry_point}'")
                raise ValueError(f"Entry point file not found in directory: '{metadata.plugin.entry_point}'")

    if metadata.plugin.logo_path:
        logo_path = plugin_path / metadata.plugin.logo_path
        if not logo_path.exists():
            logger.debug(f"Logo file not found in directory: '{metadata.plugin.logo_path}'")
            raise ValueError(f"Logo file not found in directory: '{metadata.plugin.logo_path}'")


def is_valid_plugin_directory(path: Path) -> bool:
    """Does the path hold a well-formed installed plugin?

    Mirrors the criteria of ``get_installed_plugin_records``: the manifest
    parses, referenced files exist, and the plugin name matches the directory
    name. A directory that fails this is debris, e.g. remnants of an
    interrupted uninstall (issue #228).
    """
    if not (path / "ida-plugin.json").exists():
        return False

    try:
        validate_metadata_in_plugin_directory(path)
        metadata = get_metadata_from_plugin_directory(path)
    except ValueError:
        return False

    return metadata.plugin.name == path.name


@dataclass(frozen=True)
class InstalledPluginRecord:
    """An installed plugin and its on-disk metadata."""

    path: Path
    metadata: IDAMetadataDescriptor

    @property
    def name(self) -> str:
        return self.metadata.plugin.name

    @property
    def version(self) -> str:
        return self.metadata.plugin.version

    @property
    def host(self) -> str:
        return self.metadata.plugin.host


def get_installed_plugin_records() -> list[InstalledPluginRecord]:
    """Enumerate installed plugins with their on-disk metadata.

    This is the canonical source of truth for "what is installed". Other
    helpers (``get_installed_plugin_paths``, ``is_plugin_installed``, etc.)
    are implemented on top of this list so they agree on what counts as
    installed.
    """
    plugins_dir = get_plugins_directory()
    records: list[InstalledPluginRecord] = []

    if not plugins_dir.exists():
        return records

    for plugin_path in plugins_dir.iterdir():
        if not plugin_path.is_dir():
            continue

        metadata_file = plugin_path / "ida-plugin.json"
        if not metadata_file.exists():
            continue

        try:
            validate_metadata_in_plugin_directory(plugin_path)
        except ValueError as e:
            logger.debug(f"Invalid plugin metadata in {plugin_path}: {e}")
            continue

        try:
            metadata = get_metadata_from_plugin_directory(plugin_path)
        except ValueError as e:
            logger.warning(f"Failed to read metadata from {plugin_path}: {e}")
            continue

        if metadata.plugin.name != plugin_path.name:
            logger.debug("plugin name and path mismatch")
            continue

        records.append(InstalledPluginRecord(path=plugin_path, metadata=metadata))

    return records


def find_installed_plugin_in(
    records: list[InstalledPluginRecord],
    name: str,
    host: str | None = None,
) -> InstalledPluginRecord | None:
    """Search *pre-fetched* records for a plugin by name and optional host.

    Returns ``None`` when no match is found (does not raise).
    """
    wanted_name = name.lower()
    wanted_host = normalize_plugin_host(host) if host else None

    for record in records:
        if record.name.lower() != wanted_name:
            continue
        if wanted_host is not None and normalize_plugin_host(record.host) != wanted_host:
            continue
        return record

    return None


def find_installed_plugin(name: str, host: str | None = None) -> InstalledPluginRecord:
    """Find an installed plugin by name, optionally qualified by host.

    Name matching is case-insensitive because the user may type a different
    case than the on-disk directory. Host matching, when supplied, is done
    after normalization.

    Raises:
        PluginNotInstalledError: when no matching installed plugin exists.
    """
    record = find_installed_plugin_in(get_installed_plugin_records(), name, host)
    if record is None:
        raise PluginNotInstalledError(name)
    return record


def resolve_installed_plugin_directory(name: str) -> Path:
    """Resolve the on-disk directory for an installed plugin by name.

    Case-insensitive. Used by local commands (uninstall, config) so typing
    ``PLUGIN1`` finds ``$IDAUSR/plugins/plugin1``.

    Raises:
        PluginNotInstalledError: when no matching installed plugin exists.
    """
    return find_installed_plugin(name).path


def get_installed_plugin_paths() -> list[Path]:
    return [r.path for r in get_installed_plugin_records()]


def collect_all_python_dependencies(
    new_plugin_deps: list[str],
    installed_records: list[InstalledPluginRecord],
    exclude_names: set[str] | None = None,
) -> list[str]:
    """Collect Python dependencies from all installed plugins plus a new plugin's deps."""
    all_deps: list[str] = []
    for record in installed_records:
        if exclude_names and record.name in exclude_names:
            continue
        try:
            deps = collect_python_dependencies_from_directory(record.path, record.metadata)
        except Exception as e:
            logger.debug("skipping unreadable plugin dependencies at %s: %s", record.path, e)
            continue
        all_deps.extend(deps)
    all_deps.extend(new_plugin_deps)
    return all_deps


@dataclass
class PluginDependencyInfo:
    name: str
    dependencies: list[str]


def collect_plugin_dependencies() -> list[PluginDependencyInfo]:
    """Enumerate installed plugins that have Python dependencies.

    Skips plugins with unreadable metadata or no dependencies.
    """
    result: list[PluginDependencyInfo] = []
    for record in get_installed_plugin_records():
        try:
            deps = collect_python_dependencies_from_directory(record.path, record.metadata)
        except Exception as e:
            logger.debug("skipping unreadable plugin dependencies at %s: %s", record.path, e)
            continue
        if deps:
            result.append(PluginDependencyInfo(name=record.name, dependencies=deps))
    return result


@dataclass
class PluginDependencyResult:
    name: str
    dependencies: list[str]
    success: bool
    error: str | None = None


def install_single_plugin_dependencies(
    python_exe: Path,
    plugin: PluginDependencyInfo,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
) -> PluginDependencyResult:
    """Install Python dependencies for a single plugin. Does not raise."""
    try:
        pip_install_packages(python_exe, plugin.dependencies, pip_options=pip_options)
        return PluginDependencyResult(name=plugin.name, dependencies=plugin.dependencies, success=True)
    except CantInstallPackagesError as e:
        return PluginDependencyResult(name=plugin.name, dependencies=plugin.dependencies, success=False, error=str(e))


def get_installed_minimal_plugins() -> list[tuple[Path, MinimalIDAPluginMetadata]]:
    """fetch (name, path) pairs for currently installed minimal (likely legacy) plugins"""
    plugins_dir = get_plugins_directory()
    installed_plugins: list[tuple[Path, MinimalIDAPluginMetadata]] = []

    if not plugins_dir.exists():
        return installed_plugins

    for plugin_path in plugins_dir.iterdir():
        if not plugin_path.is_dir():
            continue

        metadata_file = plugin_path / "ida-plugin.json"
        if not metadata_file.exists():
            continue

        try:
            _ = get_metadata_from_plugin_directory(plugin_path)
        except ValueError:
            pass
        else:
            # skip the valid plugins
            continue

        try:
            metadata = MinimalIDAPluginMetadata.model_validate_json(metadata_file.read_bytes())
        except ValueError as e:
            logger.debug(f"Invalid plugin metadata in {plugin_path}: {e}")
            continue

        installed_plugins.append((metadata_file, metadata))

    return installed_plugins


def get_installed_legacy_plugins() -> list[Path]:
    """fetch paths for  currently installed legacy, single-file plugins"""
    plugins_dir = get_plugins_directory()
    installed_plugins: list[Path] = []

    if not plugins_dir.exists():
        return installed_plugins

    for plugin_path in plugins_dir.iterdir():
        if plugin_path.is_dir():
            continue

        if plugin_path.name.endswith(".py"):
            installed_plugins.append(plugin_path)

        if plugin_path.name.endswith((".so", ".dll", ".dylib")):
            installed_plugins.append(plugin_path)

    return installed_plugins


def resolve_python_for_dependencies(python_dependencies: list[str], *, check_environment: bool = True) -> Path:
    """Find IDA's Python and, unless disabled, refuse environments where installing into it is pointless.

    The environment check prints warnings to stderr for non-recommended but
    workable setups and raises for setups where the packages would never reach
    IDA (no venv, wrong Python version, PEP 668, uv overlay, no pip).

    Raises:
        PythonNotFoundError: when IDA's Python can't be determined.
        DependencyInstallationError: when the environment check finds errors.
        PipNotAvailableError: when pip is missing and the environment check was skipped.
    """
    resolved = resolve_current_python()
    if check_environment:
        try:
            validate_python_environment(resolved)
        except PythonEnvironmentError as e:
            raise DependencyInstallationError(python_dependencies, str(e)) from e

    if not has_pip(resolved.exe):
        logger.debug("pip not available")
        raise PipNotAvailableError(resolved.exe)

    return resolved.exe


def validate_for_install(metadata: IDAMetadataDescriptor, ctx: InstallContext) -> Path:
    """Validate that a plugin can be installed. Returns the destination path.

    Raises:
        InvalidPluginNameError: If plugin name is invalid.
        PluginAlreadyInstalledError: If plugin is already installed.
        BrokenPluginInstallationError: If remnants of a broken installation are in the way.
        PlatformIncompatibleError: If current platform is not supported.
        IDAVersionIncompatibleError: If current IDA version is not supported.
    """
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        raise InvalidPluginNameError(name, str(e)) from e

    # is_symlink() catches dangling editable installs that fail exists().
    if destination_path.exists() or destination_path.is_symlink():
        if is_valid_plugin_directory(destination_path):
            raise PluginAlreadyInstalledError(name, destination_path)
        raise BrokenPluginInstallationError(name, destination_path)

    if ctx.env.platform not in metadata.plugin.platforms:
        raise PlatformIncompatibleError(ctx.env.platform, metadata.plugin.platforms)

    if metadata.plugin.ida_versions and not is_ida_version_compatible(
        ctx.env.ida_version, metadata.plugin.ida_versions
    ):
        raise IDAVersionIncompatibleError(ctx.env.ida_version, metadata.plugin.ida_versions)

    return destination_path


def validate_for_upgrade(metadata: IDAMetadataDescriptor, ctx: InstallContext) -> Path:
    """Validate that a plugin can be upgraded. Returns the destination path.

    Raises:
        InvalidPluginNameError: If plugin name is invalid.
        PluginNotInstalledError: If plugin is not installed.
        PlatformIncompatibleError: If current platform is not supported.
        IDAVersionIncompatibleError: If current IDA version is not supported.
        PluginVersionDowngradeError: If new version is not greater than installed.
    """
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        raise InvalidPluginNameError(name, str(e)) from e

    if not destination_path.exists():
        raise PluginNotInstalledError(name)

    if ctx.env.platform not in metadata.plugin.platforms:
        raise PlatformIncompatibleError(ctx.env.platform, metadata.plugin.platforms)

    if metadata.plugin.ida_versions and not is_ida_version_compatible(
        ctx.env.ida_version, metadata.plugin.ida_versions
    ):
        raise IDAVersionIncompatibleError(ctx.env.ida_version, metadata.plugin.ida_versions)

    existing_metadata = get_metadata_from_plugin_directory(destination_path)
    new_version = parse_plugin_version(metadata.plugin.version)
    existing_version = parse_plugin_version(existing_metadata.plugin.version)
    if new_version <= existing_version:
        raise PluginVersionDowngradeError(name, existing_metadata.plugin.version, metadata.plugin.version)

    return destination_path


def install_python_dependencies(
    python_deps: list[str],
    ctx: InstallContext,
    *,
    excluded_plugins: set[str] | None = None,
) -> None:
    """Validate and install Python dependencies, merging with existing plugins' deps.

    Raises:
        PipNotAvailableError: If pip is not available.
        DependencyInstallationError: If the Python environment is unusable.
        CantInstallPackagesError: If pip install fails.
    """
    if not python_deps:
        return

    with rich.status.Status("collecting existing Python dependencies", console=stderr_console):
        all_deps = collect_all_python_dependencies(
            python_deps, get_installed_plugin_records(), exclude_names=excluded_plugins
        )

    python_exe = resolve_python_for_dependencies(python_deps, check_environment=ctx.options.check_environment)

    verify_pip_can_install_packages(python_exe, all_deps, pip_options=ctx.options.pip_options)

    with rich.status.Status(f"installing Python dependencies: {', '.join(python_deps)}", console=stderr_console):
        pip_install_packages(python_exe, all_deps, pip_options=ctx.options.pip_options)


def validate_archive_entry(file_info: zipfile.ZipInfo, relative_path: pathlib.PurePosixPath) -> None:
    """Validate a ZIP archive entry before extraction.

    This function prevents path traversal attacks by rejecting:
    - Symlinks (which can point outside the extraction directory)
    - Absolute paths
    - Paths containing '..' (parent directory references)

    Raises:
        ValueError: If the entry is unsafe to extract
    """
    # Reject symlinks - they can escape the extraction directory
    # Unix symlink has file type 0xA in the high nibble of external_attr
    if (file_info.external_attr >> 28) == 0xA:
        logger.warning("Rejecting symlink in archive: %s", file_info.filename)
        raise ValueError(f"Symlinks not allowed in archive: {file_info.filename}")

    # Reject absolute paths
    if relative_path.is_absolute():
        logger.warning("Rejecting absolute path in archive: %s", file_info.filename)
        raise ValueError(f"Absolute path in archive: {file_info.filename}")

    # Reject path traversal sequences
    if ".." in relative_path.parts:
        logger.warning("Rejecting path traversal in archive: %s", file_info.filename)
        raise ValueError(f"Path traversal in archive: {file_info.filename}")


def should_extract_plugin_archive_path(plugin_dir_prefix: str, file_info: zipfile.ZipInfo) -> bool:
    """Should the given file entry be extracted for the given plugin directory in a zip archive?

    Args:
      plugin_dir_prefix: the path within the ZIP archive to the plugin to extract
      file_info: the entry to consider
    """
    if not file_info.filename.startswith(plugin_dir_prefix):
        # only consider entries within the plugin directory
        return False

    if file_info.filename == plugin_dir_prefix:
        # don't extract the plugin directory entry itself
        return False

    if file_info.filename.startswith(plugin_dir_prefix + ".git/"):
        # don't extract git repo junk, which comes from manually archiving a plugin source repo
        return False

    relative_path = pathlib.PurePosixPath(file_info.filename).relative_to(plugin_dir_prefix.rstrip("/"))
    # don't extract the plugin directory entry itself (again)
    return str(relative_path) != "."


def extract_zip_subdirectory_to(zip_data: bytes, subdirectory: Path, destination: Path):
    """Extract a subdirectory from a zip archive to a destination path."""
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_file:
        if not subdirectory or subdirectory == Path("."):
            # subdirectory represents the root (e.g., None or Path("."))
            plugin_dir_prefix = ""
        else:
            plugin_dir_prefix = subdirectory.as_posix() + "/"

        # Stage in the trash area beside the destination's plugins directory
        # rather than in the system temp dir: (normally) the same filesystem,
        # so the final rename is atomic on all platforms and the fully-formed
        # plugin directory appears all at once. An interrupted install never
        # leaves a partial destination, and abandoned staging directories are
        # swept by later plugin commands.
        staging_root = get_trash_directory(destination.parent)
        staging_root.mkdir(parents=True, exist_ok=True)
        temp_path = staging_root / f"{destination.name}.staging-{uuid.uuid4().hex[:8]}"
        temp_path.mkdir()

        try:
            # do validation pass before extracting any content to prevent any half-extracted content
            for file_info in zip_file.infolist():
                if not should_extract_plugin_archive_path(plugin_dir_prefix, file_info):
                    continue

                relative_path = pathlib.PurePosixPath(file_info.filename).relative_to(plugin_dir_prefix.rstrip("/"))
                validate_archive_entry(file_info, relative_path)

            for file_info in zip_file.infolist():
                if not should_extract_plugin_archive_path(plugin_dir_prefix, file_info):
                    continue

                relative_path = pathlib.PurePosixPath(file_info.filename).relative_to(plugin_dir_prefix.rstrip("/"))
                target_path = temp_path / relative_path

                if file_info.is_dir():
                    logger.debug("creating directory: %s", relative_path)
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with zip_file.open(file_info.filename) as source_file, target_path.open("wb") as target_file:
                            logger.debug("creating file:      %s", relative_path)
                            shutil.copyfileobj(source_file, target_file)
                    except OSError as e:
                        if e.errno == errno.ENOSPC:
                            raise NoSpaceError(target_path.parent) from e
                        raise

            logger.debug("creating plugin directory: %s", destination)
            os.rename(temp_path, destination)
        except BaseException:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise


def apply_plugin_archive_files(
    zip_data: bytes,
    plugin_subdirectory: Path,
    destination: Path,
    plugin_name: str,
) -> None:
    """Extract plugin archive to disk, cleaning up any stale editable .pth file first.

    A previous editable install of the same plugin may have left a .pth
    in IDA's site-packages. Drop it so the non-editable install isn't
    shadowed by stale paths on sys.path.
    """
    _remove_editable_pth_file(plugin_name)
    extract_zip_subdirectory_to(zip_data, plugin_subdirectory, destination)


def apply_plugin_editable_files(
    source_dir: Path,
    destination: Path,
    plugin_name: str,
) -> None:
    """Symlink ``source_dir`` into the plugins directory for an editable install.

    Replaces any existing install at the destination and writes a .pth file
    for src-layout projects.
    """
    # is_symlink() is checked before exists() because a broken symlink
    # fails exists() but should still be replaced.
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        remove_plugin_directory(destination)

    try:
        destination.symlink_to(source_dir, target_is_directory=True)
    except OSError as e:
        raise ValueError(
            f"Failed to create symlink {destination} -> {source_dir}: {e}. "
            "On Windows, symlink creation requires Developer Mode or "
            "administrator privileges."
        ) from e

    logger.info("symlinked %s -> %s", destination, source_dir)

    # If the project uses the standard src-layout, drop a .pth file into
    # IDA's site-packages so the package is importable. This mirrors what
    # `pip install -e .` does (PEP 660). For flat-layout projects, IDA
    # already exposes the plugin directory on sys.path (because plugin.py
    # is exec'd from there), so no .pth is needed -- but we still clear
    # any stale one left over from a prior src-layout install.
    src_dir = source_dir / "src"
    if src_dir.is_dir():
        _write_editable_pth_file(plugin_name, src_dir)
    else:
        _remove_editable_pth_file(plugin_name)


def apply_upgrade_with_rollback(
    zip_data: bytes,
    plugin_subdirectory: Path,
    destination: Path,
    plugin_name: str,
) -> None:
    """Extract a plugin archive over an existing install, rolling back on failure.

    Note that Python dependencies installed before the failure aren't
    rolled back; they're upgraded in place and left as-is.

    Raises:
        PluginInUseError: when the upgrade target is locked.
        NoSpaceError: when disk is full during extraction.
    """
    # Atomically move the current install into the trash area before writing
    # anything. When plugin files are locked (e.g. loaded by a running IDA
    # on Windows), this fails without modifying the installation. The unique
    # trash name means a stale rollback from an interrupted upgrade never
    # blocks this one; the sweep removes such leftovers later.
    rollback_path = move_plugin_directory_to_trash(destination, label=".rollback")
    try:
        apply_plugin_archive_files(zip_data, plugin_subdirectory, destination, plugin_name)
    except Exception as e:
        logger.debug("error during upgrade: install: %s", e)
        logger.debug("rolling back to prior version")
        shutil.rmtree(destination, ignore_errors=True)
        if destination.exists():
            logger.error(
                "could not restore previous version: partial upgrade remains at %s; uninstall and reinstall",
                destination,
            )
        else:
            os.rename(rollback_path, destination)
        raise
    else:
        try:
            shutil.rmtree(rollback_path)
        except OSError as e:
            logger.debug("could not delete rollback copy %s: %s (leaving for later sweep)", rollback_path, e)


def install_plugin_archive(
    zip_data: bytes,
    name: str,
    ctx: InstallContext,
):
    """Validate and extract a plugin archive to disk.

    Does not install Python dependencies; call install_python_dependencies
    separately when needed.
    """
    if not is_source_plugin_archive(zip_data, name) and not is_binary_plugin_archive(zip_data, name):
        raise ValueError("Invalid plugin archive")

    metadata_path, metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, metadata_path, metadata)

    logger.info("installing plugin: %s (%s)", metadata.plugin.name, metadata.plugin.version)

    validate_components_for_install(metadata, zip_data, name)
    destination_path = validate_for_install(metadata, ctx)

    apply_plugin_archive_files(zip_data, metadata_path.parent, destination_path, metadata.plugin.name)


# Files/directories under a plugin source tree we never want to ship into a
# distributable archive (dev / VCS / OS noise).
_PLUGIN_DIRECTORY_SKIP_PARTS = frozenset({".git", ".hg", ".svn", "__pycache__", ".DS_Store"})


def pack_plugin_directory_to_zip(source_dir: Path) -> bytes:
    """Pack a plugin source directory into an in-memory ZIP archive, files
    placed at the archive root. The resulting bytes can be fed straight into
    `install_plugin_archive`, so a local directory install reuses the same
    validation and extraction logic as a remote/zip install.

    Skips common dev junk so an in-place git checkout doesn't poison the
    install with `.git/`, `__pycache__/`, etc.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(source_dir)
            if any(part in _PLUGIN_DIRECTORY_SKIP_PARTS for part in rel.parts):
                continue
            zf.write(path, str(rel))
    return buf.getvalue()



def _editable_pth_filename(plugin_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in plugin_name)
    return f"_hcli_editable_{safe}.pth"


def _get_ida_site_packages_dir() -> Path:
    """Return IDA's Python site-packages directory (purelib).

    Matches where ``pip install`` would land packages without ``--user`` /
    ``--target``. Uses sysconfig on the IDA-side interpreter so the result
    reflects IDA's bundled Python, not hcli's.
    """
    python_exe = find_current_python_executable()
    result = subprocess.run(
        [str(python_exe), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        capture_output=True,
        check=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _write_editable_pth_file(plugin_name: str, *paths: Path) -> None:
    site_dir = _get_ida_site_packages_dir()
    site_dir.mkdir(parents=True, exist_ok=True)
    pth_path = site_dir / _editable_pth_filename(plugin_name)
    pth_path.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    logger.info("wrote editable .pth file: %s", pth_path)


def _remove_editable_pth_file(plugin_name: str) -> None:
    try:
        site_dir = _get_ida_site_packages_dir()
    except Exception as e:
        logger.debug("could not locate IDA site-packages to clean up .pth: %s", e)
        return
    pth_path = site_dir / _editable_pth_filename(plugin_name)
    if pth_path.exists():
        pth_path.unlink()
        logger.info("removed editable .pth file: %s", pth_path)


def _uninstall_broken_plugin_directory(name: str) -> None:
    """Remove a plugins/<name> entry that is not a valid installation.

    Remnants of an interrupted uninstall (e.g. rmtree failed partway because a
    running IDA held a DLL open, issue #228) don't count as installed, but the
    entry blocks reinstallation. The user asked for this name to be gone, so
    remove whatever is squatting on it: a broken directory, a stale symlink,
    or even a plain file.

    Raises:
        PluginNotInstalledError: If no matching entry exists at all
        PluginInUseError: If files are in use; nothing is modified
    """
    plugins_dir = get_plugins_directory()
    for child in plugins_dir.iterdir():
        if child.name.lower() != name.lower():
            continue

        logger.warning("removing remnants of a broken plugin installation: %s", child)
        if child.is_symlink():
            child.unlink()
            _remove_editable_pth_file(child.name)
        elif child.is_file():
            child.unlink()
        else:
            remove_plugin_directory(child)
        return

    raise PluginNotInstalledError(name)


def uninstall_plugin(name: str):
    """Remove an installed plugin.

    Transactional: the plugin directory is first atomically renamed into the
    trash area, then deleted. When plugin files are locked (e.g. loaded by a
    running IDA on Windows), the rename fails without modifying anything.

    Raises:
        PluginNotInstalledError: If plugin is not installed
        PluginInUseError: If plugin files are in use; the installation is untouched
    """
    try:
        record = find_installed_plugin(name)
    except PluginNotInstalledError:
        # a directory may exist without being a valid installation (issue #228)
        _uninstall_broken_plugin_directory(name)
        return

    logger.info("uninstalling plugin: %s (%s)", record.name, record.version)

    # note that the pythonDependencies of the plugin aren't pruned.
    # we could re-collect all the deps requested by other plugins
    # but we shouldn't do a sync, since there might be other utils installed by the user.
    # so I think its better to just leave the orphans around.

    if record.path.is_symlink():
        # Editable install (or a manually-symlinked plugin): remove the
        # symlink only. Calling shutil.rmtree on a directory symlink raises
        # on POSIX and recurses into the target on some Windows configs --
        # both wrong; we want to leave the source tree untouched.
        record.path.unlink()
        # Editable installs may have dropped a .pth into IDA's site-packages
        # to expose a src-layout package. Best-effort cleanup.
        _remove_editable_pth_file(record.name)
    else:
        remove_plugin_directory(record.path)


def is_plugin_installed(name: str) -> bool:
    try:
        find_installed_plugin(name)
    except PluginNotInstalledError:
        return False
    return True


def upgrade_plugin_archive(
    zip_data: bytes,
    name: str,
    ctx: InstallContext,
):
    """Validate and upgrade a plugin archive on disk.

    Does not install Python dependencies; call install_python_dependencies
    separately when needed.
    """
    if not is_source_plugin_archive(zip_data, name) and not is_binary_plugin_archive(zip_data, name):
        raise ValueError("Invalid plugin archive")

    metadata_path, metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, metadata_path, metadata)

    validate_components_for_install(metadata, zip_data, name, is_upgrade=True)
    destination_path = validate_for_upgrade(metadata, ctx)

    apply_upgrade_with_rollback(zip_data, metadata_path.parent, destination_path, metadata.plugin.name)


def apply_install(
    *,
    source: bytes | Path,
    plugin_name: str,
    metadata: IDAMetadataDescriptor,
    ctx: InstallContext,
    settings: dict[str | None, dict[str, str]] | None = None,
    plugin_repo: BasePluginRepo | None = None,
    editable: bool = False,
) -> InstallResult:
    """Library-level install entry point.

    Orchestrates: validation, Python dependencies, file extraction, settings,
    and loose dependency installation. ``settings`` maps target name to
    key-value pairs: ``None`` for the root plugin, component/dependency names
    for the rest.
    """
    from hcli.lib.ida.plugin.settings import apply_resolved_settings

    version = metadata.plugin.version
    files_written = False
    all_s = settings or {}
    root_settings = all_s.get(None, {})
    named: dict[str, dict[str, str]] = {k: v for k, v in all_s.items() if k is not None}

    try:
        component_metadatas = validate_components_for_install(metadata, source, plugin_name, is_upgrade=False)
        destination_path = validate_for_install(metadata, ctx)

        if editable:
            assert isinstance(source, Path)
            source_dir = source.resolve()
            validate_metadata_in_plugin_directory(source_dir)

            python_deps = collect_python_dependencies_from_directory(source_dir, metadata)
            install_python_dependencies(python_deps, ctx, excluded_plugins={metadata.plugin.name})

            apply_plugin_editable_files(source_dir, destination_path, metadata.plugin.name)
        else:
            assert isinstance(source, bytes)
            metadata_path = get_metadata_path_from_plugin_archive(source, plugin_name)
            validate_metadata_in_plugin_archive(source, metadata_path, metadata)

            python_deps = collect_python_dependencies_from_archive(source, metadata_path, metadata)
            install_python_dependencies(python_deps, ctx)

            apply_plugin_archive_files(source, metadata_path.parent, destination_path, metadata.plugin.name)

        files_written = True

        if root_settings and not apply_resolved_settings(plugin_name, metadata, root_settings):
            _rollback_fresh_install(plugin_name)
            return InstallResult(
                plugin=plugin_name,
                version=version,
                status=InstallStatus.FAILED,
                reason="settings validation failed",
            )

        for comp_name, comp_meta in component_metadatas.items():
            comp_config = named.pop(comp_name, {})
            if not comp_config:
                continue
            if not apply_resolved_settings(comp_name, comp_meta, comp_config):
                _rollback_fresh_install(plugin_name)
                return InstallResult(
                    plugin=plugin_name,
                    version=version,
                    status=InstallStatus.FAILED,
                    reason=f"settings validation failed for component '{comp_name}'",
                )

    except Exception as e:
        logger.debug("apply_install failed: %s", e, exc_info=True)
        if files_written:
            try:
                _rollback_fresh_install(plugin_name)
            except Exception:
                logger.debug("rollback also failed", exc_info=True)
        return InstallResult(
            plugin=plugin_name,
            version=version,
            status=InstallStatus.FAILED,
            reason=str(e),
        )

    dep_results = _install_loose_dependencies(metadata, plugin_repo, ctx, named or None)

    return InstallResult(
        plugin=plugin_name,
        version=version,
        status=InstallStatus.SUCCESS,
        dependencies=dep_results,
    )


def apply_upgrade(
    *,
    zip_data: bytes,
    plugin_name: str,
    metadata: IDAMetadataDescriptor,
    ctx: InstallContext,
    settings: dict[str | None, dict[str, str]] | None = None,
    plugin_repo: BasePluginRepo | None = None,
    old_deps: list[str] | None = None,
) -> InstallResult:
    """Library-level upgrade entry point.

    Orchestrates: validation, Python dependencies, file upgrade with rollback,
    settings, and loose dependency installation.
    """
    from hcli.lib.ida.plugin.settings import apply_resolved_settings

    version = metadata.plugin.version
    all_s = settings or {}
    root_settings = all_s.get(None, {})
    named: dict[str, dict[str, str]] = {k: v for k, v in all_s.items() if k is not None}

    try:
        metadata_path = get_metadata_path_from_plugin_archive(zip_data, plugin_name)
        validate_metadata_in_plugin_archive(zip_data, metadata_path, metadata)

        component_metadatas = validate_components_for_install(metadata, zip_data, plugin_name, is_upgrade=True)
        destination_path = validate_for_upgrade(metadata, ctx)

        python_deps = collect_python_dependencies_from_archive(zip_data, metadata_path, metadata)
        install_python_dependencies(python_deps, ctx, excluded_plugins={metadata.plugin.name})

        apply_upgrade_with_rollback(zip_data, metadata_path.parent, destination_path, metadata.plugin.name)
    except Exception as e:
        logger.debug("apply_upgrade failed: %s", e, exc_info=True)
        return InstallResult(
            plugin=plugin_name,
            version=version,
            status=InstallStatus.FAILED,
            reason=str(e),
        )

    # The upgrade itself already succeeded; the plugin is at the new version.
    # Rolling back on a settings failure would be worse than leaving settings
    # unconfigured, so just warn.
    if root_settings and not apply_resolved_settings(plugin_name, metadata, root_settings):
        logger.warning("failed to configure settings during upgrade")

    for comp_name, comp_meta in component_metadatas.items():
        comp_config = named.pop(comp_name, {})
        if not comp_config:
            continue
        if not apply_resolved_settings(comp_name, comp_meta, comp_config):
            logger.warning("failed to configure settings for component '%s' during upgrade", comp_name)

    dep_results: list[InstallResult] = []
    if old_deps is not None and plugin_repo is not None:
        old_names = {parse_dependency_spec(s).name for s in old_deps}
        new_names = {parse_dependency_spec(s).name for s in metadata.plugin.dependencies}
        dropped = old_names - new_names
        for name in sorted(dropped):
            dep_results.append(
                InstallResult(
                    plugin=name,
                    version="",
                    status=InstallStatus.ALREADY_INSTALLED,
                    reason="dependency dropped; remains installed",
                )
            )

    dep_results.extend(_install_loose_dependencies(metadata, plugin_repo, ctx, named or None))

    return InstallResult(
        plugin=plugin_name,
        version=version,
        status=InstallStatus.SUCCESS,
        dependencies=dep_results,
    )


def _rollback_fresh_install(plugin_name: str) -> None:
    """Remove a freshly installed plugin on failure."""
    try:
        uninstall_plugin(plugin_name)
    except Exception:
        logger.debug("rollback failed for %s", plugin_name, exc_info=True)


def _install_loose_dependencies(
    metadata: IDAMetadataDescriptor,
    plugin_repo: BasePluginRepo | None,
    ctx: InstallContext,
    settings: dict[str, dict[str, str]] | None = None,
) -> list[InstallResult]:
    """Install loose dependencies and return InstallResult entries."""
    # Deferred: dependencies.py imports from install.py at module level.
    from hcli.lib.ida.plugin.dependencies import install_dependencies

    if not metadata.plugin.dependencies:
        return []

    if plugin_repo is None:
        results: list[InstallResult] = []
        for dep_spec in metadata.plugin.dependencies:
            results.append(
                InstallResult(
                    plugin=dep_spec,
                    version="",
                    status=InstallStatus.FAILED,
                    reason="cannot auto-install from a local source",
                )
            )
        return results

    try:
        dep_result = install_dependencies(
            metadata=metadata,
            plugin_repo=plugin_repo,
            ctx=ctx,
            settings=settings,
        )
    except Exception as e:
        logger.debug("dependency installation failed: %s", e, exc_info=True)
        return [
            InstallResult(
                plugin=metadata.plugin.name,
                version="",
                status=InstallStatus.FAILED,
                reason=f"dependency installation failed: {e}",
            )
        ]

    results = []
    for name in dep_result.installed:
        results.append(
            InstallResult(
                plugin=name,
                version="",
                status=InstallStatus.SUCCESS,
            )
        )
    for name in dep_result.upgraded:
        results.append(
            InstallResult(
                plugin=name,
                version="",
                status=InstallStatus.SUCCESS,
                reason="upgraded",
            )
        )
    for name in dep_result.skipped:
        results.append(
            InstallResult(
                plugin=name,
                version="",
                status=InstallStatus.ALREADY_INSTALLED,
            )
        )
    for name, error in dep_result.failed:
        results.append(
            InstallResult(
                plugin=name,
                version="",
                status=InstallStatus.FAILED,
                reason=error,
            )
        )
    return results
