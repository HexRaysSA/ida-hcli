# see also hcli.lib.util.python
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from hcli.lib.ida import find_current_idat_executable

logger = logging.getLogger(__name__)


FIND_PYTHON_PY = """
# invoke like:
#
#     idat -a -A -c -t -L"/absolute/path/to/ida.log" -S"/absolute/path/to/idat-find-python.py"
#
# -a disable auto analysis
# -A autuonomous, no dialogs
# -c delete old database
# -t create an empty database
# -L"/absolute/path/to/ida.log"
# -S"/absolute/path/to/script.py"
#
# output like:
#
#     __hcli__:"/Users/user/code/hex-rays/ida-hcli/.venv/bin/python3"
import shutil
import sys
import json
print("__hcli__:" + json.dumps(shutil.which("python")))
sys.exit()
"""


def find_current_python_executable() -> Path:
    """find the python executable associated with the current IDA installation"""
    if "HCLI_CURRENT_IDA_PYTHON_EXE" in os.environ:
        return Path(os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"])

    idat_path = find_current_idat_executable()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        script_path = temp_path / "idat-sys-executable.py"
        log_path = temp_path / "ida.log"

        script_path.write_text(FIND_PYTHON_PY)

        cmd = [
            str(idat_path),
            "-a",  # disable auto analysis
            "-A",  # autonomous, no dialogs
            "-c",  # delete old database
            "-t",  # create an empty database
            f"-L{str(log_path.absolute())}",
            f"-S{str(script_path.absolute())}",
        ]

        _ = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.debug(f"idat command: {' '.join(cmd)}")

        if not log_path.exists():
            raise RuntimeError(f"Log file was not created: {log_path}")

        for line in log_path.read_text().splitlines():
            if not line.startswith("__hcli__:"):
                continue

            sys_executable = Path(json.loads(line[len("__hcli__:") :]))
            logger.debug("sys.executable: %s", sys_executable)
            return Path(sys_executable)

        raise RuntimeError("Could not find __hcli__: prefix in log output")


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
