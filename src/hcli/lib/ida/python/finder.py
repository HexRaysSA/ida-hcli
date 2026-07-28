"""Resolve which Python interpreter belongs to the current IDA installation.

Strategy order:

  1. HCLI_CURRENT_IDA_PYTHON_EXE, when the user has told us the answer
  2. idapro - reads IDA's configuration, cheap, no IDA process
  3. idat, when the installation ships one and idapro came up empty

idapro leads because it costs nothing and covers the venvs IDA resolves itself.
idat remains the authority on venvs that only exist inside IDA - an
idapythonrc.py that edits sys.path - so it stays as the fallback.
"""

import logging
import os
import subprocess
from pathlib import Path

from hcli.env import ENV

from . import idapro, idat
from .paths import PythonNotFoundError

logger = logging.getLogger(__name__)


def find_current_ida_python_executable() -> Path:
    """find the python executable associated with the current IDA installation"""
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    exe = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE")
    if exe:
        return Path(exe)
    if ENV.HCLI_CURRENT_IDA_PYTHON_EXE is not None:
        return Path(ENV.HCLI_CURRENT_IDA_PYTHON_EXE)

    try:
        return idapro.find_python_executable()
    except PythonNotFoundError as e:
        logger.debug("idapro detection failed, falling back to idat: %s", e)
        idapro_error = e

    if not idat.is_available():
        # Free/Home ship no idat, so IDA cannot be asked directly.
        logger.debug("no idat available")
        raise idapro_error

    try:
        return idat.find_python_executable()
    except PythonNotFoundError as idat_error:
        # A broken plugin can take idat down on startup, and #99 breaks it
        # outright on 9.2/Linux under a path with spaces.
        logger.debug("idat detection also failed: %s", idat_error)
        raise idapro_error from idat_error


def detect_current_ida_python_version() -> str:
    """Detect the major.minor Python version of the active IDA Python.

    Raises if detection fails rather than silently falling back to the
    hcli interpreter's version, which may differ from IDA's Python.
    """
    logger.debug("detecting IDA Python executable...")
    python_exe = find_current_ida_python_executable()
    logger.debug("found IDA Python executable: %s", python_exe)
    result = subprocess.run(
        [str(python_exe), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    version = result.stdout.strip()
    logger.debug("detected Python version: %s", version)
    return version
