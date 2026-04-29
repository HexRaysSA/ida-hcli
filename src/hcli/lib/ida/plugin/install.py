import errno
import io
import logging
import pathlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import rich.status

from hcli.lib.console import stderr_console
from hcli.lib.ida import (
    find_current_ida_platform,
    find_current_ida_version,
    get_ida_user_dir,
)
from hcli.lib.ida.plugin import (
    IDAMetadataDescriptor,
    MinimalIDAPluginMetadata,
    get_metadata_from_plugin_archive,
    get_metadata_path_from_plugin_archive,
    get_python_dependencies_from_plugin_archive,
    get_python_dependencies_from_plugin_directory,
    is_binary_plugin_archive,
    is_ida_version_compatible,
    is_source_plugin_archive,
    parse_plugin_version,
    validate_metadata_in_plugin_archive,
    validate_path,
)
from hcli.lib.ida.plugin.exceptions import (
    DependencyInstallationError,
    IDAVersionIncompatibleError,
    InvalidPluginNameError,
    PipNotAvailableError,
    PlatformIncompatibleError,
    PluginAlreadyInstalledError,
    PluginNotInstalledError,
    PluginVersionDowngradeError,
)
from hcli.lib.ida.plugin.reference import normalize_plugin_host
from hcli.lib.ida.python import (
    PIP_OPTIONS_DEFAULT,
    CantInstallPackagesError,
    PipOptions,
    does_current_ida_have_pip,
    find_current_python_executable,
    pip_install_packages,
    verify_pip_can_install_packages,
)
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
    helpers (``get_installed_plugins``, ``is_plugin_installed``, etc.) are
    implemented on top of this list so they agree on what counts as
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


def get_installed_plugins() -> list[tuple[str, str]]:
    """fetch (name, version) pairs for currently installed plugins"""
    return [(r.name, r.version) for r in get_installed_plugin_records()]


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


def validate_can_install_python_dependencies(
    zip_data: bytes,
    metadata: IDAMetadataDescriptor,
    excluded_plugins: list[str] | None = None,
    python_exe: Path | None = None,
    no_build_isolation: bool = False,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
) -> Path | None:
    """Verify Python dependencies can be installed.

    Returns:
        The Python executable path if dependencies were validated, None if no dependencies needed.

    Raises:
        PipNotAvailableError: If pip is not available in IDA's Python
        DependencyInstallationError: If dependencies cannot be installed
    """
    python_dependencies = get_python_dependencies_from_plugin_archive(zip_data, metadata)
    if python_dependencies:
        all_python_dependencies: list[str] = []
        for existing_plugin_path in get_installed_plugin_paths():
            existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
            if excluded_plugins and existing_metadata.plugin.name in excluded_plugins:
                continue

            existing_deps = get_python_dependencies_from_plugin_directory(existing_plugin_path, existing_metadata)
            all_python_dependencies.extend(existing_deps)

        all_python_dependencies.extend(python_dependencies)

        if python_exe is None:
            python_exe = find_current_python_executable()
        logger.debug(f"python: {python_exe}")

        if not does_current_ida_have_pip(python_exe):
            logger.debug("pip not available")
            raise PipNotAvailableError(python_exe)

        try:
            verify_pip_can_install_packages(
                python_exe, all_python_dependencies, pip_options=pip_options, no_build_isolation=no_build_isolation
            )
        except CantInstallPackagesError as e:
            logger.debug("can't install dependencies: %s", e)
            raise DependencyInstallationError(python_dependencies, str(e)) from e

        return python_exe

    return None


def validate_can_install_plugin(
    zip_data: bytes,
    metadata: IDAMetadataDescriptor,
    current_platform: str,
    current_version: str,
    no_build_isolation: bool = False,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
) -> Path | None:
    """Verify plugin can be installed.

    Returns:
        The Python executable path if pip dependencies were validated, None otherwise.

    Raises:
        InvalidPluginNameError: If plugin name is invalid
        PluginAlreadyInstalledError: If plugin is already installed
        PlatformIncompatibleError: If current platform is not supported
        IDAVersionIncompatibleError: If current IDA version is not supported
        PipNotAvailableError: If pip is not available (when dependencies are needed)
        DependencyInstallationError: If dependencies cannot be installed
    """
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        logger.error(f"Can't install plugin: {e!s}")
        raise InvalidPluginNameError(name, str(e)) from e

    if destination_path.exists():
        logger.warning(f"Plugin directory already exists: {destination_path}")
        raise PluginAlreadyInstalledError(name, destination_path)

    platforms = metadata.plugin.platforms
    if current_platform not in platforms:
        logger.warning(f"Current platform not supported: {current_platform}")
        raise PlatformIncompatibleError(current_platform, platforms)

    if metadata.plugin.ida_versions and not is_ida_version_compatible(current_version, metadata.plugin.ida_versions):
        logger.warning(f"Current IDA version not supported: {current_version}")
        raise IDAVersionIncompatibleError(current_version, metadata.plugin.ida_versions)

    return validate_can_install_python_dependencies(
        zip_data, metadata, no_build_isolation=no_build_isolation, pip_options=pip_options
    )


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

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / destination.name
            temp_path.mkdir()

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
                            shutil.rmtree(temp_path, ignore_errors=True)
                            raise NoSpaceError(target_path.parent) from e
                        raise

            logger.debug("creating plugin directory: %s", destination)
            # `move` rather than `rename` to support cross-filesystem operations
            try:
                shutil.move(temp_path, destination)
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    raise NoSpaceError(destination.parent) from e
                raise


def _install_plugin_archive(
    zip_data: bytes,
    name: str,
    no_build_isolation: bool = False,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
):
    path, metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, path, metadata)

    logger.info("installing plugin: %s (%s)", metadata.plugin.name, metadata.plugin.version)

    with rich.status.Status("finding IDA installation", console=stderr_console):
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

    python_exe = validate_can_install_plugin(
        zip_data,
        metadata,
        current_platform,
        current_version,
        no_build_isolation=no_build_isolation,
        pip_options=pip_options,
    )

    destination_path = get_plugin_directory(metadata.plugin.name)

    metadata_path = get_metadata_path_from_plugin_archive(zip_data, name)
    plugin_subdirectory = metadata_path.parent

    # TODO: install idaPluginDependencies

    python_dependencies = get_python_dependencies_from_plugin_archive(zip_data, metadata)
    if python_dependencies:
        with rich.status.Status("collecting existing Python dependencies", console=stderr_console):
            all_python_dependencies: list[str] = []
            for existing_plugin_path in get_installed_plugin_paths():
                existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
                existing_deps = get_python_dependencies_from_plugin_directory(existing_plugin_path, existing_metadata)
                all_python_dependencies.extend(existing_deps)

            logger.debug("installing new python dependencies: %s", python_dependencies)
            all_python_dependencies.extend(python_dependencies)

        with rich.status.Status(
            f"installing Python dependencies: {', '.join(python_dependencies)}", console=stderr_console
        ):
            assert python_exe is not None
            try:
                pip_install_packages(
                    python_exe, all_python_dependencies, pip_options=pip_options, no_build_isolation=no_build_isolation
                )
            except CantInstallPackagesError:
                logger.debug("can't install dependencies")
                raise

    extract_zip_subdirectory_to(zip_data, plugin_subdirectory, destination_path)


def install_source_plugin_archive(
    zip_data: bytes, name: str, no_build_isolation: bool = False, pip_options: PipOptions = PIP_OPTIONS_DEFAULT
):
    return _install_plugin_archive(zip_data, name, no_build_isolation=no_build_isolation, pip_options=pip_options)


def install_binary_plugin_archive(
    zip_data: bytes, name: str, no_build_isolation: bool = False, pip_options: PipOptions = PIP_OPTIONS_DEFAULT
):
    return _install_plugin_archive(zip_data, name, no_build_isolation=no_build_isolation, pip_options=pip_options)


def install_plugin_archive(
    zip_data: bytes, name: str, no_build_isolation: bool = False, pip_options: PipOptions = PIP_OPTIONS_DEFAULT
):
    if is_source_plugin_archive(zip_data, name):
        install_source_plugin_archive(zip_data, name, no_build_isolation=no_build_isolation, pip_options=pip_options)
    elif is_binary_plugin_archive(zip_data, name):
        install_binary_plugin_archive(zip_data, name, no_build_isolation=no_build_isolation, pip_options=pip_options)
    else:
        raise ValueError("Invalid plugin archive")


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


def install_plugin_directory_editable(source_dir: Path, name: str, no_build_isolation: bool = False):
    """Install a plugin from a local source directory by symlinking it into
    $IDAUSR/plugins/<name>.

    Edits to files in `source_dir` take effect on the next plugin reload, with
    no re-install needed -- the standard development workflow for plugin
    authors.

    Python dependencies declared in `ida-plugin.json` are installed the same
    way as a regular install. The destination path is replaced if it already
    exists (file, directory, or stale symlink), but the source directory is
    never touched.
    """
    source_dir = source_dir.resolve()
    metadata = get_metadata_from_plugin_directory(source_dir)
    validate_metadata_in_plugin_directory(source_dir)

    if metadata.plugin.name != name:
        raise ValueError(
            f"plugin name mismatch: caller passed '{name}', ida-plugin.json declares '{metadata.plugin.name}'"
        )

    logger.info("installing plugin (editable): %s (%s)", metadata.plugin.name, metadata.plugin.version)

    with rich.status.Status("finding IDA installation", console=stderr_console):
        current_platform = find_current_ida_platform()
        current_version = find_current_ida_version()

    platforms = metadata.plugin.platforms
    if current_platform not in platforms:
        logger.warning(f"Current platform not supported: {current_platform}")
        raise PlatformIncompatibleError(current_platform, platforms)

    if metadata.plugin.ida_versions and not is_ida_version_compatible(current_version, metadata.plugin.ida_versions):
        logger.warning(f"Current IDA version not supported: {current_version}")
        raise IDAVersionIncompatibleError(current_version, metadata.plugin.ida_versions)

    try:
        destination_path = get_plugin_directory(metadata.plugin.name)
    except ValueError as e:
        logger.error(f"Can't install plugin: {e!s}")
        raise InvalidPluginNameError(metadata.plugin.name, str(e)) from e

    # Validate + install Python dependencies. Excludes the current plugin from
    # the existing-installed set so a re-install of the same plugin doesn't
    # double-count its own deps when verifying the resolver.
    python_dependencies = get_python_dependencies_from_plugin_directory(source_dir, metadata)
    if python_dependencies:
        with rich.status.Status("collecting existing Python dependencies", console=stderr_console):
            all_python_dependencies: list[str] = []
            for existing_plugin_path in get_installed_plugin_paths():
                try:
                    existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
                except Exception as e:
                    logger.debug("skipping unreadable plugin metadata at %s: %s", existing_plugin_path, e)
                    continue
                if existing_metadata.plugin.name == metadata.plugin.name:
                    continue
                existing_deps = get_python_dependencies_from_plugin_directory(existing_plugin_path, existing_metadata)
                all_python_dependencies.extend(existing_deps)
            all_python_dependencies.extend(python_dependencies)

        python_exe = find_current_python_executable()
        if not does_current_ida_have_pip(python_exe):
            raise PipNotAvailableError(python_exe)

        try:
            verify_pip_can_install_packages(python_exe, all_python_dependencies, no_build_isolation=no_build_isolation)
        except CantInstallPackagesError as e:
            raise DependencyInstallationError(python_dependencies, str(e)) from e

        with rich.status.Status(
            f"installing Python dependencies: {', '.join(python_dependencies)}", console=stderr_console
        ):
            try:
                pip_install_packages(python_exe, all_python_dependencies, no_build_isolation=no_build_isolation)
            except CantInstallPackagesError:
                logger.debug("can't install dependencies")
                raise

    # Remove any existing install at the target. is_symlink() is checked
    # before exists() because a broken symlink fails exists() but should
    # still be replaced.
    if destination_path.is_symlink() or destination_path.is_file():
        destination_path.unlink()
    elif destination_path.exists():
        shutil.rmtree(destination_path)

    try:
        destination_path.symlink_to(source_dir, target_is_directory=True)
    except OSError as e:
        raise ValueError(
            f"Failed to create symlink {destination_path} -> {source_dir}: {e}. "
            "On Windows, symlink creation requires Developer Mode or "
            "administrator privileges."
        ) from e

    logger.info("symlinked %s -> %s", destination_path, source_dir)


def validate_can_uninstall_plugin(name: str) -> None:
    """Verify plugin can be uninstalled.

    Raises:
        PluginNotInstalledError: If plugin is not installed
    """
    # find_installed_plugin raises PluginNotInstalledError when no match; name
    # matching is case-insensitive.
    find_installed_plugin(name)


def uninstall_plugin(name: str):
    # NOTE: keep this in sync with upgrade (checkpoint/rollback) which has an inlined copy.

    record = find_installed_plugin(name)
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
    else:
        shutil.rmtree(record.path)


def is_plugin_installed(name: str) -> bool:
    try:
        find_installed_plugin(name)
    except PluginNotInstalledError:
        return False
    return True


def validate_can_upgrade_plugin(
    zip_data: bytes,
    metadata: IDAMetadataDescriptor,
    current_platform: str,
    current_version: str,
    no_build_isolation: bool = False,
    pip_options: PipOptions = PIP_OPTIONS_DEFAULT,
) -> None:
    """Verify plugin can be upgraded.

    Raises:
        InvalidPluginNameError: If plugin name is invalid
        PluginNotInstalledError: If plugin is not currently installed
        PlatformIncompatibleError: If current platform is not supported
        IDAVersionIncompatibleError: If current IDA version is not supported
        PipNotAvailableError: If pip is not available (when dependencies are needed)
        DependencyInstallationError: If dependencies cannot be installed
    """
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        logger.error(f"Can't upgrade plugin: {e!s}")
        raise InvalidPluginNameError(name, str(e)) from e

    if not destination_path.exists():
        logger.warning(f"Plugin directory doesn't exist: {destination_path}")
        raise PluginNotInstalledError(name)

    platforms = metadata.plugin.platforms
    if current_platform not in platforms:
        logger.warning(f"Current platform not supported: {current_platform}")
        raise PlatformIncompatibleError(current_platform, platforms)

    if metadata.plugin.ida_versions and not is_ida_version_compatible(current_version, metadata.plugin.ida_versions):
        logger.warning(f"Current IDA version not supported: {current_version}")
        raise IDAVersionIncompatibleError(current_version, metadata.plugin.ida_versions)

    validate_can_install_python_dependencies(
        zip_data, metadata, excluded_plugins=[name], no_build_isolation=no_build_isolation, pip_options=pip_options
    )


def upgrade_plugin_archive(
    zip_data: bytes, name: str, no_build_isolation: bool = False, pip_options: PipOptions = PIP_OPTIONS_DEFAULT
):
    path, metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, path, metadata)

    if not is_plugin_installed(metadata.plugin.name):
        raise PluginNotInstalledError(metadata.plugin.name)

    current_platform = find_current_ida_platform()
    current_version = find_current_ida_version()

    validate_can_upgrade_plugin(
        zip_data,
        metadata,
        current_platform,
        current_version,
        no_build_isolation=no_build_isolation,
        pip_options=pip_options,
    )

    plugin_path = get_plugin_directory(metadata.plugin.name)
    existing_metadata = get_metadata_from_plugin_directory(plugin_path)

    new_version = parse_plugin_version(metadata.plugin.version)
    existing_version = parse_plugin_version(existing_metadata.plugin.version)

    if new_version <= existing_version:
        logger.warning(
            f"New version {metadata.plugin.version} is not greater than existing version {existing_metadata.plugin.version}"
        )
        raise PluginVersionDowngradeError(
            metadata.plugin.name, existing_metadata.plugin.version, metadata.plugin.version
        )

    rollback_path = plugin_path.parent / (metadata.plugin.name + ".rollback")
    if rollback_path.exists():
        raise RuntimeError("rollback path already exists for some reason")
    shutil.move(plugin_path, rollback_path)

    try:
        install_plugin_archive(zip_data, name, no_build_isolation=no_build_isolation, pip_options=pip_options)
    except Exception as e:
        logger.debug("error during upgrade: install: %s", e)
        logger.debug("rolling back to prior version")
        shutil.rmtree(plugin_path, ignore_errors=True)
        shutil.move(rollback_path, plugin_path)
        raise
    else:
        shutil.rmtree(rollback_path)
