"""IDA version detection from the main IDA executable."""

from __future__ import annotations

import logging
import plistlib
import re
import struct
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 9.4.260622.abc123def or 9.0.241217 (ASCII ida_build_version, 9.1+)
_RE_BUILD_VERSION = re.compile(rb"\d+\.\d+\.\d{6}(?:\.[a-z0-9]+)?")

# 9.0.24.0925 (PE FileVersion, UTF-16LE, 9.0+)
_RE_FILE_VERSION = re.compile(rb"\d+\.\d+\.\d{2}\.\d{4}")


def _get_ida_version_pe(path: Path) -> str | None:
    """Extract FileVersion from a PE VERSIONINFO resource using pefile."""
    import pefile

    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])

    if not hasattr(pe, "FileInfo"):
        return None

    for file_info in pe.FileInfo:
        for entry in file_info:
            if entry.Key != b"StringFileInfo":
                continue
            for table in entry.StringTable:
                version = table.entries.get(b"FileVersion")
                if version:
                    return version.decode()

    return None


def _get_ida_version_elf(path: Path) -> str | None:
    """Extract version from the .ida.version ELF section using pyelftools."""
    from elftools.elf.elffile import ELFFile

    with path.open("rb") as f:
        elf = ELFFile(f)
        section = elf.get_section_by_name(".ida.version")
        if section is None:
            return None
        data = section.data().decode().strip("\x00")
        m = _RE_BUILD_VERSION.match(data.encode())
        return m.group(0).decode() if m else data


def _get_ida_version_macho(path: Path) -> str | None:
    """Extract CFBundleShortVersionString from the embedded Mach-O plist using macholib."""
    from macholib.mach_o import LC_SEGMENT_64
    from macholib.MachO import MachO

    # IDA on macOS is 64-bit only; no need for LC_SEGMENT / 32-bit support.
    sect_64_fmt = "<16s16sQQIIIIIIII"
    sect_size = struct.calcsize(sect_64_fmt)

    macho = MachO(str(path))
    header = macho.headers[0]

    for lc, cmd, data in header.commands:
        if lc.cmd != LC_SEGMENT_64:
            continue
        if cmd.segname.rstrip(b"\x00") != b"__TEXT":
            continue

        for i in range(cmd.nsects):
            fields = struct.unpack(sect_64_fmt, data[i * sect_size : (i + 1) * sect_size])
            if fields[0].rstrip(b"\x00") != b"__info_plist":
                continue

            # fields[3] = size, fields[4] = offset
            size, offset = fields[3], fields[4]
            with path.open("rb") as f:
                # header.offset handles fat/universal binaries (0 for thin)
                f.seek(header.offset + offset)
                plist_bytes = f.read(size)

            info = plistlib.loads(plist_bytes)
            version = info.get("CFBundleShortVersionString")
            return version if isinstance(version, str) else None

    return None


def _extract_file_version_raw(data: bytes) -> str | None:
    """Walk past the UTF-16LE FileVersion key to read its value."""
    marker = "FileVersion".encode("utf-16-le")
    idx = data.find(marker)
    if idx < 0:
        return None

    idx += len(marker)
    # Skip null terminators and alignment padding.
    while idx < len(data) - 1 and data[idx : idx + 2] == b"\x00\x00":
        idx += 2

    # Read null-terminated UTF-16LE value.
    end = data.find(b"\x00\x00", idx)
    if end < 0:
        return None

    text = data[idx : end + 1].decode("utf-16-le", errors="ignore").strip("\x00")
    m = _RE_FILE_VERSION.match(text.encode())
    return m.group(0).decode() if m else None


def _get_ida_version_generic(path: Path) -> str | None:
    """Scan the binary for known version string patterns."""
    data = path.read_bytes()

    if sys.platform == "win32":
        return _extract_file_version_raw(data)

    m = _RE_BUILD_VERSION.search(data)
    return m.group(0).decode() if m else None


_PLATFORM_EXTRACTORS = {
    "win32": _get_ida_version_pe,
    "linux": _get_ida_version_elf,
    "darwin": _get_ida_version_macho,
}


def get_ida_binary_version(path: Path) -> str | None:
    """Extract the full IDA version string from an IDA executable."""
    if not path.exists() or not path.is_file():
        return None

    extractor = _PLATFORM_EXTRACTORS.get(sys.platform)
    if extractor is not None:
        try:
            version = extractor(path)
            if version:
                return version
        except Exception as e:
            logger.debug("failed to extract IDA version from %s using structured parser: %s", path, e)

    try:
        return _get_ida_version_generic(path)
    except OSError as e:
        logger.debug("failed to read IDA version from %s: %s", path, e)
        return None


def parse_ida_binary_version(version_str: str) -> tuple[int, int, int] | None:
    """Normalize an IDA binary version string into a comparable tuple.

    Examples:
        9.4.260622.abc123def -> (9, 4, 260622)
        9.0.241217           -> (9, 0, 241217)
        9.0.24.1201          -> (9, 0, 241201)
        9.4                  -> (9, 4, 0)
    """
    parts = version_str.strip().split(".")
    if len(parts) < 2:
        return None

    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return None

    date = 0
    if len(parts) >= 3:
        if len(parts) >= 4 and len(parts[2]) == 2 and parts[2].isdigit() and parts[3].isdigit():
            # PE FileVersion: MAJOR.MINOR.YY.MMDD -> YYMMDD
            date = int(parts[2]) * 10000 + int(parts[3])
        elif parts[2].isdigit():
            # ida_build_version: MAJOR.MINOR.YYMMDD[.BUILD_ID]
            date = int(parts[2])
        else:
            return None

    return (major, minor, date)


def normalize_ida_binary_version(version_str: str) -> str | None:
    """Convert an IDA binary version string to HCLI's major.minor version format."""
    parsed = parse_ida_binary_version(version_str)
    if parsed is None:
        return None
    major, minor, _date = parsed
    return f"{major}.{minor}"


def parse_version_from_ida_binary(path: Path) -> str | None:
    """Parse HCLI's major.minor IDA version from the main IDA executable."""
    version = get_ida_binary_version(path)
    if version is None:
        return None
    return normalize_ida_binary_version(version)
