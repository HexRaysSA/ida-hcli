import os
import platform
import struct
import sys
import tempfile
import types
from pathlib import Path

import pytest

from hcli.lib.ida import (
    IdaProduct,
    _is_ida_install_dir_name,
    _prepare_headless_ida_user_dir,
    accept_eula,
    detect_binary_arch,
    find_current_ida_install_directory,
    find_current_ida_platform,
    find_current_ida_version,
    find_current_idat_executable,
    generate_instance_name,
    get_ida_config,
    get_ida_config_path,
    get_ida_path,
    get_idalib_path,
    is_idalib_capable_installation,
    parse_instance_version,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
    parse_version_from_windows_registry,
    select_default_ida_instance,
)
from hcli.lib.ida.version import normalize_ida_binary_version, parse_version_from_ida_binary


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
    assert result in ["9.0", "9.1", "9.2", "9.3", "9.4"]


def test_parse_version_from_ida_pro_py():
    """Test parsing IDA version from python/ida_pro.py."""
    ida_dir = find_current_ida_install_directory()
    result = parse_version_from_ida_pro_py(ida_dir)
    if has_idat():
        # editions with IDAPython should have python/ida_pro.py
        assert result in ["9.0", "9.1", "9.2", "9.3", "9.4"]


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
    assert parse_version_from_dir_name(Path("/opt/IDA Professional 9.4 beta 1")) == "9.4"
    assert parse_version_from_dir_name(Path("/opt/IDA-Professional-9.4-beta-1")) == "9.4"
    assert parse_version_from_dir_name(Path("/opt/ida-pro-9.4")) == "9.4"
    assert parse_version_from_dir_name(Path("/opt/my-ida")) is None


def test_normalize_ida_binary_version():
    assert normalize_ida_binary_version("9.4.260622.abc123def") == "9.4"
    assert normalize_ida_binary_version("9.0.241217") == "9.0"
    assert normalize_ida_binary_version("9.0.24.0925") == "9.0"
    assert normalize_ida_binary_version("9.4") == "9.4"
    assert normalize_ida_binary_version("not-a-version") is None


def test_parse_version_from_ida_binary_linux_fallback(monkeypatch, tmp_path):
    binary = tmp_path / "ida"
    binary.write_bytes(b"prefix 9.4.260622.abc123def suffix")

    monkeypatch.setattr("hcli.lib.ida.version.sys.platform", "linux")

    assert parse_version_from_ida_binary(binary) == "9.4"


def test_parse_version_from_ida_binary_windows_fallback(monkeypatch, tmp_path):
    binary = tmp_path / "ida.exe"
    binary.write_bytes(
        b"prefix"
        + "FileVersion".encode("utf-16-le")
        + b"\x00\x00"
        + "9.0.24.0925".encode("utf-16-le")
        + b"\x00\x00"
        + b"suffix"
    )

    monkeypatch.setattr("hcli.lib.ida.version.sys.platform", "win32")

    assert parse_version_from_ida_binary(binary) == "9.0"


def test_parse_version_from_windows_registry(monkeypatch):
    class FakeKey:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_winreg = types.SimpleNamespace(HKEY_LOCAL_MACHINE="HKLM", KEY_READ=1)

    def open_key(root, path, *_args):
        if root == "HKLM" and path == r"Software\Microsoft\Windows\CurrentVersion\Uninstall":
            return FakeKey("root")
        if isinstance(root, FakeKey) and root.name == "root" and path == "IDA Professional 9.1":
            return FakeKey("ida91")
        raise OSError

    def enum_key(_root, index):
        if index == 0:
            return "IDA Professional 9.1"
        raise OSError

    def query_value_ex(key, value_name):
        values = {
            "DisplayName": "IDA Professional 9.1",
            "DisplayVersion": "9.1",
            "InstallLocation": r"C:\IDA91",
        }
        if key.name == "ida91" and value_name in values:
            return values[value_name], None
        raise OSError

    fake_winreg.OpenKey = open_key
    fake_winreg.EnumKey = enum_key
    fake_winreg.QueryValueEx = query_value_ex
    fake_winreg.CloseKey = lambda _key: None

    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr("hcli.lib.ida.is_ida_dir", lambda _path: True)

    assert parse_version_from_windows_registry(Path(r"C:\IDA91")) == "9.1"
    assert generate_instance_name(Path(r"C:\IDA91")) == "ida-pro-9.1"


def test_parse_instance_version_stops_after_first_valid_source(monkeypatch, tmp_path):
    def fail(*_args):
        raise AssertionError("version source should not be evaluated")

    monkeypatch.setattr("hcli.lib.ida.parse_version_from_windows_registry", lambda _ida_dir: "9.4")
    monkeypatch.setattr("hcli.lib.ida.parse_version_from_ida_pro_py", fail)
    monkeypatch.setattr("hcli.lib.ida.parse_version_from_ida_binary", fail)
    monkeypatch.setattr("hcli.lib.ida.parse_version_from_dir_name", fail)

    assert str(parse_instance_version("ida94", tmp_path / "IDA94")) == "9.4"


def test_parse_instance_version_uses_ida_binary_for_nonstandard_directory(monkeypatch, tmp_path):
    ida_dir = tmp_path / "IDA94"
    ida_dir.mkdir()
    binary = ida_dir / "ida.exe"
    binary.write_bytes(
        b"prefix"
        + "FileVersion".encode("utf-16-le")
        + b"\x00\x00"
        + "9.4.26.0622".encode("utf-16-le")
        + b"\x00\x00"
        + b"suffix"
    )

    monkeypatch.setattr("hcli.lib.ida.version.sys.platform", "win32")
    monkeypatch.setattr("hcli.lib.ida.get_ida_binary_path", lambda _ida_dir: binary)

    assert str(parse_instance_version("ida94", ida_dir)) == "9.4"


def test_parse_instance_version_uses_ida_pro_py_for_nonstandard_directory(tmp_path):
    ida_dir = tmp_path / "IDA91"
    python_dir = get_ida_path(ida_dir) / "python"
    python_dir.mkdir(parents=True)
    (python_dir / "ida_pro.py").write_text('"""IDA SDK v9.1."""', encoding="utf-8")

    assert str(parse_instance_version("ida91", ida_dir)) == "9.1"


def test_select_default_ida_instance_uses_highest_parsed_version(tmp_path):
    ida91 = tmp_path / "IDA91"
    python_dir = ida91 / "python"
    python_dir.mkdir(parents=True)
    (python_dir / "ida_pro.py").write_text('"""IDA SDK v9.1."""', encoding="utf-8")

    ida94 = tmp_path / "IDA Professional 9.4"

    assert select_default_ida_instance([("ida-pro-9.4", ida94), ("ida91", ida91)]) == "ida-pro-9.4"


def test_select_default_ida_instance_falls_back_to_name_without_versions(tmp_path):
    assert select_default_ida_instance([("ida-a", tmp_path / "ida-a"), ("ida-z", tmp_path / "ida-z")]) == "ida-z"


def test_accept_eula_writes_supported_registry_keys(monkeypatch, tmp_path):
    writes = []

    monkeypatch.setitem(sys.modules, "idapro", types.SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "ida_registry",
        types.SimpleNamespace(reg_write_int=lambda key, value: writes.append((key, value))),
    )

    accept_eula(tmp_path)

    assert writes == [
        ("EULA 90", 1),
        ("EULA 91", 1),
        ("EULA 92", 1),
        ("EULA 93", 1),
        ("EULA 94", 1),
    ]


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("ida-pro_94_x64linux.run", "IDA Professional 9.4"),
        ("ida-classroom_94_x64linux.run", "IDA Classroom 9.4"),
        ("ida-essential_94_x64linux.run", "IDA Essential 9.4"),
        ("ida-free_94_x64linux.run", "IDA Free 9.4"),
        ("ida-home-arm_94_x64linux.run", "IDA Home (ARM) 9.4"),
        ("ida-home-mips_94_x64linux.run", "IDA Home (MIPS) 9.4"),
        ("ida-home-pc_94_x64linux.run", "IDA Home (PC) 9.4"),
        ("ida-home-ppc_94_x64linux.run", "IDA Home (PPC) 9.4"),
        ("ida-home-riscv_94_x64linux.run", "IDA Home (RISC-V) 9.4"),
    ],
)
def test_installer_filename_parsing_matches_ida_xml_editions(filename, expected):
    """Installer filename editions should map to ida.xml's ida_edition names."""
    assert str(IdaProduct.from_installer_filename(filename)) == expected


@pytest.mark.parametrize(
    "name",
    [
        # Windows install directories and macOS app bundles from ida.xml.
        "IDA Professional 9.4",
        "IDA Classroom 9.4",
        "IDA Essential 9.4",
        "IDA Free 9.4",
        "IDA Home (ARM) 9.4",
        "IDA Home (MIPS) 9.4",
        "IDA Home (PC) 9.4",
        "IDA Home (PPC) 9.4",
        "IDA Home (RISC-V) 9.4.app",
        # Linux install directories from ida.xml.
        "ida-pro-9.4",
        "ida-classroom-9.4",
        "ida-essential-9.4",
        "ida-free-9.4",
        "ida-home-arm-9.4",
        "ida-home-mips-9.4",
        "ida-home-pc-9.4",
        "ida-home-ppc-9.4",
        "ida-home-riscv-9.4",
        # Legacy hcli Linux install directory names.
        "IDA Professional 9.2",
        "IDA-Professional-9.2",
        "IDA-Home-(PC)-9.2",
    ],
)
def test_ida_install_dir_name_detection_matches_ida_xml(name):
    assert _is_ida_install_dir_name(name)


@pytest.mark.parametrize("name", ["IDA 9.4", "ida-unknown-9.4", "IDA Professional", "my-ida-pro-9.4"])
def test_ida_install_dir_name_detection_rejects_non_installer_names(name):
    assert not _is_ida_install_dir_name(name)


@pytest.mark.parametrize(
    ("os_name", "filename"),
    [
        ("windows", "idalib.dll"),
        ("linux", "libidalib.so"),
        ("mac", "libidalib.dylib"),
    ],
)
def test_idalib_capable_installation_probes_for_idalib(monkeypatch, tmp_path, os_name, filename):
    monkeypatch.setattr("hcli.lib.ida.get_os", lambda: os_name)
    ida_dir = tmp_path / "IDA Home 9.4"
    idalib_path = get_idalib_path(ida_dir)

    assert idalib_path.name == filename
    assert not is_idalib_capable_installation(ida_dir)

    idalib_path.parent.mkdir(parents=True)
    idalib_path.write_bytes(b"")

    assert is_idalib_capable_installation(ida_dir)


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


def test_prepare_headless_ida_user_dir_copies_only_required_files(tmp_path):
    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"

    (source_dir / "cfg").mkdir(parents=True)
    (source_dir / "cfg" / "idapython.cfg").write_text("configured", encoding="utf-8")
    (source_dir / "plugins").mkdir()
    (source_dir / "plugins" / "bad_plugin.py").write_text("raise RuntimeError", encoding="utf-8")
    (source_dir / "mcp").mkdir()
    (source_dir / "mcp" / "state.json").write_text("{}", encoding="utf-8")
    (source_dir / "ida.reg").write_text("registry", encoding="utf-8")
    (source_dir / "ida-config.json").write_text("{}", encoding="utf-8")
    (source_dir / "idapythonrc.py").write_text("raise RuntimeError", encoding="utf-8")
    (source_dir / "license.hexlic").write_text("license", encoding="utf-8")

    _prepare_headless_ida_user_dir(source_dir, target_dir)

    assert (target_dir / "cfg" / "idapython.cfg").read_text(encoding="utf-8") == "configured"
    assert (target_dir / "ida.reg").read_text(encoding="utf-8") == "registry"
    assert (target_dir / "idapythonrc.py").read_text(encoding="utf-8") == "raise RuntimeError"
    assert (target_dir / "license.hexlic").read_text(encoding="utf-8") == "license"
    assert not (target_dir / "ida-config.json").exists()
    assert not (target_dir / "plugins").exists()
    assert not (target_dir / "mcp").exists()
