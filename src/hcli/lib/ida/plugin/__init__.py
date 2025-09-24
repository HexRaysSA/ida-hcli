import io
import logging
import pathlib
import re
import sys
import typing
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import semantic_version
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)


PLATFORM_WINDOWS = "windows-x86_64"
PLATFORM_LINUX = "linux-x86_64"
PLATFORM_MACOS_INTEL = "macos-x86_64"
PLATFORM_MACOS_ARM = "macos-aarch64"

Platform = Literal[
    "windows-x86_64",
    "linux-x86_64",
    "macos-x86_64",
    "macos-aarch64",
]

ALL_PLATFORMS: frozenset[Platform] = frozenset(typing.get_args(Platform))

IdaVersion = Literal[
    # next versions, unreleased. names are guesses and not any sort of official announcement.
    # we should have these available so that older versions of hcli don't complain about new plugin support.
    "10.0",
    "9.4",
    "9.3",
    # released versions
    "9.2",  #    2025-09
    "9.1",  #    2025-02
    "9.0sp1",  # 2024-12
    "9.0",  #    2024-09
    # 9.0 introduced `ida-plugin.json` support which we strictly rely on for the plugin manager
    # so the plugin manager can't support older versions, without backporting the loader
    # (which is possible, and a possible extension)
    "8.5",  #    2025-02
    "8.4sp2",  # 2024-05
    "8.4sp1",  # 2024-03
    "8.4",  #    2024-02
    "8.3",  #    2023-06
    "8.2sp1",  # 2023-01
    "8.2",  #    2022-12
    "8.1",  #    2022-10
    "8.0sp1",  # 2022-08
    "8.0",  #    2022-07
    "7.7sp1",  # 2022-01
    "7.7",  #    2021-12
    "7.6sp1",
    "7.6",  #    2021-03
    "7.5sp3",  # 2020-10
    "7.5sp2",  # 2020-07
    "7.5sp1",  # 2020-06
    "7.5",  #    2020-05
    "7.4sp1",  # 2019-11
    "7.4",  #    2019-10
    "7.3",  #    2019-06
    "7.2",  #    2018-11
    "7.1",  #    2018-02
    "7.0sp1",  # 2017-11
    "7.0",
    "6.95",  #   2016-08
    "6.9",
    "6.8",
    "6.7",
    "6.6",
    "6.5",
    "6.4",
    "6.3",
    "6.2",
    "6.1",
    "6.0",
    "5.7",
    "5.6",
    "5.5",
    "5.4",
    "5.3",
    "5.2",
    "5.1",
    "5.0",
    "4.9sp1",
    "4.9",
    "4.8",
    "4.7",
    "4.6",
    "4.5",
    "4.4",
    "4.3",
    "4.2",
    "4.1",
    "4.0",
    "3.0",
]

ALL_IDA_VERSIONS: frozenset[IdaVersion] = frozenset(typing.get_args(IdaVersion))


def parse_plugin_version(version: str) -> semantic_version.Version:
    if re.match(r"\.0\d", version):
        # 2025.09.24 -> 2025.9.24
        version = re.sub(r"\.0(\d+)", ".\1", version)
    #
    # we want to use Version, instead of SimpleSpec, because it is sortable
    return semantic_version.Version(version, partial=True)


def parse_ida_version(version: str) -> semantic_version.Version:
    normalized_version = version.replace("sp", ".")

    if re.match(r"\d+\.\d+\.\d+", normalized_version):
        return semantic_version.Version(normalized_version)

    # now we're guaranteed to only have one (X) or two (X.Y) component versions
    # X -> X.0.0
    # X.Y -> X.Y.0
    if "." not in normalized_version:
        normalized_version = version + ".0.0"
    else:
        normalized_version = version + ".0"

    return semantic_version.Version(normalized_version)


def parse_ida_version_spec(version: str) -> semantic_version.SimpleSpec:
    normalized_version = version.replace("sp", ".")
    return semantic_version.SimpleSpec(normalized_version)


def split_plugin_version_spec(version_spec: str) -> tuple[str, str]:
    """Split a plugin version spec into plugin name and version.

    Args:
        version_spec: Plugin version specification like "plugin>=1.0.0"

    Returns:
        Tuple of (plugin_name, plugin_version)

    Raises:
        ValueError: If the version spec format is invalid
    """
    plugin_name = re.split("[=><!~]", version_spec)[0]
    if plugin_name == version_spec:
        return plugin_name, ""

    op_chars = version_spec[len(plugin_name) : len(plugin_name) + 2]
    if not op_chars or op_chars[0] not in "=><!~":
        raise ValueError(f"invalid plugin version spec: {version_spec}")

    if len(op_chars) < 2 or op_chars[1] != "=":
        raise ValueError(f"invalid plugin version spec: {version_spec}")

    plugin_version = version_spec[len(plugin_name) + 2 :]
    _ = parse_plugin_version(plugin_version)

    return (plugin_name, plugin_version)


class Contact(BaseModel):
    name: str | None = None
    email: str | None = None

    @model_validator(mode="after")
    def check_at_least_one(self):
        if not self.name and not self.email:
            raise ValueError("name or email must be present")
        return self


class URLs(BaseModel):
    # URL of GitHub repository containing the source code for the plugin.
    # Uses the form: https://github.com/org/project
    repository: str

    # URL of website describing the plugin, if different from the GitHub repo.
    homepage: str | None = None

    @field_validator("repository", mode="after")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        github_pattern = r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/?$"
        if not re.match(github_pattern, v):
            raise ValueError("Repository must be a valid GitHub URL in the format: https://github.com/org/project")
        return v


class PluginMetadata(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, extra="allow")  # type: ignore

    ###########################################################################
    # required

    # The name will be used to identify the plugin.
    #
    # The project name must consist of ASCII letters, digits, underscores "_", hyphens "-".
    # It must not start or end with an underscore or hyphen.
    #
    # Two plugins with the same name cannot be installed at the same time;
    # therefore, this should be globally unique.
    #
    # This is used by IDA Pro when loading the plugin to derived a namespaced identifier.
    # The namespace name is generated by converting all non alphanumeric characters
    # of the plugin name to underscores (_) and prepending __plugins__ to it.
    # For example "my plugin" would become __plugins__my_plugin.
    name: str

    # Specify the version of your plugin. It must follow the x.y.z format (e.g., 1.0.0).
    # Do not include a leading "v".
    #
    # Examples:
    #   - "1.0.0"
    version: str

    # The filename of the "main" file for the plugin.
    # It should be stored in the same directory as its ida-plugin.json file.
    # If the entryPoint has no file extension,
    #  IDA will assume it is a native plugin and append the appropriate file extension
    #  for dynamic shared objects for the host platform (.dll, .so, .dylib).
    # For IDAPython plugins, this should typically be a .py file (e.g., my-first-plugin.py).
    entry_point: str = Field(alias="entryPoint")

    # A list of URLs associated with your project.
    urls: URLs

    ###########################################################################
    # optional

    # This should be a one-line description of your project,
    # to show as the “headline” of your project page on the plugin repository,
    # and other places such as lists of search results.
    description: str | None = None

    # Used to improve discoverability in the plugin repository by providing major categories.
    categories: list[
        Literal["disassembly-and-processor-modules"]
        | Literal["file-parsers-and-loaders"]
        | Literal["decompilation"]
        | Literal["debugging-and-tracing"]
        | Literal["deobfuscation"]
        | Literal["collaboration-and-productivity"]
        | Literal["integration-with-third-parties-interoperability"]
        | Literal["api-scripting-and-automation"]
        | Literal["ui-ux-and-visualization"]
        | Literal["malware-analysis"]
        | Literal["vulnerability-research-and-exploit-development"]
        | Literal["other"]
    ] = Field(default_factory=list)

    # Used to improve discoverability in the plugin repository by providing search terms.
    keywords: list[str] = Field(default_factory=list)

    # License for the plugin.
    # Examples:
    #   - "Apache 2.0"
    #   - "MIT"
    #   - "BSD 3-Clause"
    license: str | None = None

    # Both of these fields contain lists of people identified by a name and/or an email address.
    # There must be at least one author or maintainer provided for each plugin.
    authors: list[Contact] = Field(default_factory=list)
    maintainers: list[Contact] = Field(default_factory=list)

    # Declare which versions of IDA your plugin supports.
    # You must declare each version separately, because IDA's APIs don't clearly follow semantic versioning.
    # The default is all versions, but this is almost certainly incorrect!
    ida_versions: list[IdaVersion] = Field(alias="idaVersions", default_factory=lambda: list(sorted(ALL_IDA_VERSIONS)))

    # Declare which platforms your plugin supports.
    # The default is all versions, which is likely for cross-platform Python plugins.
    # Native plugins should declare the platform consistent with the .dll/.so/.dylib file in the archive.
    platforms: list[Platform] = Field(default_factory=lambda: list(sorted(ALL_PLATFORMS)))

    # Include an image to visually represent your plugin on its page at plugins.hex-rays.com.
    # This should be a relative path to an image file within your plugin’s repository.
    # The recommended aspect ratio for the image is 16:9.
    logo_path: str | None = Field(alias="logoPath", default=None)

    # If your project has dependencies, list them like this:
    # pythonDependencies = [
    #   "httpx",
    #   "gidgethub[httpx]>4.0.0",
    #   "pkg3>=1.0,<=2.0",
    # ]
    # The dependency syntax is intended to be used by pip.
    python_dependencies: list[str] | str = Field(alias="pythonDependencies", default_factory=list)

    @field_validator("name", mode="after")
    @classmethod
    def is_ok_name(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Name must consist of ASCII letters, digits, underscores, and hyphens only")

        if v.startswith(("_", "-")) or v.endswith(("_", "-")):
            raise ValueError("Name must not start or end with underscore or hyphen")

        return v

    @field_validator("version", mode="after")
    @classmethod
    def is_ok_version(cls, value: str) -> str:
        try:
            _ = parse_plugin_version(value)
        except Exception as e:
            raise ValueError(f"failed to parse version: {e}") from e
        return value

    @field_validator("ida_versions", mode="before")
    @classmethod
    def transform_ida_version_spec_to_versions(cls, raw: str | list[IdaVersion]) -> list[IdaVersion]:
        if isinstance(raw, str):
            spec = parse_ida_version_spec(raw)

            versions: list[IdaVersion] = []
            for version in ALL_IDA_VERSIONS:
                if parse_ida_version(version) in spec:
                    versions.append(version)
            return versions
        else:
            return raw

    @model_validator(mode="after")
    def check_at_least_one_contact(self):
        if not self.authors and not self.maintainers:
            raise ValueError("authors or maintainers must be present")
        return self

    @field_serializer("ida_versions")
    def serialize_sorted_ida_versions(self, versions: list[IdaVersion]):
        return sorted(versions, key=parse_ida_version)

    @field_serializer("platforms")
    def serialize_sorted_platforms(self, platforms: list[Platform]):
        return sorted(platforms)

    @property
    def host(self) -> str:
        """fetch the canonical host for the plugin.

        Today this is the (GitHub) repository URL.
        In the future, it may alternatively be another URL scheme, like HTTP site.
        Then we expect a guarantee like urls.repository xor urls.website xor urls.foo
         and this routine fetches the canonical URL.
        """
        return self.urls.repository


class IDAMetadataDescriptor(BaseModel):
    """Metadata from ida-plugin.json"""

    model_config = ConfigDict(serialize_by_alias=True)  # type: ignore

    schema_: str | None = Field(alias="$schema", default=None, exclude=True)
    metadata_version: Literal[1] = Field(alias="IDAMetadataDescriptorVersion")  # must be 2
    plugin: PluginMetadata


class MinimalIDAPluginMetadata(BaseModel):
    """Minimal set of IDA Plugin metadata from ida-plugin.json

    This covers plugins that have an ida-plugin.json that written
     before hcli and its plugin manager.
    So really the only required field was "name"
    """

    model_config = ConfigDict(extra="allow")

    class MinimalPluginMetadata(BaseModel):
        model_config = ConfigDict(extra="allow")
        name: str
        version: str | None = None

    # IDAMetadataDescriptorVersion=1 is expected by the IDA Pro plugin loader.
    # So we can't bump it without coordination.
    metadata_version: Literal[1] = Field(validation_alias="IDAMetadataDescriptorVersion")
    plugin: "MinimalIDAPluginMetadata.MinimalPluginMetadata"

    def guess_hcli_plugin_version(self):
        """
        legacy vs incompatible vs ...

        Versions:
        - 1: initial ida-plugin.json schema and format (2025-xx)
        - 2: version required, pythonDependencies added (2025-08)
        - 3: urls.repository required (2025-09-12)
        """
        # TODO: i'm not sure if we want this.
        if "urls" in (self.plugin.__pydantic_extra__ or {}):
            return 3

        if "version" in (self.plugin.__pydantic_extra__ or {}):
            return 2

        return 1


def parse_pep723_metadata(python_file_content: str) -> list[str]:
    """Parse PEP 723 inline script metadata from Python file content.

    Returns a list of dependencies found in the PEP 723 metadata block,
    or an empty list if no metadata block is found.

    Raises:
        ValueError: If metadata block is found but contains invalid TOML or unexpected data format
    """
    pattern = r"#\s*///\s*script\s*\n(.*?)#\s*///\s*\n"
    match = re.search(pattern, python_file_content, re.DOTALL | re.MULTILINE)

    if not match:
        return []

    metadata_block = match.group(1)

    lines = []
    for line in metadata_block.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        if line:
            lines.append(line)

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
        # zip files always use forward slashes
        zip_path = file_path.as_posix()
        with zip_file.open(zip_path) as f:
            return f.read()


def get_python_dependencies_from_plugin_archive(zip_data: bytes, metadata: IDAMetadataDescriptor) -> list[str]:
    """Get Python dependencies from a plugin archive.

    If pythonDependencies is "inline", parse PEP 723 metadata from the entry point.
    Otherwise, return the pythonDependencies list directly.

    Raises:
        ValueError: If entry point is not a Python file for inline dependencies,
                   if file is not found, or if dependencies format is unexpected
    """
    if isinstance(metadata.plugin.python_dependencies, str) and metadata.plugin.python_dependencies == "inline":
        if not metadata.plugin.entry_point.endswith(".py"):
            raise ValueError("Entry point must be a Python file (.py) for inline dependencies")

        python_content_bytes = get_file_content_from_plugin_archive(
            zip_data, metadata.plugin.name, metadata.plugin.entry_point
        )
        python_content = python_content_bytes.decode("utf-8")
        return parse_pep723_metadata(python_content)
    else:
        if isinstance(metadata.plugin.python_dependencies, list):
            return metadata.plugin.python_dependencies
        else:
            raise ValueError(
                f"Unexpected python_dependencies type: {type(metadata.plugin.python_dependencies).__name__}"
            )


def get_python_dependencies_from_plugin_directory(plugin_path: Path, metadata: IDAMetadataDescriptor) -> list[str]:
    """Get Python dependencies from a plugin directory.

    If pythonDependencies is "inline", parse PEP 723 metadata from the entry point.
    Otherwise, return the pythonDependencies list directly.

    Raises:
        ValueError: If entry point is not a Python file for inline dependencies,
                   if file is not found, or if dependencies format is unexpected
    """
    if isinstance(metadata.plugin.python_dependencies, str) and metadata.plugin.python_dependencies == "inline":
        if not metadata.plugin.entry_point.endswith(".py"):
            raise ValueError("Entry point must be a Python file (.py) for inline dependencies")

        entry_point_path = plugin_path / metadata.plugin.entry_point

        python_content = entry_point_path.read_text(encoding="utf-8")
        return parse_pep723_metadata(python_content)
    else:
        if isinstance(metadata.plugin.python_dependencies, list):
            return metadata.plugin.python_dependencies
        else:
            raise ValueError(
                f"Unexpected python_dependencies type: {type(metadata.plugin.python_dependencies).__name__}"
            )


def get_metadatas_with_paths_from_plugin_archive(
    zip_data: bytes,
) -> Iterator[tuple[Path, IDAMetadataDescriptor]]:
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        for file_path in zip_file.namelist():
            if not file_path.endswith("ida-plugin.json"):
                continue

            logger.debug("found metadata path: %s", file_path)
            with zip_file.open(file_path) as f:
                try:
                    metadata = IDAMetadataDescriptor.model_validate_json(f.read().decode("utf-8"))
                except (ValueError, ValidationError) as e:
                    logger.debug("failed to validate: %s", e)
                    continue
                else:
                    yield Path(file_path), metadata


def get_metadata_path_from_plugin_archive(zip_data: bytes, name: str) -> Path:
    for path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.plugin.name == name:
            return path

    raise ValueError(f"plugin '{name}' not found in zip archive")


def get_metadata_from_plugin_archive(zip_data: bytes, name: str) -> IDAMetadataDescriptor:
    """Extract ida-plugin.json metadata for plugin with the given name from zip archive without extracting"""

    for _path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.plugin.name == name:
            return metadata

    raise ValueError(f"plugin '{name}' not found in zip archive")


def does_path_exist_in_zip_archive(zip_data: bytes, path: str) -> bool:
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        return path in zip_file.namelist()


def does_plugin_path_exist_in_plugin_archive(zip_data: bytes, plugin_name: str, relative_path: str) -> bool:
    """does the given path exist relative to the metadata file of the given plugin?"""
    metadata_path = get_metadata_path_from_plugin_archive(zip_data, plugin_name)
    plugin_root_path = Path(metadata_path).parent
    candidate_path = plugin_root_path / Path(relative_path)
    # zip files always use forward slashes
    return does_path_exist_in_zip_archive(zip_data, candidate_path.as_posix())


def is_ida_version_compatible(current_version: str, compatible_versions: Iterable[str]) -> bool:
    """Check if current IDA version is compatible with the given versions."""
    return current_version in compatible_versions


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


def validate_metadata_in_plugin_archive(zip_data: bytes, metadata: IDAMetadataDescriptor):
    """validate the `ida-plugin.json` metadata within the given plugin archive.

    The following things must be checked:
    - the following paths must contain relative paths, no paths like ".." or similar escapes:
      - entry point
      - logo path
    - the file paths must exist in the archive:
      - entry point
      - logo path
    """
    name = metadata.plugin.name

    validate_path(metadata.plugin.entry_point, "entry point")
    if metadata.plugin.logo_path:
        validate_path(metadata.plugin.logo_path, "logo path")

    if metadata.plugin.entry_point.endswith(".py"):
        if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.entry_point):
            logger.debug("Missing python entry point file: %s", metadata.plugin.entry_point)
            raise ValueError(f"Entry point file not found in archive: '{metadata.plugin.entry_point}'")
    else:
        # binary plugin
        has_bare_name = False
        for ext in (".so", ".dll", ".dylib"):
            if does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.entry_point + ext):
                has_bare_name = True

        if has_bare_name:
            extensions = [
                (".so", PLATFORM_LINUX),
                (".dll", PLATFORM_WINDOWS),
                (".dylib", PLATFORM_MACOS_ARM),
                (".dylib", PLATFORM_MACOS_INTEL),
            ]
            for ext, platform in extensions:
                if platform in metadata.plugin.platforms:
                    if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.entry_point + ext):
                        raise ValueError("missing native entry point: %s", metadata.plugin.entry_point + ext)

        else:
            if set(metadata.plugin.platforms) == {PLATFORM_MACOS_ARM, PLATFORM_MACOS_INTEL}:
                if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.entry_point):
                    raise ValueError("missing native entry point: %s", metadata.plugin.entry_point)
            elif len(set(metadata.plugin.platforms)) == 1:
                if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.entry_point):
                    raise ValueError("missing native entry point: %s", metadata.plugin.entry_point)
            else:
                raise ValueError("plugin declares multiple platforms for single native entry point")

        if not has_bare_name:
            logger.debug("Missing native entry point file: %s", metadata.plugin.entry_point)
            raise ValueError(f"Binary plugin file not found in archive: '{metadata.plugin.entry_point}'")

    if metadata.plugin.logo_path:
        if not does_plugin_path_exist_in_plugin_archive(zip_data, name, metadata.plugin.logo_path):
            logger.debug("Missing logo file: %s", metadata.plugin.logo_path)
            raise ValueError(f"Logo file not found in archive: '{metadata.plugin.logo_path}'")


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

        return metadata.plugin.entry_point.endswith(".py")
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
        entry_point = metadata.plugin.entry_point

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
