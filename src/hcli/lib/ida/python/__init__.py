"""IDA's Python interpreter: finding it, and installing into it.

  paths.py    turn IDA's reported state into an executable path
  idat.py     ask IDAPython directly, over a headless idat run
  idapro.py   read IDA's configured libpython, without running IDA
  finder.py   pick a strategy and resolve the interpreter
  pip.py      drive pip against a resolved interpreter

see also hcli.lib.util.python
"""

from .finder import detect_current_ida_python_version, find_current_ida_python_executable
from .idat import GET_PYTHON_INFO_PY
from .paths import PythonNotFoundError, _derive_python_exe
from .pip import (
    PIP_OPTIONS_DEFAULT,
    CantInstallPackagesError,
    PipOptions,
    does_current_ida_have_pip,
    merge_bundle_pip_options,
    pip_freeze,
    pip_install_packages,
    run_pip,
    verify_pip_can_install_packages,
)

__all__ = [
    "GET_PYTHON_INFO_PY",
    "PIP_OPTIONS_DEFAULT",
    "CantInstallPackagesError",
    "PipOptions",
    "PythonNotFoundError",
    "_derive_python_exe",
    "detect_current_ida_python_version",
    "does_current_ida_have_pip",
    "find_current_ida_python_executable",
    "merge_bundle_pip_options",
    "pip_freeze",
    "pip_install_packages",
    "run_pip",
    "verify_pip_can_install_packages",
]
