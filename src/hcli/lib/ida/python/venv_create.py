"""Create a virtual environment for IDA and make IDA use it.

`hcli ida python create-environment` drives this.  The steps are separate
functions so each is testable on its own:

  1. `inspect_target`: is there already something at the target path?
  2. `determine_target_python_version`: which Python must the venv have?
     IDA's embedded Python (what idapyswitch registered) is authoritative.
  3. `plan_virtual_environment`: which tool and interpreter to use.  Pure.
  4. `create_virtual_environment`: run the plan and validate the result.
  5. `render_set_env_var_command` / `append_to_shell_profile` / `set_windows_user_env_var`:
     make `IDAPYTHON_VENV_EXECUTABLE` point at the new venv.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hcli.lib.ida.python import IdatProbe, _is_windows_store_shim, has_pip, probe_current_python_info
from hcli.lib.ida.python.environment import System, get_venv_python_path
from hcli.lib.venv import find_virtual_env_python, get_python_exe_candidates, probe_python_version

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^\d+\.\d+$")

TargetKind = Literal["missing", "healthy-venv", "wrong-version-venv", "broken-venv", "not-a-venv"]
VenvTool = Literal["uv", "venv"]


class VenvCreationError(RuntimeError):
    """Creating or validating the virtual environment failed."""


@dataclass(frozen=True)
class TargetInspection:
    """What already exists at the target path."""

    kind: TargetKind
    path: Path
    # the venv's interpreter, when the target is a venv with one
    python_exe: Path | None
    # the venv's major.minor, when it could be determined
    version: str | None
    # human-readable reason for `kind`
    reason: str


def inspect_target(target: Path, wanted_version: str | None) -> TargetInspection:
    """Classify whatever is at `target` so the caller can refuse to overwrite it.

    hcli never deletes or replaces an existing directory here; the classification
    tells the user what to do instead.
    """
    if not target.exists():
        return TargetInspection("missing", target, None, None, "does not exist")

    if not target.is_dir():
        return TargetInspection("not-a-venv", target, None, None, "exists and is not a directory")

    if not (target / "pyvenv.cfg").is_file():
        try:
            empty = not any(target.iterdir())
        except OSError:
            empty = False
        if empty:
            return TargetInspection("missing", target, None, None, "exists but is empty")
        return TargetInspection(
            "not-a-venv", target, None, None, "exists and contains files, but is not a virtual environment"
        )

    python_exe = find_virtual_env_python(target)
    if python_exe is None:
        return TargetInspection(
            "broken-venv", target, None, None, "is a virtual environment, but its interpreter is missing"
        )

    version = probe_python_version(python_exe)
    if version is None:
        return TargetInspection(
            "broken-venv", target, python_exe, None, f"is a virtual environment, but {python_exe} does not run"
        )

    if wanted_version is not None and version != wanted_version:
        return TargetInspection(
            "wrong-version-venv",
            target,
            python_exe,
            version,
            f"is a Python {version} virtual environment, but IDA runs Python {wanted_version}",
        )

    if not has_pip(python_exe):
        return TargetInspection(
            "broken-venv", target, python_exe, version, "is a virtual environment, but pip is not installed in it"
        )

    return TargetInspection("healthy-venv", target, python_exe, version, f"is a Python {version} virtual environment")


@dataclass(frozen=True)
class TargetPythonVersion:
    version: str
    # where the version came from: "idat probe" or "--python-version"
    source: str
    # the probe, when one was taken, so callers can reuse its interpreter path
    probe: IdatProbe | None


def validate_python_version_string(version: str) -> None:
    """Raises:
    ValueError: when `version` is not of the form `major.minor`.
    """
    if not VERSION_RE.match(version):
        raise ValueError(f"expected a Python version like 3.12, got {version!r}")


def determine_target_python_version(explicit: str | None) -> TargetPythonVersion:
    """Decide which Python version the venv must use.

    IDA's embedded Python decides everything: a venv of any other version is
    useless to IDA.  So the idat probe wins when it works.  The explicit
    version is the fallback when idat can't run (no IDA, headless CI, broken
    registration) and a warning is left for the caller when both are known and
    disagree.

    Raises:
        ValueError: when `explicit` is malformed.
        VenvCreationError: when neither the probe nor `explicit` yields a version.
    """
    if explicit is not None:
        validate_python_version_string(explicit)

    probe: IdatProbe | None = None
    try:
        probe = probe_current_python_info()
    except Exception as e:
        logger.debug("idat probe failed while determining Python version: %s", e)

    if probe is not None:
        probed = f"{probe.version_major}.{probe.version_minor}"
        if explicit is not None and explicit != probed:
            logger.warning(
                "IDA runs Python %s, but --python-version %s was requested. Using %s. "
                "Run idapyswitch first if IDA must use Python %s.",
                probed,
                explicit,
                probed,
                explicit,
            )
        return TargetPythonVersion(probed, "idat probe", probe)

    if explicit is not None:
        return TargetPythonVersion(explicit, "--python-version", None)

    raise VenvCreationError(
        "cannot determine which Python version IDA uses: idat did not run, and no --python-version was given. "
        "Pass --python-version X.Y with the Python version that idapyswitch registered for IDA."
    )


@dataclass(frozen=True)
class VenvPlan:
    target: Path
    version: str
    tool: VenvTool
    # the uv or python executable that runs the creation
    tool_exe: Path
    # the base interpreter the venv will be built from; for uv, None means "let uv pick/download"
    base_python: Path | None

    def build_command(self) -> list[str]:
        if self.tool == "uv":
            python_arg = str(self.base_python) if self.base_python else self.version
            return [str(self.tool_exe), "venv", "--seed", "--python", python_arg, str(self.target)]
        return [str(self.tool_exe), "-m", "venv", str(self.target)]

    def render_command(self) -> str:
        return " ".join(self.build_command())


def find_uv() -> Path | None:
    found = shutil.which("uv")
    return Path(found) if found else None


def find_python_on_path(version: str) -> Path | None:
    """Find a `python{version}` (or `python3`/`python`) on PATH that actually is `version`.

    The Windows Store `python.exe` alias is skipped: it's a stub that opens the
    Store rather than an interpreter.
    """
    names = [f"python{version}", "python3", "python"]
    seen: set[str] = set()
    for name in names:
        found = shutil.which(name)
        if not found or found in seen:
            continue
        seen.add(found)
        if _is_windows_store_shim(found):
            continue
        if probe_python_version(Path(found)) == version:
            return Path(found)
    return None


def get_registered_python_exe(probe: IdatProbe | None) -> Path | None:
    """The interpreter of the Python installation that IDA loads, per the idat probe.

    Building the venv from this interpreter guarantees the version matches and
    keeps the venv's `home` pointing at the same installation idapyswitch chose.
    Only base installations count: if IDA is already running inside a venv, its
    base_prefix is used instead.
    """
    if probe is None:
        return None

    prefix = probe.base_prefix or probe.prefix
    if not prefix:
        return None

    if probe.executable and not probe.virtual_env:
        exe = Path(probe.executable)
        if exe.is_file() and not _is_windows_store_shim(str(exe)):
            return exe

    version = f"{probe.version_major}.{probe.version_minor}"
    for candidate in get_python_exe_candidates(Path(prefix), version):
        if candidate.is_file() and not _is_windows_store_shim(str(candidate)):
            return candidate

    return None


def plan_virtual_environment(
    target: Path,
    version: str,
    *,
    registered_python: Path | None,
    uv_exe: Path | None,
    path_python: Path | None,
) -> VenvPlan:
    """Choose how to build the venv.  Pure: callers gather the inputs.

    uv is preferred because `uv venv --seed` always produces a venv with pip
    and can download the needed Python if none is installed.  Without uv,
    the stdlib `venv` module is used with an interpreter of the right version:
    the one IDA loads when known, otherwise one from PATH.

    Raises:
        VenvCreationError: when there's no uv and no interpreter of `version`.
    """
    base = registered_python or path_python

    if uv_exe is not None:
        return VenvPlan(target=target, version=version, tool="uv", tool_exe=uv_exe, base_python=base)

    if base is not None:
        return VenvPlan(target=target, version=version, tool="venv", tool_exe=base, base_python=base)

    raise VenvCreationError(
        f"no Python {version} interpreter found on PATH, and uv is not installed. "
        f"Install uv (https://docs.astral.sh/uv/) or Python {version}, then try again."
    )


def create_virtual_environment(plan: VenvPlan, system: System) -> Path:
    """Run the plan, then check the venv has the right version and pip.

    Returns the venv's interpreter.

    Raises:
        VenvCreationError: when the tool fails or the result doesn't validate.
    """
    plan.target.parent.mkdir(parents=True, exist_ok=True)

    command = plan.build_command()
    logger.debug("creating virtual environment: %s", " ".join(command))
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600.0, check=False)
    except (subprocess.SubprocessError, OSError) as e:
        raise VenvCreationError(f"failed to run {command[0]}: {e}") from e

    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise VenvCreationError(f"`{plan.render_command()}` failed with exit code {result.returncode}:\n{output}")

    if plan.tool == "venv":
        python_exe = get_venv_python_path(plan.target, system)
        ensurepip = [str(python_exe), "-m", "ensurepip", "--upgrade"]
        pip_result = subprocess.run(ensurepip, capture_output=True, text=True, timeout=600.0, check=False)
        if pip_result.returncode != 0:
            output = (pip_result.stderr or pip_result.stdout or "").strip()
            raise VenvCreationError(f"`{' '.join(ensurepip)}` failed with exit code {pip_result.returncode}:\n{output}")

    return validate_created_virtual_environment(plan.target, plan.version)


def validate_created_virtual_environment(target: Path, version: str) -> Path:
    """Raises:
    VenvCreationError: when `target` lacks pyvenv.cfg, a working interpreter of `version`, or pip.
    """
    if not (target / "pyvenv.cfg").is_file():
        raise VenvCreationError(f"{target} was created but has no pyvenv.cfg, so it is not a virtual environment")

    python_exe = find_virtual_env_python(target)
    if python_exe is None:
        raise VenvCreationError(f"{target} was created but contains no Python interpreter")

    actual = probe_python_version(python_exe)
    if actual != version:
        raise VenvCreationError(
            f"{target} was created with Python {actual or 'unknown'}, but Python {version} was required"
        )

    if not has_pip(python_exe):
        raise VenvCreationError(f"{target} was created but pip is not available in {python_exe}")

    return python_exe


ShellKind = Literal["bash", "zsh", "fish", "sh", "unknown"]


def detect_shell(shell_env: str | None) -> ShellKind:
    if not shell_env:
        return "unknown"
    name = Path(shell_env).name
    if name in ("bash", "zsh", "fish", "sh"):
        return name  # type: ignore[return-value]
    return "unknown"


def get_shell_profile_path(shell: ShellKind, home: Path) -> Path | None:
    """The file where an exported variable persists for `shell`, or None when unknown."""
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    if shell == "sh":
        return home / ".profile"
    return None


def render_profile_line(name: str, value: str, shell: ShellKind) -> str:
    if shell == "fish":
        return f'set -gx {name} "{value}"'
    return f'export {name}="{value}"'


def append_to_shell_profile(profile: Path, line: str) -> bool:
    """Append `line` to `profile` unless it's already there.  Returns whether anything was written."""
    existing = ""
    if profile.is_file():
        existing = profile.read_text(encoding="utf-8", errors="replace")
        if line in existing.splitlines():
            return False

    profile.parent.mkdir(parents=True, exist_ok=True)
    with profile.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return True


def set_windows_user_env_var(name: str, value: str) -> None:
    """Persist a user environment variable via setx.

    Raises:
        VenvCreationError: when setx fails.
    """
    command = ["setx", name, value]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60.0, check=False)
    except (subprocess.SubprocessError, OSError) as e:
        raise VenvCreationError(f"failed to run setx: {e}") from e
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise VenvCreationError(f"setx failed with exit code {result.returncode}: {output}")
    os.environ[name] = value
