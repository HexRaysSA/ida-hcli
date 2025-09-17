# see also hcli.lib.util.python
import logging
import os
import subprocess
from pathlib import Path

from hcli.env import ENV
from hcli.lib.ida import run_py_in_current_idapython

logger = logging.getLogger(__name__)


FIND_PYTHON_PY = """
# output like:
#
#     __hcli__:"/Users/user/.idapro/venv/bin/python"
import shutil
import sys
import json
print("__hcli__:" + json.dumps(shutil.which("python")))
sys.exit()
"""


def find_current_python_executable() -> Path:
    """find the python executable associated with the current IDA installation"""
    if ENV.HCLI_CURRENT_IDA_PYTHON_EXE is not None:
        return Path(ENV.HCLI_CURRENT_IDA_PYTHON_EXE)
    # duplicate here, because we prefer access through ENV
    # but tests might update env vars for the current process.
    exe = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE")
    if exe:
        return Path(exe)

    exe = run_py_in_current_idapython(FIND_PYTHON_PY)
    logger.debug("found python path: %s", exe)
    return Path(exe)


def does_current_ida_have_pip(python_exe: Path) -> bool:
    """Check if pip is available in the given Python executable."""
    try:
        process = subprocess.run(
            [str(python_exe), "-m", "pip", "help"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1.0
        )
        return process.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


class CantInstallPackagesError(ValueError): ...


def verify_pip_can_install_packages(python_exe: Path, packages: list[str]):
    """Check if the given Python packages (e.g., "foo>=v1.0,<3") can be installed.

    This allows pip to determine if there are any version conflicts
    """
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install", "--dry-run"] + packages,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        # error output might look like:
        #
        #     ❯ pip install --dry-run flare-capa==v1.0.0 flare-capa==v1.0.1
        #    Collecting flare-capa==v1.0.0
        #      Using cached flare-capa-1.0.0.tar.gz (62 kB)
        #      Installing build dependencies ... done
        #      Getting requirements to build wheel ... done
        #      Preparing metadata (pyproject.toml) ... done
        #    ERROR: Cannot install flare-capa==v1.0.0 and flare-capa==v1.0.1 because these package versions have conflicting dependencies.
        #
        #    The conflict is caused by:
        #        The user requested flare-capa==v1.0.0
        #        The user requested flare-capa==v1.0.1
        #
        #    To fix this you could try to:
        #    1. loosen the range of package versions you've specified
        #    2. remove package versions to allow pip to attempt to solve the dependency conflict
        #
        #    ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts
        logger.debug("can't install packages")
        logger.debug(stdout.decode())
        logger.debug(stderr.decode())
        raise CantInstallPackagesError(stdout.decode())


def pip_install_packages(python_exe: Path, packages: list[str]):
    """Install the given Python packages (e.g., "foo>=v1.0,<3")."""
    process = subprocess.run(
        [str(python_exe), "-m", "pip", "install"] + packages, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.stdout, process.stderr
    if process.returncode != 0:
        # error output might look like:
        #
        #     ❯ pip install --dry-run flare-capa==v1.0.0 flare-capa==v1.0.1
        #    Collecting flare-capa==v1.0.0
        #      Using cached flare-capa-1.0.0.tar.gz (62 kB)
        #      Installing build dependencies ... done
        #      Getting requirements to build wheel ... done
        #      Preparing metadata (pyproject.toml) ... done
        #    ERROR: Cannot install flare-capa==v1.0.0 and flare-capa==v1.0.1 because these package versions have conflicting dependencies.
        #
        #    The conflict is caused by:
        #        The user requested flare-capa==v1.0.0
        #        The user requested flare-capa==v1.0.1
        #
        #    To fix this you could try to:
        #    1. loosen the range of package versions you've specified
        #    2. remove package versions to allow pip to attempt to solve the dependency conflict
        #
        #    ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts
        logger.debug("can't install packages")
        logger.debug(stdout.decode())
        logger.debug(stderr.decode())
        raise CantInstallPackagesError(stdout.decode())


def pip_freeze(python_exe: Path):
    process = subprocess.run([str(python_exe), "-m", "pip", "freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, _ = process.stdout, process.stderr
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, [str(python_exe), "-m", "pip", "freeze"])
    return stdout.decode()
