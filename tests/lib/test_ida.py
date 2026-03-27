import os
import platform
import struct
import tempfile
from pathlib import Path

import pytest

from hcli.lib.ida import (
    detect_binary_arch,
    find_current_ida_install_directory,
    find_current_ida_platform,
    find_current_ida_version,
    find_current_idat_executable,
    get_ida_config,
    get_ida_config_path,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
)


def test_get_ida_config_path():
    result = get_ida_config_path()
    assert isinstance(result, Path)
    assert result.name == "ida-config.json"


def test_get_ida_config():
    result = get_ida_config()
    assert result is not None
    assert hasattr(result, "paths")
    assert hasattr(result.paths, "installation_directory")


def test_find_current_ida_install_directory():
    result = find_current_ida_install_directory()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_dir()


def has_idat():
    if "HCLI_HAS_IDAT" not in os.environ:
        return True

    return os.environ["HCLI_HAS_IDAT"].lower() not in ("", "0", "false", "f")


@pytest.mark.skipif(not has_idat(), reason="Skip when idat not present (Free/Home)")
def test_find_current_idat_executable():
    result = find_current_idat_executable()
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_file()
    assert "idat" in result.name.lower()


# Platform-specific tests for find_current_ida_platform()
@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
def test_find_current_ida_platform_windows():
    """Test find_current_ida_platform() on Windows."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result == "windows-x86_64"


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux-specific test")
def test_find_current_ida_platform_linux():
    """Test find_current_ida_platform() on Linux."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result == "linux-x86_64"


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-specific test")
def test_find_current_ida_platform_macos():
    """Test find_current_ida_platform() on macOS."""
    result = find_current_ida_platform()
    assert isinstance(result, str)
    assert result in ["macos-x86_64", "macos-aarch64"]


def test_find_current_ida_version():
    """Test find_current_ida_version() returns expected version."""
    result = find_current_ida_version()
    assert isinstance(result, str)
    assert result in ["9.0", "9.1", "9.2", "9.3"]


def test_parse_version_from_ida_pro_py():
    """Test parsing IDA version from python/ida_pro.py."""
    ida_dir = find_current_ida_install_directory()
    result = parse_version_from_ida_pro_py(ida_dir)
    if has_idat():
        # editions with IDAPython should have python/ida_pro.py
        assert result in ["9.0", "9.1", "9.2", "9.3"]


def test_parse_version_from_ida_pro_py_missing():
    """Test that missing ida_pro.py returns None."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = parse_version_from_ida_pro_py(Path(tmpdir))
        assert result is None


def test_parse_version_from_dir_name():
    """Test parsing IDA version from directory names."""
    assert parse_version_from_dir_name(Path("/opt/IDA Professional 9.2")) == "9.2"
    assert parse_version_from_dir_name(Path("/opt/IDA-Professional-9.2")) == "9.2"
    assert parse_version_from_dir_name(Path("/opt/IDA Professional 9.1sp1")) == "9.1"
    assert parse_version_from_dir_name(Path("/Applications/IDA Professional 9.2.app")) == "9.2"
    assert parse_version_from_dir_name(Path("/opt/my-ida")) is None


def test_detect_binary_arch_elf_x86_64():
    """Test detecting x86_64 ELF binary."""
    # Minimal ELF header: magic + class(64) + data(LE) + ... + e_machine=0x3E
    header = bytearray(20)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little-endian
    struct.pack_into("<H", header, 18, 0x3E)  # EM_X86_64
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(header)
        f.flush()
        assert detect_binary_arch(Path(f.name)) == "x86_64"
    os.unlink(f.name)


def test_detect_binary_arch_elf_aarch64():
    """Test detecting aarch64 ELF binary."""
    header = bytearray(20)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    struct.pack_into("<H", header, 18, 0xB7)  # EM_AARCH64
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(header)
        f.flush()
        assert detect_binary_arch(Path(f.name)) == "aarch64"
    os.unlink(f.name)


def test_detect_binary_arch_macho_x86_64():
    """Test detecting x86_64 Mach-O binary."""
    header = bytearray(20)
    struct.pack_into("<I", header, 0, 0xFEEDFACF)  # MH_MAGIC_64
    struct.pack_into("<I", header, 4, 0x01000007)  # CPU_TYPE_X86_64
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(header)
        f.flush()
        assert detect_binary_arch(Path(f.name)) == "x86_64"
    os.unlink(f.name)


def test_detect_binary_arch_macho_aarch64():
    """Test detecting aarch64 Mach-O binary."""
    header = bytearray(20)
    struct.pack_into("<I", header, 0, 0xFEEDFACF)  # MH_MAGIC_64
    struct.pack_into("<I", header, 4, 0x0100000C)  # CPU_TYPE_ARM64
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(header)
        f.flush()
        assert detect_binary_arch(Path(f.name)) == "aarch64"
    os.unlink(f.name)


def test_detect_binary_arch_unknown():
    """Test that unrecognized binary returns None."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"not a binary format at all!")
        f.flush()
        assert detect_binary_arch(Path(f.name)) is None
    os.unlink(f.name)
