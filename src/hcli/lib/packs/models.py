"""Release v1 and sidecar v1 wire models.

Parsed leniently: unknown fields are ignored (the pack builder may add fields to v1),
but anything the client relies on is validated — the schema version, the
format binding, lowercase sha256 checksums, and repository-relative paths
made of portable segments that cannot escape the format root (the component
and published filename become local path segments in ``get``).
"""

from __future__ import annotations

import io
import json
import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Final

import zstandard
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from hcli.lib.packs.errors import InvalidReleaseError

RELEASE_SCHEMA_VERSION: Final = 1
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
# One path segment of a repository path (and of a local output path).
SAFE_SEGMENT_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
WINDOWS_RESERVED_NAMES: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)


class PackFormat(str, Enum):
    """The independent format roots of the repository."""

    LUMINA = "lumina"
    FLIRT = "flirt"


def _check_sha256(value: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(f"not a lowercase sha256: {value!r}")
    return value


def is_safe_segment(segment: str) -> bool:
    """Whether ``segment`` is a file name that means the same thing on every OS.

    Strict on purpose, as published names become local file names in
    ``get``: no separators, drive prefixes (``C:x``), leading dots, trailing
    dots, or Windows device names (``CON``, ``nul.txt``).
    """
    if not SAFE_SEGMENT_PATTERN.fullmatch(segment) or segment.endswith("."):
        return False
    return segment.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES


def _check_relative_path(value: str) -> str:
    """Reject anything but ``/``-separated safe segments: keys stay inside the format root."""
    if not value or not all(is_safe_segment(part) for part in value.split("/")):
        raise ValueError(f"unsafe repository path: {value!r}")
    return value


def _check_segment(value: str) -> str:
    if not is_safe_segment(value):
        raise ValueError(f"unsafe path segment: {value!r}")
    return value


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class MetaReceipt(_WireModel):
    """Where a pack's contents sidecar lives, and its exact compressed bytes."""

    path: str
    sha256: str
    size: int
    size_uncompressed: int | None = None

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return _check_relative_path(value)

    @field_validator("sha256")
    @classmethod
    def check_sha256(cls, value: str) -> str:
        return _check_sha256(value)


class ReleasePack(_WireModel):
    """One pack entry of a Release."""

    name: str
    component: str
    platform: str
    arch: str
    group: str
    revision: int
    path: str
    sha256: str
    size: int
    size_uncompressed: int | None = None
    member_bytes: int | None = None
    producer_bytes: int | None = None
    members: int
    # Retained Lumina rows; null for FLIRT (count signatures via ``members``).
    rows: int | None = None
    pack_format: int = 1
    packer_commit: str | None = None
    built_at: str
    meta: MetaReceipt

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return _check_relative_path(value)

    @field_validator("component")
    @classmethod
    def check_component(cls, value: str) -> str:
        # ``get`` uses the component as a local folder name.
        return _check_segment(value)

    @field_validator("sha256")
    @classmethod
    def check_sha256(cls, value: str) -> str:
        return _check_sha256(value)

    @property
    def address(self) -> str:
        """The CLI address, ``component/platform-group-arch``."""
        return f"{self.component}/{self.platform}-{self.group}-{self.arch}"

    @property
    def filename(self) -> str:
        """The published archive filename, e.g. ``windows-all-arm64-r2.tar.zst``."""
        return PurePosixPath(self.path).name

    def output_path(self, fmt: PackFormat) -> PurePosixPath:
        """Where ``get`` puts the pack under its output folder: ``<format>/<component>/<filename>``.

        Format and component keep a Lumina and a FLIRT pack (or two
        components) with the same published filename apart.
        """
        return PurePosixPath(fmt.value, self.component, self.filename)


class Release(_WireModel):
    """A format root's Release index (``<format>/dists/stable/Release``)."""

    schema_version: int
    repository: str
    channel: str
    snapshot: str
    previous_snapshot: str | None = None
    published_at: str
    packs: list[ReleasePack]


class MetaRows(_WireModel):
    """Per-artifact Lumina row accounting (eligible = duplicates + conflicts + retained)."""

    input: int | None = None
    eligible: int | None = None
    duplicates: int | None = None
    # Absent in sidecars written before the field existed; reads as zero.
    conflicts: int = 0
    retained: int | None = None


class MetaRecord(_WireModel):
    """One retained artifact (library) in a pack's contents sidecar."""

    producer: str
    name: str
    path: str
    bytes: int | None = None
    producer_bytes: int | None = None
    # Null for FLIRT.
    rows: MetaRows | None = None
    toolset: str | None = None
    ida_version: str | None = None
    license: Any = None


def parse_release(data: bytes, expected: PackFormat, source: str) -> Release:
    """Parse and sanity-check a Release document for format ``expected``."""
    try:
        payload = json.loads(data)
    except ValueError as e:
        raise InvalidReleaseError(f"{source} is not valid JSON: {e}.") from e
    if not isinstance(payload, dict):
        raise InvalidReleaseError(f"{source} is not a JSON object.")
    version = payload.get("schema_version")
    if version != RELEASE_SCHEMA_VERSION:
        raise InvalidReleaseError(
            f"{source} has schema_version {version!r}; this hcli reads version {RELEASE_SCHEMA_VERSION}.",
            "upgrade hcli (hcli update)",
        )
    try:
        release = Release.model_validate(payload)
    except ValidationError as e:
        raise InvalidReleaseError(f"{source} is malformed: {e}") from e
    if release.repository != expected.value:
        raise InvalidReleaseError(
            f"{source} belongs to repository {release.repository!r}, expected {expected.value!r}.",
        )
    return release


def parse_meta(compressed: bytes, source: str) -> list[MetaRecord]:
    """Decompress and parse a zstd JSONL contents sidecar."""
    try:
        # A stream reader tolerates frames without a recorded content size
        # (sidecars written before the pack builder started recording it).
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed), read_across_frames=True) as reader:
            text = reader.read().decode("utf-8")
    except (zstandard.ZstdError, UnicodeDecodeError) as e:
        raise InvalidReleaseError(f"{source} is not a valid zstd JSONL sidecar: {e}.") from e
    records: list[MetaRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(MetaRecord.model_validate_json(line))
        except ValidationError as e:
            raise InvalidReleaseError(f"{source} line {number} is malformed: {e}") from e
    return records
