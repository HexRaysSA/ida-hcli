import io
import logging
import pathlib
import shutil
import tempfile
import zipfile
from pathlib import Path

from hcli.lib.ida import find_current_ida_platform, find_current_ida_version, get_ida_user_dir
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
from hcli.lib.ida.python import (
    CantInstallPackagesError,
    does_current_ida_have_pip,
    find_current_python_executable,
    pip_install_packages,
    verify_pip_can_install_packages,
)

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
    if not name or name == "." or name == "..":
        raise ValueError(f"Invalid path component: '{name}'.")

    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"Invalid path component: '{name}'. Must contain only ASCII characters")

    if "\t" in name or "\n" in name or "\r" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain tabs or newlines")

    if "/" in name or "\\" in name:
        raise ValueError(f"Invalid path component: '{name}'. Cannot contain slashes")


# TODO: dedup
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


def get_installed_plugin_paths() -> list[Path]:
    plugins_dir = get_plugins_directory()
    installed_paths: list[Path] = []

    if not plugins_dir.exists():
        return installed_paths

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

        metadata = get_metadata_from_plugin_directory(plugin_path)
        if metadata.plugin.name != plugin_path.name:
            logger.debug("plugin name and path mismatch")
            continue

        installed_paths.append(plugin_path)

    return installed_paths


def get_installed_plugins() -> list[tuple[str, str]]:
    """fetch (name, version) pairs for currently installed plugins"""
    installed_plugins: list[tuple[str, str]] = []

    for plugin_path in get_installed_plugin_paths():
        try:
            metadata = get_metadata_from_plugin_directory(plugin_path)
            installed_plugins.append((metadata.plugin.name, metadata.plugin.version))
        except ValueError as e:
            logger.warning(f"Failed to read metadata from {plugin_path}: {e}")
            continue

    return installed_plugins


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


def can_install_python_dependencies(
    zip_data: bytes, metadata: IDAMetadataDescriptor, excluded_plugins: list[str] | None = None
) -> bool:
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

        python_exe = find_current_python_executable()

        if not does_current_ida_have_pip(python_exe):
            logger.debug("pip not available")
            return False

        try:
            verify_pip_can_install_packages(python_exe, all_python_dependencies)
        except CantInstallPackagesError:
            logger.debug("can't install dependencies")
            return False

    return True


def can_install_plugin(
    zip_data: bytes, metadata: IDAMetadataDescriptor, current_platform: str, current_version: str
) -> bool:
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        logger.error(f"Can't install plugin: {str(e)}")
        return False

    if destination_path.exists():
        logger.warning(f"Plugin directory already exists: {destination_path}")
        return False

    platforms = metadata.plugin.platforms
    if current_platform not in platforms:
        logger.warning(f"Current platform not supported: {current_platform}")
        return False

    if metadata.plugin.ida_versions and not is_ida_version_compatible(current_version, metadata.plugin.ida_versions):
        logger.warning(f"Current IDA version not supported: {current_version}")
        return False

    if not can_install_python_dependencies(zip_data, metadata):
        return False

    return True


def extract_zip_subdirectory_to(zip_data: bytes, subdirectory: Path, destination: Path):
    """Extract a subdirectory from a zip archive to a destination path."""
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_file:
        plugin_dir_prefix = str(subdirectory) + "/"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / destination.name
            temp_path.mkdir()

            for file_info in zip_file.infolist():
                if not file_info.filename.startswith(plugin_dir_prefix):
                    continue

                if file_info.filename == plugin_dir_prefix:
                    continue

                relative_path = pathlib.PurePosixPath(file_info.filename).relative_to(plugin_dir_prefix.rstrip("/"))
                if str(relative_path) == ".":
                    continue

                target_path = temp_path / relative_path

                if file_info.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zip_file.open(file_info.filename) as source_file:
                        with target_path.open("wb") as target_file:
                            shutil.copyfileobj(source_file, target_file)

            temp_path.rename(destination)


def _install_plugin_archive(zip_data: bytes, name: str):
    metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, metadata)

    logger.info("installing plugin: %s (%s)", metadata.plugin.name, metadata.plugin.version)

    current_platform = find_current_ida_platform()
    current_version = find_current_ida_version()

    if not can_install_plugin(zip_data, metadata, current_platform, current_version):
        logger.warning("can't install plugin")
        raise RuntimeError("Plugin installation is not possible")

    # path within IDAUSR/plugins to the new plugin
    #
    # note: there's a potential for collision here:
    # user1/plugin destination directory ($IDAUSER/plugins/plugin) collides with user2/plugin
    # we could fix this by prefixing the user/org name, like user1--plugin
    destination_path = get_plugin_directory(metadata.plugin.name)

    # path within the zip to ida-plugin.json
    metadata_path = get_metadata_path_from_plugin_archive(zip_data, name)
    plugin_subdirectory = metadata_path.parent

    # TODO: log steps (and revert if necessary)
    # TODO: install idaPluginDependencies

    # Get Python dependencies using helper function
    python_dependencies = get_python_dependencies_from_plugin_archive(zip_data, metadata)
    if python_dependencies:
        all_python_dependencies: list[str] = []
        for existing_plugin_path in get_installed_plugin_paths():
            existing_metadata = get_metadata_from_plugin_directory(existing_plugin_path)
            existing_deps = get_python_dependencies_from_plugin_directory(existing_plugin_path, existing_metadata)
            all_python_dependencies.extend(existing_deps)

        logger.debug("installing new python dependencies: %s", python_dependencies)
        all_python_dependencies.extend(python_dependencies)

        python_exe = find_current_python_executable()
        try:
            pip_install_packages(python_exe, all_python_dependencies)
        except CantInstallPackagesError:
            logger.debug("can't install dependencies")
            raise

    extract_zip_subdirectory_to(zip_data, plugin_subdirectory, destination_path)


def install_source_plugin_archive(zip_data: bytes, name: str):
    return _install_plugin_archive(zip_data, name)


def install_binary_plugin_archive(zip_data: bytes, name: str):
    return _install_plugin_archive(zip_data, name)


def install_plugin_archive(zip_data: bytes, name: str):
    if is_source_plugin_archive(zip_data, name):
        install_source_plugin_archive(zip_data, name)
    elif is_binary_plugin_archive(zip_data, name):
        install_binary_plugin_archive(zip_data, name)
    else:
        raise ValueError("Invalid plugin archive")


def can_uninstall_plugin(name: str) -> bool:
    if name not in [name for (name, _version) in get_installed_plugins()]:
        logger.warning(f"Plugin directory not installed: {name}")
        return False

    return True


def uninstall_plugin(name: str):
    # NOTE: keep this in sync with upgrade (checkpoint/rollback) which has an inlined copy.

    if not can_uninstall_plugin(name):
        raise ValueError("can't uninstall plugin")

    plugin_path = get_plugin_directory(name)
    metadata = get_metadata_from_plugin_directory(plugin_path)
    logger.info("uninstalling plugin: %s (%s)", name, metadata.plugin.version)

    # note that the pythonDependencies of the plugin aren't pruned.
    # we could re-collect all the deps requested by other plugins
    # but we shouldn't do a sync, since there might be other utils installed by the user.
    # so I think its better to just leave the orphans around.

    shutil.rmtree(plugin_path)


def is_plugin_installed(name: str) -> bool:
    installed_plugins = [name for (name, _version) in get_installed_plugins()]
    logger.debug("installed plugins: %s", installed_plugins)
    return name in installed_plugins


def can_upgrade_plugin(
    zip_data: bytes, metadata: IDAMetadataDescriptor, current_platform: str, current_version: str
) -> bool:
    name = metadata.plugin.name
    try:
        destination_path = get_plugin_directory(name)
    except ValueError as e:
        logger.error(f"Can't upgrade plugin: {str(e)}")
        return False

    if not destination_path.exists():
        logger.warning(f"Plugin directory doesn't exist: {destination_path}")
        return False

    platforms = metadata.plugin.platforms
    if current_platform not in platforms:
        logger.warning(f"Current platform not supported: {current_platform}")
        return False

    if metadata.plugin.ida_versions and not is_ida_version_compatible(current_version, metadata.plugin.ida_versions):
        logger.warning(f"Current IDA version not supported: {current_version}")
        return False

    if not can_install_python_dependencies(zip_data, metadata, excluded_plugins=[name]):
        return False

    return True


def upgrade_plugin_archive(zip_data: bytes, name: str):
    metadata = get_metadata_from_plugin_archive(zip_data, name)
    validate_metadata_in_plugin_archive(zip_data, metadata)

    if not is_plugin_installed(metadata.plugin.name):
        raise ValueError(f"plugin is not installed: {metadata.plugin.name}")

    current_platform = find_current_ida_platform()
    current_version = find_current_ida_version()

    if not can_upgrade_plugin(zip_data, metadata, current_platform, current_version):
        logger.warning("can't upgrade plugin")
        raise RuntimeError("Plugin upgrade is not possible")

    plugin_path = get_plugin_directory(metadata.plugin.name)
    existing_metadata = get_metadata_from_plugin_directory(plugin_path)

    new_version = parse_plugin_version(metadata.plugin.version)
    existing_version = parse_plugin_version(existing_metadata.plugin.version)

    if new_version <= existing_version:
        logger.warning(
            f"New version {metadata.plugin.version} is not greater than existing version {existing_metadata.plugin.version}"
        )
        raise ValueError(
            f"Cannot upgrade plugin {metadata.plugin.name}: new version {metadata.plugin.version} is not greater than existing version {existing_metadata.plugin.version}"
        )

    # as long as uninstallation is as simple as removing the directory
    # inline that logic here (the checkpoint/rollback).

    # note: this could conflict with a malicious plugin name, like `foo.rollback`
    # maybe put this into a different directory (XDG_CACHE_HOME?)
    rollback_path = plugin_path.parent / (metadata.plugin.name + ".rollback")
    if rollback_path.exists():
        raise RuntimeError("rollback path already exists for some reason")
    plugin_path.rename(rollback_path)

    try:
        install_plugin_archive(zip_data, name)
    except Exception as e:
        logger.debug("error during upgrade: install: %s", e)
        logger.debug("rolling back to prior version")
        shutil.rmtree(plugin_path, ignore_errors=True)
        rollback_path.rename(plugin_path)
        raise e
    else:
        shutil.rmtree(rollback_path)
