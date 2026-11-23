"""Discover IDA's Python without running IDA, by reading IDA's own configuration.

IDA stores only the path of the libpython shared library, in the registry value
"Python3TargetDLL". The version comes from its file name, the prefix from
walking up to the stdlib landmark (as the IDAPython plugin does), and the
interpreter from the prefix. No IDA database is opened and no Python runtime is
initialized - the only IDA code involved is reg_str_get.

Cheap, and works where idat is not installed, which is why it runs before the
idat probe. The registry names the base install even when IDAPython will
run inside a virtualenv, so $IDAPYTHON_VENV_EXECUTABLE is consulted first.
"""

import ctypes
import logging
import os
import re
import sys
from pathlib import Path

from hcli.lib.ida import find_current_ida_install_directory, get_ida_path

from . import PythonNotFoundError

logger = logging.getLogger(__name__)

REG_VALUE = "Python3TargetDLL"
MAX_WALK_DEPTH = 3  # matches the IDAPython plugin's own search depth

# (major, minor, modifiers), modifiers being ABI suffixes such as "d" (debug).
Version = tuple[int, int, str]


class _qstring(ctypes.Structure):
    """Layout of qstring, i.e. qvector<char>."""

    _fields_ = [
        ("array", ctypes.c_char_p),
        ("n", ctypes.c_size_t),
        ("alloc", ctypes.c_size_t),
    ]


def _load_libida() -> ctypes.CDLL:
    """Return a handle from which reg_str_get can be resolved.

    Loads libida out of the current installation, falling back to idapro (which
    resolves the installation itself) only when that is not possible: importing
    idapro calls init_library(), which we needn't trigger to read one value.
    """
    try:
        libname = {"win32": "ida.dll", "darwin": "libida.dylib"}.get(sys.platform, "libida.so")
        return ctypes.CDLL(str(Path(get_ida_path(find_current_ida_install_directory())) / libname))
    except Exception as e:
        logger.debug("could not load libida directly, falling back to idapro: %s", e)

    try:
        import idapro  # type: ignore[import-not-found]
    except ImportError as e:
        raise PythonNotFoundError(
            "could not load libida to read IDA's Python configuration, and idalib "
            "(import idapro) is not available. Run `hcli ida install` to activate idalib, "
            "or set HCLI_CURRENT_IDA_PYTHON_EXE=/path/to/python."
        ) from e

    # idapro.libida is the libidalib handle; reg_str_get resolves through its
    # dependency on libida.
    return idapro.libida


def _read_target_dll() -> str | None:
    """Read IDA's configured libpython path, or None if unset."""
    fn = _load_libida().reg_str_get
    fn.restype = ctypes.c_bool
    fn.argtypes = [ctypes.POINTER(_qstring), ctypes.c_char_p, ctypes.c_char_p]

    buf = _qstring()
    # subkey=None means the root key, Software\Hex-Rays\IDA
    if not fn(ctypes.byref(buf), REG_VALUE.encode(), None):
        return None
    return buf.array.decode() if buf.array else None


def _version_of(libpath: str) -> Version | None:
    """Return (major, minor, modifiers) from a libpython file name."""
    name = os.path.basename(libpath)
    match = re.search(r"libpython(\d+)\.(\d+)([a-z]*)", name) or re.search(
        r"python(\d)(\d+)([a-z_]*)\.dll", name, re.IGNORECASE
    )
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3) or ""


def _has_stdlib(prefix: str, version: Version) -> bool:
    """Test the stdlib landmark, as the IDAPython plugin does."""
    if os.name == "nt":
        return os.path.isfile(os.path.join(prefix, "Lib", "os.py"))
    major, minor, modifiers = version
    # Debug builds sometimes share the release stdlib directory (Debian), so
    # accept the unsuffixed name too; IDA itself only tries the first.
    for suffix in [modifiers, ""] if modifiers else [""]:
        if os.path.isfile(os.path.join(prefix, "lib", f"python{major}.{minor}{suffix}", "os.py")):
            return True
    return False


def _prefix_of(libpath: str, version: Version) -> str | None:
    """Walk up from the library to the install prefix (PYTHONHOME)."""
    candidate = os.path.dirname(libpath)
    for _ in range(MAX_WALK_DEPTH):
        if _has_stdlib(candidate, version):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return None


def _executable_of(prefix: str, version: Version) -> str | None:
    """Locate the interpreter inside the prefix."""
    major, minor, modifiers = version
    if os.name == "nt":
        candidates = [os.path.join(prefix, "python.exe")]
    else:
        bindir = os.path.join(prefix, "bin")
        candidates = [
            os.path.join(bindir, f"python{major}.{minor}{modifiers}"),
            os.path.join(bindir, f"python{major}.{minor}"),
            os.path.join(bindir, f"python{major}"),
            os.path.join(bindir, "python"),
        ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def find_python_executable() -> Path:
    """Locate IDA's Python executable from IDA's configured libpython.

    Raises:
        PythonNotFoundError: if the interpreter could not be determined.
    """
    hint = "Please run idapyswitch to select a Python installation, then try again."

    libpath = _read_target_dll()
    if libpath is None:
        raise PythonNotFoundError(f"{REG_VALUE} is not set in IDA's configuration.\n{hint}")

    version = _version_of(libpath)
    if version is None:
        raise PythonNotFoundError(f"could not determine the Python version of {libpath}.\n{hint}")

    prefix = _prefix_of(libpath, version)
    if prefix is None:
        raise PythonNotFoundError(f"could not find a Python installation around {libpath}.\n{hint}")

    executable = _executable_of(prefix, version)
    if executable is None:
        raise PythonNotFoundError(f"could not find a Python interpreter in {prefix}.\n{hint}")

    logger.debug("IDA's Python: %s (libpython %s)", executable, libpath)
    return Path(executable)
