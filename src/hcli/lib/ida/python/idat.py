"""Discover IDA's Python by asking IDAPython itself, via a headless idat run.

Authoritative, because the answer comes from the interpreter IDA actually
loaded - including any venv that idapythonrc.py activated. Costly, because it
launches idat. Unavailable on editions that ship no idat (Free/Home), which is
what idapro.py is for.
"""

import logging
from pathlib import Path

from hcli.lib.ida import find_current_idat_executable, run_py_in_current_idapython

from .paths import PythonNotFoundError, _derive_python_exe

logger = logging.getLogger(__name__)


# Script run inside IDA's embedded Python via idat.
# Returns enough sys/env info to detect the Python executable on the hcli side.
GET_PYTHON_INFO_PY = """
import sys
import io
import json
import os

# ensure UTF-8 output for unicode install paths
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

print("__hcli__:" + json.dumps({
    "frozen": getattr(sys, "frozen", False),
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "executable": sys.executable,
    "virtual_env": os.environ.get("VIRTUAL_ENV"),
    "idapython_venv_executable": os.environ.get("IDAPYTHON_VENV_EXECUTABLE"),
    "version_major": sys.version_info.major,
    "version_minor": sys.version_info.minor,
}))
sys.exit()
"""


def is_available() -> bool:
    """Whether the current IDA installation ships an idat we can run."""
    try:
        return find_current_idat_executable().exists()
    except Exception:
        # no current installation, unsupported platform, ...
        return False


def get_python_info() -> dict:
    """Report IDAPython's sys/env state, as seen from inside IDA.

    Raises:
        PythonNotFoundError: if idat could not be run.
    """
    try:
        info = run_py_in_current_idapython(GET_PYTHON_INFO_PY)
    except RuntimeError as e:
        raise PythonNotFoundError(
            "failed to run idat to detect IDA's Python interpreter. "
            "If you already know the interpreter path, set HCLI_CURRENT_IDA_PYTHON_EXE=/path/to/python and retry."
        ) from e

    logger.debug("IDA Python info: %s", info)
    return info


def find_python_executable() -> Path:
    """Locate IDA's Python executable by asking IDAPython over idat.

    Raises:
        PythonNotFoundError: if idat could not be run, or its answer could not
            be resolved to an executable.
    """
    return _derive_python_exe(get_python_info())
