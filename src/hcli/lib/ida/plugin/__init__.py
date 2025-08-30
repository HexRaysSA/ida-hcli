import io
import logging
import pathlib
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "tomli is required for Python < 3.11. Install it with: pip install tomli"
        ) from None

import packaging.version
import semantic_version
from pydantic import AliasPath, BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# TODO: dedup with hcli.lib.ida.plugin
class IDAPluginMetadata(BaseModel):
    """IDA Plugin metadata from ida-plugin.json"""

    schema_: str | None = Field(validation_alias="$schema", default=None)
    metadata_version: int = Field(validation_alias="IDAMetadataDescriptorVersion")

    #######################
    # required
    name: str = Field(validation_alias=AliasPath("plugin", "name"))
    # TODO: must validate via: packaging.version.parse
    version: str = Field(validation_alias=AliasPath("plugin", "version"))
    entry_point: str = Field(validation_alias=AliasPath("plugin", "entryPoint"))

    #######################
    # optional
    categories: list[str] = Field(validation_alias=AliasPath("plugin", "categories"), default_factory=list)
    logo_path: Optional[str] | None = Field(validation_alias=AliasPath("plugin", "logoPath"), default=None)
    # empty implies all versions
    ida_versions: Optional[str] | None = Field(validation_alias=AliasPath("plugin", "idaVersions"), default=None)
    description: Optional[str] | None = Field(validation_alias=AliasPath("plugin", "description"), default=None)

    python_dependencies: list[str] | str = Field(
        validation_alias=AliasPath("plugin", "pythonDependencies"), default_factory=list
    )


def parse_pep723_metadata(python_file_content: str) -> list[str]:
    """Parse PEP 723 inline script metadata from Python file content.

    Returns a list of dependencies found in the PEP 723 metadata block,
    or an empty list if no metadata block is found.
    
    Raises:
        ValueError: If metadata block is found but contains invalid TOML or unexpected data format
    """
    # Look for PEP 723 metadata block
    # The block starts with # /// script and ends with # ///
    pattern = r"#\s*///\s*script\s*\n(.*?)#\s*///\s*\n"
    match = re.search(pattern, python_file_content, re.DOTALL | re.MULTILINE)

    if not match:
        return []

    metadata_block = match.group(1)

    # Remove comment prefixes from each line
    lines = []
    for line in metadata_block.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        if line:  # Skip empty lines
            lines.append(line)

    # Join lines and parse as TOML
    toml_content = "\n".join(lines)

    try:
        metadata = tomllib.loads(toml_content)
        dependencies = metadata.get("dependencies", [])
        if isinstance(dependencies, list):
            return dependencies
        else:
            raise ValueError(f"PEP 723 dependencies must be a list, got {type(dependencies).__name__}")
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"Failed to parse PEP 723 TOML metadata: {e}") from e


def get_file_content_from_plugin_archive(zip_data: bytes, plugin_name: str, relative_path: str) -> bytes:
    """Get file content from a plugin archive relative to the plugin's metadata file.
    
    Args:
        zip_data: The zip archive data
        plugin_name: The name of the plugin 
        relative_path: Path relative to the plugin's metadata file
        
    Returns:
        The file content as bytes
    """
    metadata_path = get_metadata_path_from_plugin_archive(zip_data, plugin_name)
    plugin_dir = metadata_path.parent
    file_path = plugin_dir / relative_path

    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        with zip_file.open(str(file_path)) as f:
            return f.read()


def get_python_dependencies_from_plugin_archive(zip_data: bytes, metadata: IDAPluginMetadata) -> list[str]:
    """Get Python dependencies from a plugin archive.

    If pythonDependencies is "inline", parse PEP 723 metadata from the entry point.
    Otherwise, return the pythonDependencies list directly.
    
    Raises:
        ValueError: If entry point is not a Python file for inline dependencies,
                   if file is not found, or if dependencies format is unexpected
    """
    if isinstance(metadata.python_dependencies, str) and metadata.python_dependencies == "inline":
        # Parse PEP 723 metadata from entry point
        if not metadata.entry_point.endswith(".py"):
            raise ValueError("Entry point must be a Python file (.py) for inline dependencies")

        try:
            python_content_bytes = get_file_content_from_plugin_archive(zip_data, metadata.name, metadata.entry_point)
            python_content = python_content_bytes.decode("utf-8")
            return parse_pep723_metadata(python_content)
        except ValueError:
            # Re-raise ValueError from helper functions
            raise
    else:
        # Return the list of dependencies directly
        if isinstance(metadata.python_dependencies, list):
            return metadata.python_dependencies
        else:
            raise ValueError(f"Unexpected python_dependencies type: {type(metadata.python_dependencies).__name__}")


def get_python_dependencies_from_plugin_directory(plugin_path: Path, metadata: IDAPluginMetadata) -> list[str]:
    """Get Python dependencies from a plugin directory.

    If pythonDependencies is "inline", parse PEP 723 metadata from the entry point.
    Otherwise, return the pythonDependencies list directly.
    
    Raises:
        ValueError: If entry point is not a Python file for inline dependencies,
                   if file is not found, or if dependencies format is unexpected
    """
    if isinstance(metadata.python_dependencies, str) and metadata.python_dependencies == "inline":
        # Parse PEP 723 metadata from entry point
        if not metadata.entry_point.endswith(".py"):
            raise ValueError("Entry point must be a Python file (.py) for inline dependencies")

        entry_point_path = plugin_path / metadata.entry_point

        python_content = entry_point_path.read_text(encoding="utf-8")
        return parse_pep723_metadata(python_content)
    else:
        # Return the list of dependencies directly
        if isinstance(metadata.python_dependencies, list):
            return metadata.python_dependencies
        else:
            raise ValueError(f"Unexpected python_dependencies type: {type(metadata.python_dependencies).__name__}")


def get_metadatas_with_paths_from_plugin_archive(zip_data: bytes) -> Iterator[tuple[Path, IDAPluginMetadata]]:
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        for file_path in zip_file.namelist():
            if not file_path.endswith("ida-plugin.json"):
                continue

            with zip_file.open(file_path) as f:
                try:
                    metadata = IDAPluginMetadata.model_validate_json(f.read().decode("utf-8"))
                except (ValueError, ValidationError):
                    continue
                else:
                    yield Path(file_path), metadata


def get_metadata_path_from_plugin_archive(zip_data: bytes, name: str) -> Path:
    for path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.name == name:
            return path

    raise ValueError(f"plugin '{name}' not found in zip archive")


def get_metadata_from_plugin_archive(zip_data: bytes, name: str) -> IDAPluginMetadata:
    """Extract ida-plugin.json metadata for plugin with the given name from zip archive without extracting"""

    for _path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.name == name:
            return metadata

    raise ValueError(f"plugin '{name}' not found in zip archive")


PLATFORM_WINDOWS = "windows-x86_64"
PLATFORM_LINUX = "linux-x86_64"
PLATFORM_MACOS_INTEL = "macos-x86_64"
PLATFORM_MACOS_ARM = "macos-aarch64"

ALL_PLATFORMS = frozenset(
    {
        PLATFORM_WINDOWS,
        PLATFORM_LINUX,
        PLATFORM_MACOS_INTEL,
        PLATFORM_MACOS_ARM,
    }
)


def does_path_exist_in_zip_archive(zip_data: bytes, path: str) -> bool:
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        return path in zip_file.namelist()


def does_plugin_path_exist_in_plugin_archive(zip_data: bytes, plugin_name: str, relative_path: str) -> bool:
    """does the given path exist relative to the metadata file of the given plugin?"""
    metadata_path = get_metadata_path_from_plugin_archive(zip_data, plugin_name)
    plugin_root_path = Path(metadata_path).parent
    candidate_path = plugin_root_path / Path(relative_path)
    return does_path_exist_in_zip_archive(zip_data, str(candidate_path))


def discover_platforms_from_plugin_archive(zip_data: bytes, name: str) -> frozenset[str]:
    if is_source_plugin_archive(zip_data, name):
        return ALL_PLATFORMS
    elif is_binary_plugin_archive(zip_data, name):
        metadata = get_metadata_from_plugin_archive(zip_data, name)
        if metadata.entry_point.lower().endswith(".so"):
            return frozenset({PLATFORM_LINUX})
        elif metadata.entry_point.lower().endswith(".dylib"):
            # assume universal binary
            return frozenset({PLATFORM_MACOS_INTEL, PLATFORM_MACOS_ARM})
        elif metadata.entry_point.lower().endswith(".dll"):
            return frozenset({PLATFORM_WINDOWS})
        else:
            # entrypoint should be a bare filename
            # and we need to test for the existence of files with candidate extensions (.so, .dylib, .dll)
            platforms = set()
            extensions = [
                (".so", {PLATFORM_LINUX}),
                (".dll", {PLATFORM_WINDOWS}),
                ("_aarch64.dylib", {PLATFORM_MACOS_ARM}),
                ("_x86_64.dylib", {PLATFORM_MACOS_INTEL}),
            ]
            for ext, plats in extensions:
                if does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.entry_point + ext):
                    platforms.update(plats)

            # check for universal binary
            if not platforms.intersection({PLATFORM_MACOS_INTEL, PLATFORM_MACOS_ARM}):
                if does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.entry_point + ".dylib"):
                    platforms.update({PLATFORM_MACOS_INTEL, PLATFORM_MACOS_ARM})

            if platforms:
                return frozenset(platforms)

            raise ValueError("failed to discover platforms: entry point not found")
    else:
        raise ValueError("not a valid plugin archive")


def parse_plugin_version(version: str) -> semantic_version.Version:
    normalized_version = version
    normalized_version = normalized_version.lstrip("v")
    return semantic_version.Version(normalized_version, partial=True)


def parse_ida_version(version: str) -> semantic_version.Version:
    if re.match(r"\d\.\d\.\d", version):
        raise ValueError("invalid version string: three components, like X.Y.Z")

    # now we're guaranteed to only have one (X) or two (X.Y) component versions

    # negative lookbehind, pattern, negative lookahead
    normalized_version = re.sub(r"(?<![.sp])(\d+)(?![.])", r"\1.0.0", version)
    normalized_version = re.sub(r"(?<![.sp])(\d+)\.(\d+)(?![.])", r"\1.\2.0", normalized_version)

    # now we have three component versions, all ending with ".0"

    # map X.Y.0spZ to X.Y.Z
    # because if we use X.Y.0+spZ, the "+spZ" is not compared,
    # as its considered "build metadata"
    normalized_version = re.sub(r"\.0sp(\d+)", r".\1", normalized_version)

    if normalized_version != version:
        logger.debug("normalized %s -> %s", version, normalized_version)

    return semantic_version.Version(normalized_version)


def parse_ida_version_spec(spec: str) -> semantic_version.SimpleSpec:
    if re.match(r"\d\.\d\.\d", spec):
        raise ValueError("invalid spec string: three components, like X.Y.Z")

    # now we're guaranteed to only have one (X) or two (X.Y) component versions

    # negative lookbehind, pattern, negative lookahead
    normalized_spec = re.sub(r"(?<![.sp])(\d+)(?![.])", r"\1.0.0", spec)
    normalized_spec = re.sub(r"(?<![.sp])(\d+)\.(\d+)(?![.])", r"\1.\2.0", normalized_spec)

    # now we have three component versions, all ending with ".0"

    # map X.Y.0spZ to X.Y.Z
    # because if we use X.Y.0+spZ, the "+spZ" is not compared,
    # as its considered "build metadata"
    normalized_spec = re.sub(r"\.0sp(\d+)", r".\1", normalized_spec)

    if normalized_spec != spec:
        logger.debug("normalized %s -> %s", spec, normalized_spec)

    return semantic_version.SimpleSpec(normalized_spec)


def is_ida_version_compatible(current_version: str, version_spec: str) -> bool:
    """Check if current IDA version is compatible with the version specifier.

    Note that all IDA versions are expected to have at most two components,
    like "9" or "9.1". Service packs are also supported: "9.1sp".
    Three components are NOT supported: "9.1.0"

    Args:
        current_version: Current IDA version (e.g., "9.1")
        version_spec: Version specifier (e.g., ">=8.0", "~=9.0", ">=0")

    Returns:
        True if current version satisfies the specifier
    """
    cur = parse_ida_version(current_version)
    spec = parse_ida_version_spec(version_spec)
    return cur in spec


# expect paths to be:
# - relative
# - contain only ASCII
# - not contain traversals up
def validate_path(path: str, field_name: str) -> None:
    if not path:
        return

    try:
        _ = path.encode("ascii")
    except UnicodeEncodeError:
        logger.debug(f"Invalid {field_name} path: '{path}'")
        raise ValueError(f"Invalid {field_name} path: '{path}'")

    # Use PurePosixPath for consistent path handling in zip archives
    try:
        path_obj = pathlib.PurePosixPath(path)
    except Exception:
        logger.debug(f"Invalid {field_name} path: '{path}'")
        raise ValueError(f"Invalid {field_name} path: '{path}'")

    # Check if path is absolute or contains parent directory references
    if path_obj.is_absolute() or ".." in path_obj.parts:
        logger.debug(f"Invalid {field_name} path: '{path}'")
        raise ValueError(f"Invalid {field_name} path: '{path}'")


def validate_metadata_in_plugin_archive(zip_data: bytes, metadata: IDAPluginMetadata):
    """validate the `ida-plugin.json` metadata within the given plugin archive.

    The following things must be checked:
    - metadata version must be "1"
    - the following fields must contain only ASCII. alphanumeric, underscores, dashes, spaces.
      - name
    - the following paths must contain relative paths, no paths like ".." or similar escapes:
      - entry point
      - logo path
    - the file paths must exist in the archive:
      - entry point
      - logo path
    """
    name = metadata.name

    if metadata.metadata_version != 1:
        logger.debug("Invalid metadata version")
        raise ValueError(f"Invalid metadata version: {metadata.metadata_version}. Expected: 1")

    # name contains only ASCII alphanumeric, underscores, dashes, spaces
    if not re.match(r"^[a-zA-Z0-9_\- ]+$", metadata.name):
        logger.debug("Invalid name format")
        raise ValueError(
            f"Invalid name format: '{metadata.name}'. Must contain only ASCII alphanumeric, underscores, dashes"
        )

    if not metadata.entry_point:
        logger.debug("Missing entry point")
        raise ValueError("entry point required")

    validate_path(metadata.entry_point, "entry point")
    if metadata.logo_path:
        validate_path(metadata.logo_path, "logo path")

    if metadata.entry_point.endswith(".py"):
        if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.entry_point):
            raise ValueError(f"Entry point file not found in archive: '{metadata.entry_point}'")
    else:
        # binary plugin
        has_bare_name = False
        for ext in (".so", ".dll", ".dylib"):
            if does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.entry_point + ext):
                has_bare_name = True
        if not has_bare_name:
            raise ValueError(f"Binary plugin file not found in archive: '{metadata.entry_point}'")

    if metadata.logo_path:
        if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.logo_path):
            raise ValueError(f"Logo file not found in archive: '{metadata.logo_path}'")

    # we'd want to validate that there are some platforms,
    # however this is recursive.
    # _ = discover_platforms_from_plugin_archive(zip_data, name)
    _ = packaging.version.parse(metadata.version)


def is_plugin_archive(zip_data: bytes, name: str) -> bool:
    """is the given archive an IDA plugin archive for the given plugin name?"""
    try:
        metadata = get_metadata_from_plugin_archive(zip_data, name)
        validate_metadata_in_plugin_archive(zip_data, metadata)
        return True
    except (ValueError, Exception):
        return False


def is_source_plugin_archive(zip_data: bytes, name: str) -> bool:
    # the following should be true:
    # - the entry point is a filename ending with .py
    try:
        if not is_plugin_archive(zip_data, name):
            return False

        metadata = get_metadata_from_plugin_archive(zip_data, name)

        return metadata.entry_point.endswith(".py")
    except (ValueError, Exception):
        return False


def is_binary_plugin_archive(zip_data: bytes, name: str) -> bool:
    # the following should be true:
    # - the entry point is in the root of the archive
    # - the entry point ends with: .so, .dll, .dylib, or there is no extension
    try:
        if not is_plugin_archive(zip_data, name):
            return False

        metadata = get_metadata_from_plugin_archive(zip_data, name)
        entry_point = metadata.entry_point

        if "/" in entry_point or "\\" in entry_point:
            return False

        binary_extensions = {".so", ".dll", ".dylib"}
        if "." in entry_point:
            _, ext = entry_point.rsplit(".", 1)
            ext = "." + ext.lower()
            return ext in binary_extensions
        else:
            # technically this misses things like `foo.bar` with an implied extension `.so`
            # like `foo.bar.so`
            # TODO: also add check for the entry point file's existence
            return True

    except (ValueError, Exception):
        return False
