import functools
import io
import logging
import pathlib
import re
import sys
import typing
import zipfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path, PurePath
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import semantic_version
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from hcli.lib.util.logging import m

logger = logging.getLogger(__name__)


class ChoiceValueError(ValueError):
    """Error raised when a setting value doesn't match available choices.

    Args:
        key: the setting key
        value: the invalid value provided
        choices: the tuple of valid choices
    """

    def __init__(self, key: str, value: str, choices: tuple[str, ...]):
        self.key = key
        self.value = value
        self.choices = choices
        choices_str = ", ".join(choices)
        super().__init__(f"failed to validate setting value: {key}: '{value}' (must be one of: {choices_str})")


PLATFORM_WINDOWS = "windows-x86_64"
PLATFORM_WINDOWS_ARM = "windows-aarch64"
PLATFORM_LINUX = "linux-x86_64"
PLATFORM_LINUX_ARM = "linux-aarch64"
PLATFORM_MACOS_INTEL = "macos-x86_64"
PLATFORM_MACOS_ARM = "macos-aarch64"

Platform = Literal[
    "windows-x86_64",
    "windows-aarch64",
    "linux-x86_64",
    "linux-aarch64",
    "macos-x86_64",
    "macos-aarch64",
]

ALL_PLATFORMS: frozenset[Platform] = frozenset(typing.get_args(Platform))

# Released IDA versions, newest first. hcli matches the `idaVersions` spec against the
# running IDA version, so existing specs match a new IDA release before it is added here.
# hcli uses this list to pick the "!=X.Y" exclusions when it converts a deprecated list of
# versions to a spec, and to display and lint specs.
KNOWN_IDA_VERSIONS: tuple[str, ...] = (
    "9.5",  #    2026-10
    "9.4",  #    2026-07
    "9.3",  #    2026-02
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
)

IDA_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)(?:sp\d+)?$")

ALL_IDA_VERSIONS_SPEC = "*"

IdaVersionsForm = Literal["omitted", "spec", "list"]


def parse_plugin_version(version: str) -> semantic_version.Version:
    # Use Version.coerce() which automatically normalizes partial versions
    # (e.g., "1.2" -> "1.2.0", "1" -> "1.0.0") and handles leading zeros
    # (e.g., "2025.09.24" -> "2025.9.24").
    return semantic_version.Version.coerce(version)


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


@functools.lru_cache(maxsize=1024)
def parse_ida_version_spec(version: str) -> semantic_version.SimpleSpec:
    normalized_version = version.replace("sp", ".")
    return semantic_version.SimpleSpec(normalized_version)


def get_ida_major_minor(version: str) -> str:
    """Return the "X.Y" part of an IDA version such as "9.0" or "9.0sp1".

    Raises:
        ValueError: when the version does not look like "X.Y" or "X.YspN".
    """
    match = IDA_VERSION_PATTERN.match(version)
    if not match:
        raise ValueError(f"invalid IDA version: '{version}' (expected X.Y or X.YspN)")
    return f"{match.group(1)}.{match.group(2)}"


def get_ida_version_spec_from_list(versions: Sequence[str]) -> str:
    """Convert an explicit list of IDA versions into an equivalent spec string.

    hcli detects the running IDA version as "X.Y", so service packs collapse into their
    release. Known releases between the lowest and highest entry that the list leaves
    out become "!=X.Y" exclusions. An empty list means all versions.

    Raises:
        ValueError: when an entry does not look like "X.Y" or "X.YspN".
    """
    if not versions:
        return ALL_IDA_VERSIONS_SPEC

    declared = sorted({get_ida_major_minor(version) for version in versions}, key=parse_ida_version)
    if len(declared) == 1:
        return f"=={declared[0]}"

    lowest = parse_ida_version(declared[0])
    highest = parse_ida_version(declared[-1])
    known = {get_ida_major_minor(version) for version in KNOWN_IDA_VERSIONS}
    missing = sorted(
        (version for version in known - set(declared) if lowest < parse_ida_version(version) < highest),
        key=parse_ida_version,
    )
    return ",".join([f">={declared[0]}", f"<={declared[-1]}", *(f"!={version}" for version in missing)])


def get_ida_versions_matching_spec(spec: str) -> list[str]:
    """Return the known IDA versions that the spec matches, oldest first."""
    parsed = parse_ida_version_spec(spec)
    return sorted(
        (version for version in KNOWN_IDA_VERSIONS if parse_ida_version(version) in parsed), key=parse_ida_version
    )


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
    email: str = Field(description="Contact email address.")
    name: str | None = Field(default=None, description="Contact name.")


# Together with the plugin name, this URL IS the plugin's identity, so the
# namespace has to stay closed: a public plugin is identified by its GitHub
# repository, a private one by its page on the Hex-Rays plugin portal.
GITHUB_REPOSITORY_PATTERN = r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/?$"

# The portal's canonical shape is three segments, <org>/<repo>/<name>, which the
# server enforces. hcli also accepts two because production predates that rule
# and already contains <org>/<repo> and <org>/<name> identities -- installed on
# user machines, and still served until those plugins are republished.
# Rejecting them here would break status and upgrade for exactly the users who
# already have private plugins.
PORTAL_REPOSITORY_PATTERN = r"^https://plugins\.hex-rays\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)?/?$"


def is_plugin_repository_url(value: str) -> bool:
    """Whether this URL may identify a plugin (GitHub repo, or portal page)."""
    return bool(re.match(GITHUB_REPOSITORY_PATTERN, value) or re.match(PORTAL_REPOSITORY_PATTERN, value))


class URLs(BaseModel):
    repository: str = Field(
        description=(
            "Canonical URL identifying the plugin: the GitHub repository containing its source code, "
            "or, for plugins distributed through the Hex-Rays portal, its page there."
        ),
        examples=[
            "https://github.com/org/project",
            "https://plugins.hex-rays.com/org/repo/name",
        ],
    )

    homepage: str | None = Field(
        default=None,
        description="URL of a website describing the plugin, if different from the repository.",
    )

    @field_validator("repository", mode="after")
    @classmethod
    def validate_repository_url(cls, v: str) -> str:
        if not is_plugin_repository_url(v):
            raise ValueError(
                "Repository must be a GitHub URL (https://github.com/org/project) "
                "or a Hex-Rays plugin page (https://plugins.hex-rays.com/org/repo/name)"
            )
        return v


class PluginSettingDescriptor(BaseModel):
    key: str = Field(
        description="Unique code-level identifier for the setting.",
        examples=["openai_api_key"],
    )

    type: Literal["string", "boolean"] = Field(description="Setting value type.")

    required: bool = Field(description="Whether the user must supply a value for this setting.")

    default: str | bool | None = Field(
        default=None,
        description=(
            "Default value used when no other config provides one. "
            "Written into `ida-config.json` only when the user selects it, such as at an install prompt; "
            "otherwise provided on-demand."
        ),
    )

    name: str = Field(
        description="Human-readable name for the setting.",
        examples=["OpenAI API key"],
    )

    documentation: str | None = Field(
        default=None,
        description="Human-readable explanation for the setting.",
        examples=["OpenAI API key acquired from https://platform.openai.com/api-keys"],
    )

    validation_pattern: str | None = Field(
        default=None,
        description=(
            "Regular expression used to validate candidate values. "
            "Only used for string types. Mutually exclusive with `choices`."
        ),
        examples=["[a-z]{32}"],
    )

    choices: tuple[str, ...] | None = Field(
        default=None,
        description=(
            "Acceptable string values. Only used for string types. Mutually exclusive with `validation_pattern`."
        ),
        examples=[["option-a", "option-b", "option-c"]],
    )

    prompt: bool = Field(
        default=True,
        description=(
            "Whether to prompt the user for this setting during installation. "
            "Set to false to use the default value without prompting."
        ),
    )

    secret: bool = Field(
        default=False,
        description=(
            "Whether the setting contains a secret value such as an API token. "
            "When true, the input is masked during prompting and the value is "
            "redacted in `config list` output."
        ),
    )

    @field_validator("choices", mode="before")
    @classmethod
    def validate_choices_not_empty(cls, v: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
        if v is None:
            return None
        if isinstance(v, list):
            v = tuple(v)
        if len(v) == 0:
            raise ValueError("choices must not be empty")
        return v

    @model_validator(mode="after")
    def validate_constraints(self):
        if self.type == "boolean":
            if self.validation_pattern is not None:
                raise ValueError(f"validation_pattern is only supported for string settings, not boolean: {self.key}")
            if self.choices is not None:
                raise ValueError(f"choices is only supported for string settings, not boolean: {self.key}")
            if self.secret:
                raise ValueError(f"secret is only supported for string settings, not boolean: {self.key}")

        if self.validation_pattern is not None and self.choices is not None:
            raise ValueError(f"validation_pattern and choices are mutually exclusive: {self.key}")

        if not self.prompt and self.default is None:
            raise ValueError(f"prompt=False requires a default value: {self.key}")

        return self

    def validate_value(self, candidate_value: str | bool) -> None:
        if self.type == "boolean":
            if not isinstance(candidate_value, bool):
                raise ValueError(f"failed to validate setting value: {self.key}: '{candidate_value}'")
        elif self.type == "string":
            if not isinstance(candidate_value, str):
                raise ValueError(f"failed to validate setting value: {self.key}: '{candidate_value}'")
            if self.validation_pattern and not re.match(self.validation_pattern, candidate_value):
                raise ValueError(f"failed to validate setting value: {self.key}: '{candidate_value}'")
            if self.choices and candidate_value not in self.choices:
                raise ChoiceValueError(self.key, candidate_value, self.choices)


def is_valid_plugin_name(name: str) -> bool:
    return (
        bool(re.match(r"^[a-zA-Z0-9_-]+$", name)) and not name.startswith(("_", "-")) and not name.endswith(("_", "-"))
    )


IDAUSR_TOKEN = "$IDAUSR"
CONFIG_TEMPLATE_ROOT = "cfg"
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
# Base names, in lower case, of the files and directories in `$IDADIR/cfg` of IDA 9.0 to 9.5.
IDA_CONFIG_NAMES = frozenset(
    [
        "32bit-avx",
        "32bit-core",
        "32bit-linux",
        "32bit-sse",
        "64bit-avx",
        "64bit-avx512",
        "64bit-core",
        "64bit-linux",
        "64bit-segments",
        "64bit-sse",
        "6805",
        "6808",
        "6811",
        "6812",
        "6816",
        "78k0",
        "78k0s",
        "aarch64",
        "aarch64-core",
        "aarch64-fpu",
        "ad218x",
        "alpha",
        "amd64-avx",
        "arc",
        "arm-core",
        "arm-m-profile",
        "arm-ocd-banked",
        "arm-vfpv3",
        "arm-with-banked",
        "arm-with-m",
        "arm-with-neon",
        "arm_sys_reg",
        "atrap",
        "avr",
        "baltic",
        "bochsrc",
        "c166",
        "c166v1",
        "c166v2",
        "central_europe",
        "chinese",
        "clsid",
        "dbg_bochs",
        "dbg_dalvik",
        "dbg_gdb",
        "dbg_ios",
        "dbg_pin",
        "dbg_xnu",
        "dex",
        "dscu",
        "dsp561xx",
        "dsp563xx",
        "dsp566xx",
        "dsp56k",
        "dwarf",
        "eh34",
        "exceptions",
        "f2mc16l",
        "f2mc16lx",
        "fr",
        "gdb_arch",
        "ghidra_patterns",
        "golang",
        "goodname",
        "goomba",
        "greek",
        "h8",
        "hcs12",
        "hcs12x",
        "hexrays",
        "hpux",
        "i386-avx",
        "i51",
        "i960",
        "ida",
        "idaclang",
        "idagui",
        "idapython",
        "idatui",
        "ios-arm",
        "ios-arm64",
        "japanese",
        "korean",
        "kr1878",
        "latin_1",
        "m16c60",
        "m16c80",
        "m32c80",
        "m32r",
        "m65816",
        "m68k",
        "m68k-core",
        "m740",
        "m7700",
        "m7900",
        "macho",
        "mc68kvec",
        "mips-cp0",
        "mips-cpu",
        "mips-dsp",
        "mips-fpu",
        "mips-linux",
        "mips64-cp0",
        "mips64-cpu",
        "mips64-fpu",
        "mips64-linux",
        "n64",
        "noret",
        "oakdsp",
        "objc",
        "patfind",
        "pdb",
        "pe",
        "pic12",
        "pic14",
        "pic16",
        "pic24",
        "pic30",
        "pic33",
        "power-altivec",
        "power-core",
        "power-fpu",
        "power-linux",
        "power64-core",
        "powerpc-32",
        "powerpc-64",
        "powerpc-altivec64",
        "ppc",
        "psx",
        "r32c",
        "rh850",
        "rh850-core",
        "riscv",
        "riscv-32",
        "riscv-32bit-cpu",
        "riscv-64",
        "riscv-64bit-cpu",
        "rl78",
        "rust",
        "sh3",
        "spc700",
        "st10",
        "st20",
        "st7",
        "st9",
        "super10",
        "swift",
        "tms32028",
        "tms320c3",
        "tms320c54",
        "tms320c55",
        "tricore",
        "tricore-core",
        "turkish",
        "uefi",
        "uefi_guids",
        "vietnam",
        "xbe",
        "xnu-arm64",
        "xnu-x64",
        "xref_graph",
        "z180",
        "z8",
    ]
)
IDA_CONFIG_DIRECTORY_NAMES = frozenset({"ghidra_patterns"})
WINDOWS_INVALID_PATH_CHARACTERS = frozenset('<>:"|?*\\')


def validate_config_template_destination_component(component: str, destination: str) -> None:
    """Check one path component of a config template destination.

    Raises:
        ValueError: when the component is empty, a traversal, not ASCII, or not valid on Windows.
    """
    if component == "":
        raise ValueError(f"destination must not contain empty path components: '{destination}'")
    if component in (".", ".."):
        raise ValueError(f"destination must not contain '.' or '..': '{destination}'")
    if not component.isascii():
        raise ValueError(f"destination must contain only ASCII characters: '{destination}'")
    if "\\" in component:
        raise ValueError(f"destination must use '/' as the path separator: '{destination}'")
    if any(c in WINDOWS_INVALID_PATH_CHARACTERS or ord(c) < 0x20 or ord(c) == 0x7F for c in component):
        raise ValueError(f"destination contains a character that is not valid in a path: '{destination}'")
    if component.endswith((".", " ")):
        raise ValueError(f"destination path components must not end with '.' or ' ': '{destination}'")
    if component.split(".", maxsplit=1)[0].rstrip(" ").upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"destination contains a name reserved on Windows: '{component}' in '{destination}'")


def get_config_template_destination_parts(destination: str) -> tuple[str, ...]:
    """Validate a config template destination and get its path components below `$IDAUSR`.

    The destination is `$IDAUSR/cfg/<owner>.<suffix>` or `$IDAUSR/cfg/<owner>/<path>`,
    where `<owner>` has the syntax of a plugin name and is not the name of a file or directory
    that IDA ships in `$IDADIR/cfg`, because a copy in `$IDAUSR/cfg` hides the file of IDA.
    Whether `<owner>` names the declaring plugin is checked by `validate_config_template_owners`.

    Raises:
        ValueError: when the destination breaks a rule; the message names the rule.
    """
    prefix = IDAUSR_TOKEN + "/"
    if not destination.startswith(prefix):
        raise ValueError(f"destination must start with '{prefix}': '{destination}'")

    relative = destination[len(prefix) :]
    if relative.endswith("/"):
        raise ValueError(f"destination must not end with '/': '{destination}'")

    parts = tuple(relative.split("/"))
    for part in parts:
        validate_config_template_destination_component(part, destination)

    root = parts[0]
    if root != CONFIG_TEMPLATE_ROOT:
        raise ValueError(f"destination must be under {prefix}{CONFIG_TEMPLATE_ROOT}/: '{destination}'")

    if len(parts) < 2:
        raise ValueError(f"destination must name a file below {prefix}{root}/: '{destination}'")

    if len(parts) == 2:
        owner, _, suffix = parts[1].partition(".")
        if not suffix:
            raise ValueError(
                f"a destination directly in {prefix}{root}/ must be named '<plugin>.<suffix>': '{destination}'"
            )
    else:
        owner = parts[1]

    if not is_valid_plugin_name(owner):
        raise ValueError(
            f"destination must start with {prefix}{root}/<plugin>/ or {prefix}{root}/<plugin>.<suffix>, "
            f"where <plugin> is a plugin name: '{destination}'"
        )

    reserved = IDA_CONFIG_NAMES if len(parts) == 2 else IDA_CONFIG_DIRECTORY_NAMES
    if owner.lower() in reserved:
        raise ValueError(f"destination must not use the name of a config file that IDA ships: '{destination}'")

    return parts


class ConfigTemplate(BaseModel):
    source: str = Field(
        description=(
            "Path of the template file, relative to this `ida-plugin.json`. The file must be in the plugin archive."
        ),
        examples=["defaults/my-plugin.cfg"],
    )

    destination: str = Field(
        description=(
            "Path that HCLI copies the template to. It starts with the literal `$IDAUSR/cfg/`, "
            "followed by `<plugin>.<suffix>` or `<plugin>/<path>`, where `<plugin>` is this plugin's name "
            "or, for a component, the name of its suite. "
            "It must be ASCII, use `/` as the separator, and contain no `.` or `..` components. "
            "`<plugin>` must not be the name of a file or directory that IDA ships in `$IDADIR/cfg`."
        ),
        examples=["$IDAUSR/cfg/my-plugin.cfg", "$IDAUSR/cfg/my-plugin/patterns.txt"],
    )

    @field_validator("source", mode="after")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if not value:
            raise ValueError("source must not be empty")
        validate_path(value, "config template source")
        return value

    @field_validator("destination", mode="after")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        _ = get_config_template_destination_parts(value)
        return value

    @property
    def destination_parts(self) -> tuple[str, ...]:
        """Path components of the destination below `$IDAUSR`."""
        return get_config_template_destination_parts(self.destination)

    @property
    def owner(self) -> str:
        """Plugin name that the destination belongs to."""
        return self.destination_parts[1].partition(".")[0]


def set_ida_versions_json_schema(schema: dict[str, typing.Any]) -> None:
    """Document the spec string, and keep the deprecated list form valid."""
    del schema["type"]
    schema["anyOf"] = [
        {"type": "string"},
        {"type": "array", "items": {"type": "string"}, "deprecated": True},
    ]


class PluginMetadata(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True, extra="allow")  # type: ignore

    _ida_versions_form: IdaVersionsForm = PrivateAttr(default="omitted")

    # required fields
    name: str = Field(
        description=(
            "Identifier for the plugin. Must consist of ASCII letters, digits, "
            "underscores, and hyphens, and must not start or end with an underscore "
            "or hyphen. Two plugins with the same name cannot be installed at the "
            "same time, so this should be globally unique. IDA Pro derives a "
            "namespaced identifier by converting all non-alphanumeric characters to "
            "underscores and prepending `__plugins__` (e.g. `my plugin` becomes "
            "`__plugins__my_plugin`)."
        ),
        examples=["my-plugin"],
    )

    version: str = Field(
        description="Plugin version in `x.y.z` semver format. Do not include a leading `v`.",
        examples=["1.0.0"],
    )

    entry_point: str = Field(
        alias="entryPoint",
        description=(
            "Filename of the plugin's main file, relative to this `ida-plugin.json`. "
            "If the value has no file extension, IDA assumes a native plugin and "
            "appends the host platform's dynamic shared object extension "
            "(`.dll`, `.so`, `.dylib`). For IDAPython plugins, this should be a "
            "`.py` file."
        ),
        examples=["my-first-plugin.py"],
    )

    urls: URLs = Field(description="URLs associated with the plugin.")

    # optional fields
    description: str | None = Field(
        default=None,
        description=(
            "One-line description of the plugin, shown as the headline of its page "
            "on the plugin repository and in lists of search results."
        ),
    )

    categories: list[
        Literal[
            "disassembly-and-processor-modules",
            "file-parsers-and-loaders",
            "decompilation",
            "debugging-and-tracing",
            "deobfuscation",
            "collaboration-and-productivity",
            "integration-with-third-parties-interoperability",
            "api-scripting-and-automation",
            "ui-ux-and-visualization",
            "malware-analysis",
            "vulnerability-research-and-exploit-development",
            "other",
        ]
    ] = Field(
        default_factory=list,
        description="Major categories used to improve discoverability in the plugin repository.",
    )

    keywords: list[str] = Field(
        default_factory=list,
        description="Search terms used to improve discoverability in the plugin repository.",
    )

    license: str | None = Field(
        default=None,
        description="License for the plugin.",
        examples=["MIT", "Apache 2.0", "BSD 3-Clause"],
    )

    authors: list[Contact] = Field(
        default_factory=list,
        description="Authors of the plugin. At least one author or maintainer is required.",
    )
    maintainers: list[Contact] = Field(
        default_factory=list,
        description="Maintainers of the plugin. At least one author or maintainer is required.",
    )

    ida_version_spec: str = Field(
        alias="idaVersions",
        default=ALL_IDA_VERSIONS_SPEC,
        description=(
            'Versions of IDA Pro the plugin supports, as a version spec such as ">=9.0" or '
            '">=9.0,<10,!=9.3". Comma-separated clauses must all match. A spec without an upper '
            "bound also matches later IDA releases. HCLI does not distinguish service packs: "
            '"9.0" also matches 9.0 SP1. The default is all versions, but this is almost certainly '
            "incorrect. A list of versions is deprecated; HCLI converts it to the equivalent spec."
        ),
        examples=[">=9.0", ">=9.0,<10", "==9.2"],
        json_schema_extra=set_ida_versions_json_schema,
    )

    platforms: list[Platform] = Field(
        default_factory=lambda: sorted(ALL_PLATFORMS),
        description=(
            "Platforms the plugin supports. The default is all platforms, which is "
            "appropriate for cross-platform Python plugins. Native plugins should "
            "declare the platform consistent with the `.dll`/`.so`/`.dylib` file in "
            "the archive."
        ),
    )

    logo_path: str | None = Field(
        alias="logoPath",
        default=None,
        description=(
            "Relative path within the repository to an image representing the "
            "plugin on `plugins.hex-rays.com`. Recommended aspect ratio is 16:9."
        ),
    )

    requires_python: str | None = Field(
        alias="requiresPython",
        default=None,
        description=(
            "Python version requirement for the IDA Python environment, expressed as a PEP 440 version specifier. "
            "HCLI skips plugin versions whose requirement excludes the target Python version when it selects a "
            "version, and checks it again before installing the plugin."
        ),
        examples=[">=3.11"],
    )

    python_dependencies: list[str] | str = Field(
        alias="pythonDependencies",
        default_factory=list,
        description=(
            "List of pip-compatible dependency specifications, or the literal "
            "string `inline` to read PEP 723 metadata from the entry point."
        ),
        examples=[["httpx", "gidgethub[httpx]>4.0.0", "pkg3>=1.0,<=2.0"], "inline"],
    )

    settings: list[PluginSettingDescriptor] = Field(
        default_factory=list,
        description="User-configurable settings exposed by the plugin.",
    )

    config_templates: list[ConfigTemplate] = Field(
        alias="configTemplates",
        default_factory=list,
        description=(
            "Files that HCLI copies from the plugin directory into `$IDAUSR/cfg`, where IDA's "
            "`getsysfile` and `read_config_file` find them and the user can edit them. "
            "HCLI writes a missing copy at install and upgrade, replaces a copy the user did not edit "
            "when the template changes, and deletes a copy the user did not edit at uninstall. "
            "The plugin must work when the copy is missing."
        ),
        examples=[
            [
                {"source": "defaults/my-plugin.cfg", "destination": "$IDAUSR/cfg/my-plugin.cfg"},
                {"source": "defaults/patterns.txt", "destination": "$IDAUSR/cfg/my-plugin/patterns.txt"},
            ]
        ],
    )

    dependencies: list = Field(
        default_factory=list,
        description=(
            "Plugins to install alongside this one. Each entry is either a "
            "string (plugin reference: bare name, name==version, or name@host) "
            "or an object with 'plugin' (string) and optional 'required' (bool, "
            "default true). String entries are required by default. "
            "Dependencies are resolved across all configured repositories. "
            "Use name@host to pin a dependency to a specific repository and "
            "avoid ambiguity."
        ),
        examples=[
            ["go-runtime-detector", "go-string-extractor==1.2.0"],
            [
                "always-needed",
                {"plugin": "nice-to-have", "required": False},
                {"plugin": "also-needed==2.0.0", "required": True},
            ],
        ],
        json_schema_extra={
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {
                            "plugin": {
                                "type": "string",
                                "description": "Plugin reference in the same format accepted for string entries.",
                            },
                            "required": {
                                "type": "boolean",
                                "default": True,
                                "description": "Whether the parent needs this dependency to function.",
                            },
                        },
                        "required": ["plugin"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
    )

    components: "list[str | IDAMetadataDescriptor]" = Field(
        default_factory=list,
        description=(
            "Subdirectory names of plugins bundled inside this suite's archive. "
            "Each entry is either a string (subdirectory name) or a full "
            "IDAMetadataDescriptor object (expanded component metadata). "
            "String entries must match a subdirectory containing its own "
            "ida-plugin.json whose plugin.name matches the entry. Components "
            "share the suite's lifecycle and are hidden from top-level plugin "
            "listings."
        ),
        examples=[["hexrays-taint-engine", "hexrays-type-propagation"]],
        json_schema_extra={
            "items": {
                "oneOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "description": "Full IDAMetadataDescriptor for a pre-expanded component.",
                        "properties": {
                            "IDAMetadataDescriptorVersion": {"type": "integer", "const": 1},
                            "plugin": {"type": "object"},
                        },
                        "required": ["IDAMetadataDescriptorVersion", "plugin"],
                    },
                ]
            }
        },
    )

    @field_validator("dependencies", mode="before")
    @classmethod
    def validate_dependency_specs(cls, raw: list) -> list:
        from hcli.lib.ida.plugin.reference import DependencyEntry, parse_dependency_entry

        entries: list[DependencyEntry] = []
        seen_names: set[str] = set()
        for item in raw:
            entry = parse_dependency_entry(item)
            name_lower = entry.reference.name.lower()
            if name_lower in seen_names:
                raise ValueError(f"duplicate dependency: '{entry.reference.name}'")
            seen_names.add(name_lower)
            entries.append(entry)
        return entries

    @field_serializer("dependencies")
    def serialize_dependencies(self, entries: list) -> list:
        from hcli.lib.ida.plugin.reference import DependencyEntry

        result: list[str | dict] = []
        for entry in entries:
            # Guard for data constructed without going through model_validate.
            if not isinstance(entry, DependencyEntry):
                result.append(entry)
                continue
            spec = entry.format_spec()
            if entry.required:
                result.append(spec)
            else:
                result.append({"plugin": spec, "required": False})
        return result

    @field_validator("components", mode="before")
    @classmethod
    def parse_component_entries(cls, raw: list[typing.Any]) -> "list[str | IDAMetadataDescriptor]":
        entries: list[str | IDAMetadataDescriptor] = []
        for item in raw:
            if isinstance(item, str):
                entries.append(item)
            elif isinstance(item, dict):
                entries.append(IDAMetadataDescriptor.model_validate(item))
            elif isinstance(item, IDAMetadataDescriptor):
                entries.append(item)
            else:
                raise TypeError(f"component entry must be a string or object, got {type(item).__name__}")
        return entries

    @field_validator("components", mode="after")
    @classmethod
    def validate_component_names(
        cls, entries: "list[str | IDAMetadataDescriptor]"
    ) -> "list[str | IDAMetadataDescriptor]":
        seen_names: set[str] = set()
        for entry in entries:
            if isinstance(entry, str):
                name = entry
                if "==" in name:
                    raise ValueError(f"component entries must not contain version pins: '{name}'")
                if "@" in name:
                    raise ValueError(f"component entries must not contain host qualifiers: '{name}'")
                if not re.match(r"^[a-zA-Z0-9_-]+$", name):
                    raise ValueError(
                        f"component name must consist of ASCII letters, digits, underscores, and hyphens: '{name}'"
                    )
                if name.startswith(("_", "-")) or name.endswith(("_", "-")):
                    raise ValueError(f"component name must not start or end with underscore or hyphen: '{name}'")
            else:
                name = entry.plugin.name
            name_lower = name.lower()
            if name_lower in seen_names:
                raise ValueError("component names must be unique within a single manifest")
            seen_names.add(name_lower)
        return entries

    @field_serializer("components")
    def serialize_components(self, entries: "list[str | IDAMetadataDescriptor]") -> list[str | dict]:
        result: list[str | dict] = []
        for entry in entries:
            if isinstance(entry, str):
                result.append(entry)
            elif isinstance(entry, IDAMetadataDescriptor):
                result.append(entry.model_dump(mode="json", by_alias=True))
            else:
                result.append(entry)
        return result

    @field_validator("requires_python", mode="after")
    @classmethod
    def is_valid_python_requirement(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if not value:
            raise ValueError("requiresPython must not be empty")

        try:
            SpecifierSet(value)
        except InvalidSpecifier as e:
            raise ValueError("requiresPython must be a valid PEP 440 version specifier (for example, '>=3.11')") from e
        return value

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

    @field_validator("ida_version_spec", mode="before")
    @classmethod
    def normalize_ida_version_spec(cls, raw: object) -> object:
        if isinstance(raw, list) and all(isinstance(version, str) for version in raw):
            return get_ida_version_spec_from_list(raw)

        if not isinstance(raw, str):
            return raw

        spec = re.sub(r"\s+", "", raw)
        try:
            _ = parse_ida_version_spec(spec)
        except ValueError as e:
            raise ValueError(f"invalid IDA version spec: '{raw}': {e}") from e
        return spec

    @model_validator(mode="wrap")
    @classmethod
    def record_ida_versions_form(
        cls, data: typing.Any, handler: typing.Callable[[typing.Any], "PluginMetadata"]
    ) -> "PluginMetadata":
        form: IdaVersionsForm = "omitted"
        if isinstance(data, dict):
            raw = data.get("idaVersions")
            if isinstance(raw, list):
                form = "list"
            elif raw is not None:
                form = "spec"
        model = handler(data)
        if not isinstance(data, PluginMetadata):
            model._ida_versions_form = form
        return model

    @property
    def ida_versions_form(self) -> IdaVersionsForm:
        """How ida-plugin.json declared idaVersions: omitted, as a spec string, or as a deprecated list."""
        return self._ida_versions_form

    @property
    def ida_versions(self) -> list[str]:
        """Known IDA versions that the spec matches, oldest first."""
        return get_ida_versions_matching_spec(self.ida_version_spec)

    @model_validator(mode="after")
    def check_at_least_one_contact(self):
        if not self.authors and not self.maintainers:
            raise ValueError("authors or maintainers must be present")
        return self

    @field_serializer("platforms")
    def serialize_sorted_platforms(self, platforms: list[Platform]):
        return sorted(platforms)

    @field_validator("settings", mode="after")
    @classmethod
    def has_unique_setting_keys(cls, settings: list[PluginSettingDescriptor]) -> list[PluginSettingDescriptor]:
        keys = [setting.key for setting in settings]
        if len(set(keys)) != len(keys):
            raise ValueError("setting keys must be unique")

        return settings

    @field_validator("config_templates", mode="after")
    @classmethod
    def has_unique_config_template_destinations(cls, templates: list[ConfigTemplate]) -> list[ConfigTemplate]:
        seen: set[str] = set()
        for template in templates:
            key = template.destination.lower()
            if key in seen:
                raise ValueError(f"config template destinations must be unique: '{template.destination}'")
            seen.add(key)
        return templates

    @field_validator("settings", mode="after")
    @classmethod
    def do_defaults_validate(cls, settings: list[PluginSettingDescriptor]) -> list[PluginSettingDescriptor]:
        for setting in settings:
            if setting.default is None:
                continue

            if setting.type == "boolean":
                if not isinstance(setting.default, bool):
                    raise ValueError(
                        f"setting default value type mismatch: {setting.key}: expected bool, got {type(setting.default).__name__}",
                    )
            elif setting.type == "string":
                if not isinstance(setting.default, str):
                    raise ValueError(
                        f"setting default value type mismatch: {setting.key}: expected str, got {type(setting.default).__name__}",
                    )
                if setting.validation_pattern and not re.match(setting.validation_pattern, setting.default):
                    raise ValueError(
                        f"setting default value does not validate: {setting.key}: '{setting.default}'",
                    )
                if setting.choices and setting.default not in setting.choices:
                    raise ValueError(
                        f"setting default value does not validate: {setting.key}: '{setting.default}'",
                    )

        return settings

    def get_setting(self, key: str) -> PluginSettingDescriptor:
        for setting in self.settings:
            if setting.key == key:
                return setting

        raise KeyError(f"unknown setting: {key}")

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
    """Top-level descriptor for `ida-plugin.json`."""

    model_config = ConfigDict(  # type: ignore
        serialize_by_alias=True,
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ida-plugin.json",
        },
    )

    schema_: str | None = Field(
        alias="$schema",
        default=None,
        exclude=True,
        description="Optional JSON Schema URL used by editors like VSCode for validation.",
    )
    metadata_version: Literal[1] = Field(
        alias="IDAMetadataDescriptorVersion",
        description="Version of the IDA metadata descriptor schema. Must be `1`.",
    )
    plugin: PluginMetadata = Field(description="Plugin metadata.")


def get_component_name(entry: str | IDAMetadataDescriptor) -> str:
    """Extract the plugin name from a component entry (string or descriptor)."""
    if isinstance(entry, str):
        return entry
    return entry.plugin.name


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
            raise ValueError(f"PEP 723 dependencies must be a list, got {type(dependencies).__name__}")  # noqa: TRY004
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
    context: dict[str, str] | None = None,
) -> Iterator[tuple[Path, IDAMetadataDescriptor]]:
    if context is None:
        context = {}
    logger.debug(m("finding plugin metadata", **context))
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        for file_path in zip_file.namelist():
            if not file_path.endswith("ida-plugin.json"):
                continue

            logger.debug(m("found metadata path: %s", file_path, **context))
            with zip_file.open(file_path) as f:
                try:
                    metadata = IDAMetadataDescriptor.model_validate_json(f.read().decode("utf-8"))
                except (ValueError, ValidationError) as e:
                    logger.debug(
                        m("failed to validate metadata: %s", file_path, **(dict(context, path=file_path, error=str(e))))
                    )
                    continue
                else:
                    logger.debug(m("found valid metadata: %s", file_path, **context))
                    yield Path(file_path), metadata


def get_metadata_path_from_plugin_archive(zip_data: bytes, name: str) -> Path:
    for path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.plugin.name == name:
            return path

    raise ValueError(f"plugin '{name}' not found in zip archive")


def get_version_from_plugin_archive(zip_data: bytes, name: str) -> str:
    for _, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.plugin.name == name:
            return metadata.plugin.version
    raise ValueError(f"plugin '{name}' not found in archive")


def get_metadata_from_plugin_archive(zip_data: bytes, name: str) -> tuple[Path, IDAMetadataDescriptor]:
    """Extract ida-plugin.json metadata for plugin with the given name from zip archive without extracting"""

    for path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data):
        if metadata.plugin.name == name:
            return path, metadata

    raise ValueError(f"plugin '{name}' not found in zip archive")


def does_path_exist_in_zip_archive(zip_data: bytes, path: str) -> bool:
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        return path in zip_file.namelist()


def does_plugin_path_exist_in_plugin_archive(zip_data: bytes, plugin_root: Path, relative_path: str) -> bool:
    """does the given path exist relative to the metadata file of the given plugin?"""
    candidate_path = plugin_root / Path(relative_path)
    # zip files always use forward slashes
    return does_path_exist_in_zip_archive(zip_data, candidate_path.as_posix())


def is_ida_version_compatible(current_version: str, ida_version_spec: str) -> bool:
    """Check if the IDA version matches the plugin's `idaVersions` spec.

    A version that cannot be parsed matches no spec.
    """
    try:
        version = parse_ida_version(current_version)
    except ValueError:
        logger.debug("failed to parse IDA version: %s", current_version)
        return False
    return version in parse_ida_version_spec(ida_version_spec)


def is_python_version_compatible(current_version: str, requirement: str) -> bool:
    """Check whether a Python version satisfies a PEP 440 version requirement."""
    return Version(current_version) in SpecifierSet(requirement)


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


MAX_COMPONENT_DEPTH = 10


def validate_config_template_owners(metadata: IDAMetadataDescriptor, suite_names: Iterable[str]) -> None:
    """Check that each config template destination belongs to the plugin or to one of its suites.

    Raises:
        ValueError: when a destination belongs to another plugin.
    """
    name = metadata.plugin.name
    allowed = {name.lower(), *(suite_name.lower() for suite_name in suite_names)}
    for template in metadata.plugin.config_templates:
        if template.owner.lower() not in allowed:
            raise ValueError(
                f"config template destination of plugin '{name}' must start with "
                f"{IDAUSR_TOKEN}/cfg/{name}/ or {IDAUSR_TOKEN}/cfg/{name}.<suffix>: '{template.destination}'"
            )


def get_inline_component_metadata(parent: IDAMetadataDescriptor, name: str) -> IDAMetadataDescriptor | None:
    """Get the expanded metadata of the named component from the `components` field of its parent."""
    for entry in parent.plugin.components:
        if isinstance(entry, IDAMetadataDescriptor) and entry.plugin.name == name:
            return entry
    return None


def get_suite_names(
    plugin_dir: PurePath, name: str, get_metadata: Callable[[PurePath], IDAMetadataDescriptor | None]
) -> set[str]:
    """Get the names of the suites that contain the plugin, at any depth.

    A component lives in the subdirectory of its suite that has the component's name,
    and the suite lists the component in `components`.
    `get_metadata` returns the metadata of the plugin in a directory, or None.
    """
    suite_names: set[str] = set()
    child_dir = plugin_dir
    child_name = name
    for _ in range(MAX_COMPONENT_DEPTH):
        if child_dir.name != child_name or child_dir.parent == child_dir:
            break

        parent = get_metadata(child_dir.parent)
        if parent is None:
            break
        if child_name not in {get_component_name(entry) for entry in parent.plugin.components}:
            break

        suite_names.add(parent.plugin.name)
        child_dir = child_dir.parent
        child_name = parent.plugin.name

    return suite_names


def get_suite_names_from_plugin_archive(zip_data: bytes, metadata_path: Path, name: str) -> set[str]:
    """Get the names of the suites in the archive that contain the plugin, at any depth.

    `metadata_path` is the path of the plugin's `ida-plugin.json` in the archive.
    """
    metadatas = {path.parent: metadata for path, metadata in get_metadatas_with_paths_from_plugin_archive(zip_data)}

    def get_metadata(directory: PurePath, depth: int = 0) -> IDAMetadataDescriptor | None:
        if directory in metadatas:
            return metadatas[directory]
        if depth >= MAX_COMPONENT_DEPTH or directory.parent == directory:
            return None
        parent = get_metadata(directory.parent, depth + 1)
        if parent is None:
            return None
        return get_inline_component_metadata(parent, directory.name)

    return get_suite_names(metadata_path.parent, name, get_metadata)


def get_metadata_from_plugin_directory_or_suite(directory: Path, *, _depth: int = 0) -> IDAMetadataDescriptor | None:
    """Get the metadata of the plugin in the directory.

    A component of a suite may have no `ida-plugin.json`, when the suite lists its expanded metadata.
    """
    metadata_file = directory / "ida-plugin.json"
    if metadata_file.is_file():
        try:
            return IDAMetadataDescriptor.model_validate_json(metadata_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.debug("failed to read metadata %s: %s", metadata_file, e)
            return None

    if _depth >= MAX_COMPONENT_DEPTH or directory.parent == directory:
        return None
    parent = get_metadata_from_plugin_directory_or_suite(directory.parent, _depth=_depth + 1)
    if parent is None:
        return None
    return get_inline_component_metadata(parent, directory.name)


def get_suite_names_from_plugin_directory(plugin_path: Path, name: str) -> set[str]:
    """Get the names of the suites that contain the plugin, from the manifests in its parent directories."""
    return get_suite_names(
        plugin_path.absolute(), name, lambda directory: get_metadata_from_plugin_directory_or_suite(Path(directory))
    )


def has_config_templates_owned_by_others(metadata: IDAMetadataDescriptor) -> bool:
    name = metadata.plugin.name.lower()
    return any(template.owner.lower() != name for template in metadata.plugin.config_templates)


def validate_config_templates_in_plugin_archive(
    zip_data: bytes, metadata_path: Path, metadata: IDAMetadataDescriptor
) -> None:
    """Check that each config template source is a file in the archive and that each destination belongs to the plugin.

    Raises:
        ValueError: when a source is missing or a destination belongs to another plugin.
    """
    if not metadata.plugin.config_templates:
        return

    plugin_root = metadata_path.parent
    with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zip_file:
        names = set(zip_file.namelist())

    for template in metadata.plugin.config_templates:
        if (plugin_root / template.source).as_posix() not in names:
            raise ValueError(f"config template source not found in archive: '{template.source}'")

    suite_names: set[str] = set()
    if has_config_templates_owned_by_others(metadata):
        suite_names = get_suite_names_from_plugin_archive(zip_data, metadata_path, metadata.plugin.name)
    validate_config_template_owners(metadata, suite_names)


def validate_config_templates_in_plugin_directory(plugin_path: Path, metadata: IDAMetadataDescriptor) -> None:
    """Check that each config template source is a file in the directory and that each destination belongs to the plugin.

    Raises:
        ValueError: when a source is missing or a destination belongs to another plugin.
    """
    for template in metadata.plugin.config_templates:
        if not (plugin_path / template.source).is_file():
            raise ValueError(f"config template source not found in directory: '{template.source}'")

    suite_names: set[str] = set()
    if has_config_templates_owned_by_others(metadata):
        suite_names = get_suite_names_from_plugin_directory(plugin_path, metadata.plugin.name)
    validate_config_template_owners(metadata, suite_names)


def validate_metadata_in_plugin_archive(zip_data: bytes, metadata_path: Path, metadata: IDAMetadataDescriptor):
    """validate the `ida-plugin.json` metadata within the given plugin archive.

    The following things must be checked:
    - the following paths must contain relative paths, no paths like ".." or similar escapes:
      - entry point
      - logo path
    - the file paths must exist in the archive:
      - entry point
      - logo path
      - config template sources
    - config template destinations must belong to the plugin or to one of its suites
    """
    plugin_root = metadata_path.parent

    validate_path(metadata.plugin.entry_point, "entry point")
    if metadata.plugin.logo_path:
        validate_path(metadata.plugin.logo_path, "logo path")

    if metadata.plugin.entry_point.endswith(".py"):
        if not does_plugin_path_exist_in_plugin_archive(zip_data, plugin_root, metadata.plugin.entry_point):
            logger.debug("Missing python entry point file: %s", metadata.plugin.entry_point)
            raise ValueError(f"Entry point file not found in archive: '{metadata.plugin.entry_point}'")
    else:
        # binary plugin
        has_bare_name = False
        for ext in (".so", ".dll", ".dylib"):
            if does_plugin_path_exist_in_plugin_archive(zip_data, plugin_root, metadata.plugin.entry_point + ext):
                has_bare_name = True

        if has_bare_name:
            extensions = [
                (".so", PLATFORM_LINUX),
                (".so", PLATFORM_LINUX_ARM),
                (".dll", PLATFORM_WINDOWS),
                (".dll", PLATFORM_WINDOWS_ARM),
                (".dylib", PLATFORM_MACOS_ARM),
                (".dylib", PLATFORM_MACOS_INTEL),
            ]
            for ext, platform in extensions:
                if platform in metadata.plugin.platforms:  # noqa: SIM102
                    if not does_plugin_path_exist_in_plugin_archive(
                        zip_data, plugin_root, metadata.plugin.entry_point + ext
                    ):
                        raise ValueError("missing native entry point: %s", metadata.plugin.entry_point + ext)

        else:
            if (
                set(metadata.plugin.platforms) == {PLATFORM_MACOS_ARM, PLATFORM_MACOS_INTEL}
                or len(set(metadata.plugin.platforms)) == 1
            ):
                if not does_plugin_path_exist_in_plugin_archive(zip_data, plugin_root, metadata.plugin.entry_point):
                    raise ValueError("missing native entry point: %s", metadata.plugin.entry_point)
            else:
                raise ValueError("plugin declares multiple platforms for single native entry point")

        if not has_bare_name:
            logger.debug("Missing native entry point file: %s", metadata.plugin.entry_point)
            raise ValueError(f"Binary plugin file not found in archive: '{metadata.plugin.entry_point}'")

    if metadata.plugin.logo_path:  # noqa: SIM102
        if not does_plugin_path_exist_in_plugin_archive(zip_data, plugin_root, metadata.plugin.logo_path):
            logger.debug("Missing logo file: %s", metadata.plugin.logo_path)
            raise ValueError(f"Logo file not found in archive: '{metadata.plugin.logo_path}'")

    validate_config_templates_in_plugin_archive(zip_data, metadata_path, metadata)


def is_plugin_archive(zip_data: bytes, name: str) -> bool:
    """is the given archive an IDA plugin archive for the given plugin name?"""
    try:
        path, metadata = get_metadata_from_plugin_archive(zip_data, name)
        validate_metadata_in_plugin_archive(zip_data, path, metadata)
        return True
    except (ValueError, Exception):
        return False


def is_source_plugin_archive(zip_data: bytes, name: str) -> bool:
    # the following should be true:
    # - the entry point is a filename ending with .py
    try:
        if not is_plugin_archive(zip_data, name):
            return False

        _, metadata = get_metadata_from_plugin_archive(zip_data, name)

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

        _, metadata = get_metadata_from_plugin_archive(zip_data, name)
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
