"""Inspect IDA's Python environment and check it against the recommended setup.

The recommended setup has four parts: IDA's Python is a virtual environment,
that venv has pip, `IDAPYTHON_VENV_EXECUTABLE` points at the venv's
interpreter, and the venv's Python version matches the libpython that
idapyswitch registered.  Anything else is a "non-recommended" setup, and each
way it can differ is a distinct finding here.

Two layers:

  - `PythonEnvironmentState` is a plain record of facts about the environment,
    gathered once by `collect_python_environment_state`.
  - `check_python_environment` turns a state into `EnvironmentFinding`s without
    touching the file system or spawning processes, so it's easy to test.

The `collect_*` functions and report models further down are shared by
`hcli ida python explain-environment` (which shows raw state) and
`hcli ida python doctor` (which interprets it).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import stderr_console
from hcli.lib.ida import (
    detect_binary_arch,
    find_current_ida_executable,
    find_current_ida_platform,
    find_standard_installations,
    get_ida_user_dir,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
    resolve_current_ida_install_directory,
    resolve_current_ida_version,
)
from hcli.lib.ida.python import (
    CantInstallPackagesError,
    IdatProbe,
    PythonNotFoundError,
    ResolvedPython,
    _get_venv_root_from_python,
    _is_windows_store_shim,
    _normalize_path,
    find_current_python_executable,
    find_python_version_mismatches,
    has_pip,
    is_externally_managed,
    probe_current_python_info,
    resolve_current_python,
)
from hcli.lib.util.io import get_os
from hcli.lib.venv import (
    find_candidate_virtual_envs,
    get_virtual_env_version,
    is_uv_cache_virtual_env,
    parse_pyvenv_cfg,
    probe_python_version,
    resolve_user_virtual_env,
)

logger = logging.getLogger(__name__)

Severity = Literal["error", "warning"]
System = Literal["windows", "mac", "linux"]

# Fragments of idapythonrc.py that activate a virtualenv at IDA startup, per the
# community recipe (site.addsitedir) and the virtualenv-provided activate_this.py.
IDAPYTHONRC_VENV_MARKERS = ("addsitedir", "activate_this")

HOMEBREW_PREFIXES = ("/opt/homebrew", "/usr/local/Cellar", "/home/linuxbrew/.linuxbrew")


@dataclass(frozen=True)
class EnvironmentFinding:
    """One way the environment differs from the recommended setup."""

    # machine-readable slug, like "no-venv" or "version-mismatch"
    id: str
    severity: Severity
    # one line
    summary: str
    # multi-line explanation of why this matters
    detail: str
    # what the user should do, with concrete commands where possible
    fix_hint: str


@dataclass(frozen=True)
class PythonEnvironmentState:
    """Facts about IDA's Python environment, as inputs to the health checks.

    Built by `collect_python_environment_state` in production; constructed
    directly in tests.  `None` means "unknown", and checks that need the value
    are skipped rather than guessed.
    """

    # the interpreter HCLI would install plugin dependencies with
    python_exe: Path
    # what selected it, like "$IDAPYTHON_VENV_EXECUTABLE" (see ResolvedPython.source)
    source: str
    system: System
    idausr: Path | None
    # the virtualenv containing python_exe, when it's a venv interpreter
    venv_root: Path | None
    # whether `import pip` works in python_exe; None when not probed
    pip_available: bool | None
    # major.minor of python_exe
    python_version: str | None
    # major.minor of IDA's embedded Python, from the idat probe (what idapyswitch registered)
    ida_python_version: str | None
    # PEP 668 marker applies to python_exe (a base interpreter, not a venv)
    externally_managed: bool
    # venv_root is a `uv run --with` overlay that vanishes when uv exits
    uv_ephemeral: bool
    # $IDAPYTHON_VENV_EXECUTABLE as HCLI sees it
    idapython_venv_executable: Path | None
    # $VIRTUAL_ENV as HCLI sees it, excluding HCLI's own venv and uv overlays
    shell_virtual_env: Path | None
    idapythonrc_path: Path | None
    idapythonrc_activates_venv: bool
    # sys.base_prefix inside IDA, when probed
    base_prefix: Path | None
    conda: bool


def is_conda_prefix(python_exe: Path) -> bool:
    """Whether the interpreter belongs to a conda environment.

    Conda environments are not PEP 405 venvs (no pyvenv.cfg), but they always
    carry a `conda-meta` directory at the prefix root.
    """
    candidates = [python_exe.parent, python_exe.parent.parent]
    return any((prefix / "conda-meta").is_dir() for prefix in candidates)


def is_homebrew_path(path: Path | None) -> bool:
    if path is None:
        return False
    candidates = {path.as_posix()}
    try:
        candidates.add(path.resolve().as_posix())
    except OSError:
        pass
    return any(candidate.startswith(prefix) for candidate in candidates for prefix in HOMEBREW_PREFIXES)


def does_idapythonrc_activate_venv(idapythonrc: Path) -> bool:
    """Whether the startup script appears to activate a virtualenv.

    This is a text search, not an analysis: idapythonrc.py is arbitrary Python
    and the recipe users copy varies.  A match is a strong hint, not proof.
    """
    try:
        text = idapythonrc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in IDAPYTHONRC_VENV_MARKERS)


def get_system() -> System:
    os_ = get_os()
    if os_ == "windows":
        return "windows"
    if os_ == "mac":
        return "mac"
    return "linux"


def get_recommended_venv_dir(idausr: Path | None) -> Path:
    """Where HCLI creates and looks for IDA's virtualenv by default: $IDAUSR/venv."""
    if idausr is None:
        return Path("~/.idapro/venv")
    return idausr / "venv"


def get_venv_python_path(venv_root: Path, system: System) -> Path:
    if system == "windows":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def render_set_env_var_command(name: str, value: str, system: System) -> str:
    """The shell command that sets an environment variable persistently for the user.

    `setx` writes the user's registry environment, so it survives new
    terminals and reaches IDA launched from the Start menu.  On POSIX, this is
    the line to add to a shell profile.
    """
    if system == "windows":
        return f'setx {name} "{value}"'
    return f'export {name}="{value}"'


def _get_ida_probe_for_state(resolved: ResolvedPython, probe_ida: bool) -> IdatProbe | None:
    """IDA's own view of its Python, for the version-match check.

    Resolution via the idat probe already carries it.  Resolution via
    `$IDAPYTHON_VENV_EXECUTABLE` skips the probe, so when the caller can afford
    it (`probe_ida`) we run the probe now, since the recommended setup's one
    remaining failure mode is that variable pointing at a venv of the wrong
    version.  `$HCLI_CURRENT_IDA_PYTHON_EXE` is HCLI's own override and is
    taken at face value: it exists so the user (or a test harness) can say
    exactly which interpreter to use without IDA being consulted.
    """
    if resolved.probe is not None:
        return resolved.probe

    if not probe_ida or resolved.source != "$IDAPYTHON_VENV_EXECUTABLE":
        return None

    try:
        return probe_current_python_info()
    except Exception as e:
        logger.debug("idat probe unavailable for environment check: %s", e)
        return None


def collect_python_environment_state(
    resolved: ResolvedPython,
    *,
    probe_ida: bool = True,
) -> PythonEnvironmentState:
    """Gather the facts about IDA's Python environment that the checks need.

    This runs the interpreter once or twice (version, pip) and may launch idat
    once when `probe_ida` is set and IDA's version isn't already known; see
    `_get_ida_probe_for_state`.  Everything is best-effort: what can't be
    determined is recorded as unknown.
    """
    python_exe = resolved.exe
    system = get_system()

    idausr: Path | None
    try:
        idausr = get_ida_user_dir()
    except ValueError:
        idausr = None

    venv_root = _get_venv_root_from_python(str(python_exe))
    externally_managed = is_externally_managed(resolved)

    # pip can't be used in an externally-managed base interpreter regardless,
    # so don't spend a process launch learning whether it's importable.
    pip_available: bool | None = None
    if not externally_managed:
        pip_available = has_pip(python_exe)

    python_version = probe_python_version(python_exe)

    probe = _get_ida_probe_for_state(resolved, probe_ida)
    ida_python_version = f"{probe.version_major}.{probe.version_minor}" if probe else None
    base_prefix = Path(probe.base_prefix) if probe and probe.base_prefix else None

    uv_ephemeral = venv_root is not None and is_uv_cache_virtual_env(venv_root)

    raw_venv_exe = ENV.IDAPYTHON_VENV_EXECUTABLE
    idapython_venv_executable = Path(raw_venv_exe) if raw_venv_exe else None

    shell_virtual_env = resolve_user_virtual_env()

    idapythonrc_path = idausr / "idapythonrc.py" if idausr else None
    if idapythonrc_path is not None and not idapythonrc_path.is_file():
        idapythonrc_path = None
    idapythonrc_activates_venv = idapythonrc_path is not None and does_idapythonrc_activate_venv(idapythonrc_path)

    return PythonEnvironmentState(
        python_exe=python_exe,
        source=resolved.source,
        system=system,
        idausr=idausr,
        venv_root=venv_root,
        pip_available=pip_available,
        python_version=python_version,
        ida_python_version=ida_python_version,
        externally_managed=externally_managed,
        uv_ephemeral=uv_ephemeral,
        idapython_venv_executable=idapython_venv_executable,
        shell_virtual_env=shell_virtual_env,
        idapythonrc_path=idapythonrc_path,
        idapythonrc_activates_venv=idapythonrc_activates_venv,
        base_prefix=base_prefix,
        conda=is_conda_prefix(python_exe),
    )


def _same_venv(a: Path | None, b: Path | None) -> bool:
    if a is None or b is None:
        return False
    return _normalize_path(str(a)) == _normalize_path(str(b))


def venv_executable_points_at(state: PythonEnvironmentState) -> bool:
    """Whether $IDAPYTHON_VENV_EXECUTABLE names an interpreter inside `state.venv_root`."""
    if state.idapython_venv_executable is None or state.venv_root is None:
        return False
    requested_root = _get_venv_root_from_python(str(state.idapython_venv_executable))
    if requested_root is None:
        # the variable may name a python that doesn't exist yet; compare by layout instead
        requested_root = state.idapython_venv_executable.parent.parent
    return _same_venv(requested_root, state.venv_root)


def _render_create_environment_hint(state: PythonEnvironmentState) -> str:
    venv_dir = get_recommended_venv_dir(state.idausr)
    venv_python = get_venv_python_path(venv_dir, state.system)
    version = state.ida_python_version or state.python_version or "3.X"
    set_var = render_set_env_var_command("IDAPYTHON_VENV_EXECUTABLE", str(venv_python), state.system)
    return (
        f"Run `{ENV.HCLI_BINARY_NAME} ida python create-environment`. It creates a virtual environment at "
        f"{venv_dir} with Python {version} and configures IDA to use it.\n"
        f"Or do it yourself:\n"
        f"  uv venv --seed --python {version} {venv_dir}\n"
        f"  {set_var}"
    )


def check_python_environment(state: PythonEnvironmentState) -> list[EnvironmentFinding]:
    """Compare the environment against the recommended setup.

    Pure: reads only `state`.  Errors are conditions under which installing
    plugin dependencies is pointless or destructive; warnings are setups that
    work today but that we don't recommend.
    """
    findings: list[EnvironmentFinding] = []
    exe = str(state.python_exe)
    doctor = f"{ENV.HCLI_BINARY_NAME} ida python doctor"

    if state.uv_ephemeral:
        findings.append(
            EnvironmentFinding(
                id="uv-ephemeral",
                severity="error",
                summary=f"IDA's Python resolved to a temporary uv environment: {state.venv_root}",
                detail=(
                    "`uv run --with` creates this virtualenv for one command and discards it afterwards. "
                    "IDA never loads it. Packages installed into it are lost when the command exits."
                ),
                fix_hint=(
                    f"Install HCLI permanently, for example with `uv tool install ida-hcli`. "
                    f"Or set $IDAPYTHON_VENV_EXECUTABLE to IDA's real virtualenv.\n"
                    f"{_render_create_environment_hint(state)}"
                ),
            )
        )

    if state.venv_root is None:
        summary = f"IDA's Python is not a virtual environment: {exe}"
        detail = (
            "IDA loads a global Python (system, Homebrew, python.org, or Windows Store). Plugin dependencies "
            "installed there mix with OS packages and may need administrator rights. Debian, Ubuntu 24.04+, and "
            "Homebrew refuse such installs (PEP 668). A dedicated virtual environment avoids these problems."
        )
        if state.conda:
            detail += (
                "\nThis looks like a conda environment. conda environments have no pyvenv.cfg, so HCLI cannot "
                "check or manage them like a virtualenv."
            )
        findings.append(
            EnvironmentFinding(
                id="no-venv",
                severity="error",
                summary=summary,
                detail=detail,
                fix_hint=_render_create_environment_hint(state),
            )
        )

    if state.externally_managed:
        findings.append(
            EnvironmentFinding(
                id="externally-managed",
                severity="error",
                summary=f"IDA's Python is externally managed (PEP 668): {exe}",
                detail=(
                    "The distributor marked this Python as EXTERNALLY-MANAGED, so pip refuses to install packages "
                    "into it. Do not remove the marker: OS updates can overwrite or break packages that pip "
                    "installs there."
                ),
                fix_hint=_render_create_environment_hint(state),
            )
        )

    if state.pip_available is False:
        venv_hint = ""
        if state.venv_root is not None:
            venv_hint = (
                f"If you created this virtualenv with `uv venv` without `--seed`, recreate it with "
                f"`uv venv --seed --python {state.python_version or '3.X'} {state.venv_root}`. "
                f"Or add pip with `{exe} -m ensurepip --upgrade`."
            )
        else:
            venv_hint = f"Install pip with `{exe} -m ensurepip --upgrade`. Better: use a virtual environment."
        findings.append(
            EnvironmentFinding(
                id="no-pip",
                severity="error",
                summary=f"pip is not available in IDA's Python: {exe}",
                detail=(
                    "HCLI installs plugin dependencies with pip inside IDA's Python. Without pip, HCLI cannot "
                    "install any plugin that has Python dependencies."
                ),
                fix_hint=venv_hint,
            )
        )

    if (
        state.python_version is not None
        and state.ida_python_version is not None
        and state.python_version != state.ida_python_version
    ):
        findings.append(
            EnvironmentFinding(
                id="version-mismatch",
                severity="error",
                summary=(
                    f"Python version mismatch: IDA runs Python {state.ida_python_version}, "
                    f"but HCLI would install dependencies for Python {state.python_version} ({exe})"
                ),
                detail=(
                    "idapyswitch selects the libpython that IDA loads, and that alone sets the Python version "
                    "inside IDA. A virtualenv only changes sys.path, not the version. Packages installed for "
                    f"{state.python_version} go to a site-packages directory that IDA's Python "
                    f"{state.ida_python_version} never reads."
                ),
                fix_hint=(
                    f"Run idapyswitch to select a Python {state.python_version} installation for IDA. "
                    f"Or recreate the virtualenv with Python {state.ida_python_version}: "
                    f"`{ENV.HCLI_BINARY_NAME} ida python create-environment` selects the matching version."
                ),
            )
        )

    if state.venv_root is not None and not venv_executable_points_at(state):
        venv_python = get_venv_python_path(state.venv_root, state.system)
        set_var = render_set_env_var_command("IDAPYTHON_VENV_EXECUTABLE", str(venv_python), state.system)
        if state.idapython_venv_executable is None:
            summary = "$IDAPYTHON_VENV_EXECUTABLE is not set"
        else:
            summary = (
                f"$IDAPYTHON_VENV_EXECUTABLE points to a different environment: {state.idapython_venv_executable} "
                f"(HCLI resolved {exe})"
            )
        findings.append(
            EnvironmentFinding(
                id="no-venv-exe-var",
                severity="warning",
                summary=summary,
                detail=(
                    "IDAPYTHON_VENV_EXECUTABLE tells IDA which virtualenv to use, however IDA starts (terminal, "
                    "Dock, or file association). Other methods, such as idapythonrc.py or an activated shell, work "
                    "only in some situations. HCLI then has to guess which environment IDA uses."
                ),
                fix_hint=f"Set it for your user account, then restart IDA:\n  {set_var}",
            )
        )

    if state.idapythonrc_activates_venv:
        findings.append(
            EnvironmentFinding(
                id="idapythonrc-venv",
                severity="warning",
                summary=f"{state.idapythonrc_path} appears to activate a virtualenv at startup",
                detail=(
                    "A virtualenv activated from idapythonrc.py works in interactive IDA only. idat and HCLI's "
                    "environment probe see the base interpreter, so HCLI can install packages where IDA never "
                    "looks. The script is arbitrary Python, so HCLI cannot tell which virtualenv it selects."
                ),
                fix_hint=(
                    "Set $IDAPYTHON_VENV_EXECUTABLE to the virtualenv's interpreter. "
                    "Then remove the activation code from idapythonrc.py."
                ),
            )
        )

    if findings:
        logger.debug("environment findings: %s (see `%s`)", [f.id for f in findings], doctor)

    return findings


def has_errors(findings: list[EnvironmentFinding]) -> bool:
    return any(finding.severity == "error" for finding in findings)


def format_environment_warnings(findings: list[EnvironmentFinding]) -> str:
    """Render findings as a compact rich-markup block for stderr, or "" when there are none."""
    if not findings:
        return ""

    errors = [f for f in findings if f.severity == "error"]
    if errors:
        header = "[bold red]Error:[/bold red] HCLI cannot install plugin dependencies into IDA's Python environment."
    else:
        header = "[bold yellow]Warning:[/bold yellow] IDA's Python environment is not the recommended setup."

    lines = [header]
    for finding in findings:
        tag = "[red]error[/red]  " if finding.severity == "error" else "[yellow]warning[/yellow]"
        lines.append(f"  {tag} {escape(finding.summary)}")

    lines.append(f"Run `{ENV.HCLI_BINARY_NAME} ida python doctor` for details and fixes.")
    return "\n".join(lines)


def format_environment_findings_plain(findings: list[EnvironmentFinding]) -> str:
    """Render findings without markup, for exception messages."""
    lines = []
    for finding in findings:
        lines.append(f"- [{finding.severity}] {finding.summary}")
    lines.append(f"Run '{ENV.HCLI_BINARY_NAME} ida python doctor' for details and fixes.")
    return "\n".join(lines)


class PythonEnvironmentError(CantInstallPackagesError):
    """IDA's Python environment has error-level findings, so packages must not be installed into it."""

    def __init__(self, findings: list[EnvironmentFinding]):
        self.findings = findings
        super().__init__(
            "HCLI cannot install plugin dependencies into IDA's Python environment:\n"
            + format_environment_findings_plain(findings)
        )


def validate_python_environment(resolved: ResolvedPython) -> None:
    """Check the environment before installing packages: warn on stderr, reject on errors.

    Raises:
        PythonEnvironmentError: when any finding has severity "error".
    """
    try:
        state = collect_python_environment_state(resolved, probe_ida=True)
    except Exception as e:
        # the check must never be the reason an install fails for an unrelated cause
        logger.debug("python environment check skipped: %s", e)
        return

    findings = check_python_environment(state)
    if not findings:
        return

    stderr_console.print(format_environment_warnings(findings), highlight=False)

    if has_errors(findings):
        raise PythonEnvironmentError(findings)


def warn_python_environment(resolved: ResolvedPython) -> None:
    """Warn on stderr about a non-recommended environment, without ever blocking.

    Does not launch idat: this runs on every `hcli ida python exec`, where a
    multi-second probe is not acceptable.
    """
    try:
        state = collect_python_environment_state(resolved, probe_ida=False)
        findings = check_python_environment(state)
    except Exception as e:
        logger.debug("python environment check skipped: %s", e)
        return

    warning = format_environment_warnings(findings)
    if warning:
        stderr_console.print(warning, highlight=False)


@dataclass(frozen=True)
class SetupPattern:
    """A known configuration, named so users recognize their starting point."""

    id: str
    name: str
    description: str


def identify_setup_pattern(state: PythonEnvironmentState) -> SetupPattern:
    """Name the configuration `state` most resembles.

    Patterns are checked from most to least specific.  The result describes the
    starting point; the findings describe what to change.
    """
    findings = check_python_environment(state)
    var_ok = venv_executable_points_at(state)

    if state.venv_root is not None and var_ok and not findings:
        return SetupPattern(
            id="properly-configured",
            name="Properly configured",
            description=(
                "IDA loads a virtual environment selected by $IDAPYTHON_VENV_EXECUTABLE. It has pip, and its "
                "Python version matches the one idapyswitch registered."
            ),
        )

    if state.uv_ephemeral:
        return SetupPattern(
            id="uv-ephemeral",
            name="Temporary uv environment",
            description=(
                "HCLI runs under `uv run --with`. The virtualenv it sees is uv's temporary overlay, not IDA's "
                "environment."
            ),
        )

    if state.idapythonrc_activates_venv:
        return SetupPattern(
            id="idapythonrc-venv",
            name="idapythonrc.py virtualenv",
            description=(
                "idapythonrc.py activates a virtualenv when IDA starts. This works in interactive IDA, but idat "
                "and HCLI do not see it."
            ),
        )

    if state.venv_root is not None and state.shell_virtual_env is not None and state.idapython_venv_executable is None:
        return SetupPattern(
            id="shell-activated-venv",
            name="Shell-activated virtualenv",
            description=(
                "A virtualenv is active in this shell ($VIRTUAL_ENV). IDA uses it only when started from such a "
                "shell. Desktop launchers and file associations start IDA with the base Python."
            ),
        )

    if state.venv_root is not None and var_ok:
        return SetupPattern(
            id="configured-with-problems",
            name="Configured virtualenv with problems",
            description=(
                "$IDAPYTHON_VENV_EXECUTABLE selects a virtualenv for IDA, but that environment has the problems "
                "listed below."
            ),
        )

    if state.venv_root is not None:
        return SetupPattern(
            id="venv-not-configured",
            name="Virtualenv not configured for IDA",
            description=(
                "HCLI found a virtualenv, but $IDAPYTHON_VENV_EXECUTABLE does not select it. IDA may load a "
                "different environment than the one HCLI installs into."
            ),
        )

    if _is_windows_store_shim(str(state.python_exe)):
        return SetupPattern(
            id="windows-store",
            name="Windows Store Python",
            description=(
                "IDA's Python resolves to the Microsoft Store app-execution alias. This alias cannot install "
                "packages, and it may not be the Python that idapyswitch registered."
            ),
        )

    if state.conda:
        return SetupPattern(
            id="conda",
            name="Anaconda/conda Python",
            description=(
                "IDA loads a conda environment. conda environments are not standard virtualenvs, so HCLI cannot "
                "check them. Installing packages mixes pip and conda packages."
            ),
        )

    if is_homebrew_path(state.python_exe) or is_homebrew_path(state.base_prefix):
        return SetupPattern(
            id="homebrew",
            name="Homebrew Python",
            description=(
                "IDA loads a Homebrew Python. Homebrew upgrades replace the interpreter, which breaks the "
                "idapyswitch registration. Homebrew also marks it externally managed, so pip refuses to install "
                "into it."
            ),
        )

    return SetupPattern(
        id="default",
        name="Default (no setup)",
        description=(
            "IDA loads a global Python. There is no virtual environment, and $IDAPYTHON_VENV_EXECUTABLE is not "
            "set. This is how a fresh IDA installation looks, and it is the most common cause of plugin "
            "installation problems."
        ),
    )


def finding_to_dict(finding: EnvironmentFinding) -> dict[str, str]:
    return asdict(finding)


# ---------------------------------------------------------------------------
# Environment report: raw state shared by explain-environment and doctor.
# ---------------------------------------------------------------------------


class InstallationEntry(BaseModel):
    path: str
    version: str | None


class KnownInstallationsReport(BaseModel):
    installations: list[InstallationEntry]
    error: str | None


class SelectedInstallationReport(BaseModel):
    install_dir: str | None
    install_dir_source: str | None
    install_dir_error: str | None


class ArchitectureAndVersionReport(BaseModel):
    ida_binary: str | None
    ida_binary_error: str | None
    binary_arch: str | None
    binary_arch_error: str | None
    platform: str | None
    platform_error: str | None
    ida_version: str | None
    ida_version_source: str | None
    ida_version_error: str | None


class CandidateVirtualEnv(BaseModel):
    path: str
    source: str


class PythonEnvironmentReport(BaseModel):
    virtual_env: str | None
    virtual_env_is_uv_cache: bool
    user_virtual_env: str | None
    candidate_virtual_envs: list[CandidateVirtualEnv]
    idapython_venv_executable: str | None
    idapython_venv_executable_exists: bool | None
    python_exe: str | None
    python_exe_source: str | None
    python_exe_error: str | None
    externally_managed: bool
    idat_probe: IdatProbe | None
    idat_probe_error: str | None


class IdaPythonVirtualEnvReport(BaseModel):
    venv: str
    home: str | None
    system_site_packages: str | None
    python_version: str | None


class PythonVersionReport(BaseModel):
    final_python_exe: str | None
    final_python_exe_error: str | None
    probed_version: str | None
    probed_version_error: str | None
    hcli_interpreter_version: str
    hcli_interpreter_path: str


class PythonVersionMismatchEntry(BaseModel):
    ida_version: str
    other_version: str
    other_path: str
    other_source: str


class EnvironmentNote(BaseModel):
    kind: Literal["diagnostic", "hint", "warning"]
    text: str


class EnvironmentReport(BaseModel):
    experimental: bool = True
    known_installations: KnownInstallationsReport
    selected_installation: SelectedInstallationReport
    # the sections below need an installation directory, so they're absent when it can't be resolved.
    architecture_and_version: ArchitectureAndVersionReport | None
    python_environment: PythonEnvironmentReport | None
    idapython_virtualenv: IdaPythonVirtualEnvReport | None
    python_version: PythonVersionReport | None
    python_version_mismatches: list[PythonVersionMismatchEntry]
    python_version_mismatch_error: str | None
    notes: list[EnvironmentNote]


def collect_known_installations() -> KnownInstallationsReport:
    installations: list[InstallationEntry] = []
    error: str | None = None

    try:
        for path in sorted(find_standard_installations()):
            version = parse_version_from_ida_pro_py(path) or parse_version_from_dir_name(path) or None
            installations.append(InstallationEntry(path=str(path), version=version))
    except Exception as e:
        error = str(e)

    return KnownInstallationsReport(installations=installations, error=error)


def collect_selected_installation() -> SelectedInstallationReport:
    try:
        resolved = resolve_current_ida_install_directory()
    except Exception as e:
        return SelectedInstallationReport(
            install_dir=None,
            install_dir_source=None,
            install_dir_error=str(e),
        )

    return SelectedInstallationReport(
        install_dir=str(resolved.path),
        install_dir_source=resolved.source,
        install_dir_error=None,
    )


def collect_architecture_and_version() -> ArchitectureAndVersionReport:
    ida_binary: str | None = None
    ida_binary_error: str | None = None
    binary_arch: str | None = None
    binary_arch_error: str | None = None

    try:
        ida_binary_path = find_current_ida_executable()
        ida_binary = str(ida_binary_path)
    except Exception as e:
        ida_binary_error = str(e)
    else:
        try:
            binary_arch = detect_binary_arch(ida_binary_path)
        except Exception as e:
            binary_arch_error = str(e)

    platform: str | None = None
    platform_error: str | None = None
    try:
        platform = find_current_ida_platform()
    except Exception as e:
        platform_error = str(e)

    ida_version: str | None = None
    ida_version_source: str | None = None
    ida_version_error: str | None = None

    try:
        resolved_version = resolve_current_ida_version()
        ida_version = resolved_version.version
        ida_version_source = resolved_version.source
    except Exception as e:
        ida_version_error = str(e)

    return ArchitectureAndVersionReport(
        ida_binary=ida_binary,
        ida_binary_error=ida_binary_error,
        binary_arch=binary_arch,
        binary_arch_error=binary_arch_error,
        platform=platform,
        platform_error=platform_error,
        ida_version=ida_version,
        ida_version_source=ida_version_source,
        ida_version_error=ida_version_error,
    )


def collect_python_environment() -> PythonEnvironmentReport:
    process_virtual_env = os.environ.get("VIRTUAL_ENV")

    user_venv = resolve_user_virtual_env()

    candidate_virtual_envs = [
        CandidateVirtualEnv(path=str(candidate.path), source=candidate.source)
        for candidate in find_candidate_virtual_envs()
        if not is_uv_cache_virtual_env(candidate.path)
    ]

    idapython_venv_exe = os.environ.get("IDAPYTHON_VENV_EXECUTABLE") or ENV.IDAPYTHON_VENV_EXECUTABLE
    idapython_venv_executable = str(idapython_venv_exe) if idapython_venv_exe else None
    idapython_venv_executable_exists = Path(idapython_venv_exe).is_file() if idapython_venv_exe else None

    python_exe: str | None = None
    python_exe_source: str | None = None
    python_exe_error: str | None = None
    externally_managed = False
    idat_probe: IdatProbe | None = None
    idat_probe_error: str | None = None

    try:
        resolved = resolve_current_python()
    except PythonNotFoundError as e:
        python_exe_error = f"{type(e).__name__}: {e}"
        # resolution failed either because the probe failed or because no
        # interpreter could be derived from it; show the probe when available.
        try:
            idat_probe = probe_current_python_info()
        except Exception as probe_e:
            idat_probe_error = f"{type(probe_e).__name__}: {probe_e}"
    else:
        python_exe = str(resolved.exe)
        python_exe_source = resolved.source
        idat_probe = resolved.probe
        externally_managed = is_externally_managed(resolved)

    return PythonEnvironmentReport(
        virtual_env=process_virtual_env,
        virtual_env_is_uv_cache=process_virtual_env is not None and is_uv_cache_virtual_env(process_virtual_env),
        user_virtual_env=str(user_venv) if user_venv else None,
        candidate_virtual_envs=candidate_virtual_envs,
        idapython_venv_executable=idapython_venv_executable,
        idapython_venv_executable_exists=idapython_venv_executable_exists,
        python_exe=python_exe,
        python_exe_source=python_exe_source,
        python_exe_error=python_exe_error,
        externally_managed=externally_managed,
        idat_probe=idat_probe,
        idat_probe_error=idat_probe_error,
    )


def collect_idapython_virtualenv(probe: IdatProbe | None) -> IdaPythonVirtualEnvReport | None:
    ida_venv = probe.virtual_env if probe else None
    if not ida_venv:
        return None

    venv_path = Path(ida_venv)
    cfg = parse_pyvenv_cfg(venv_path / "pyvenv.cfg")

    return IdaPythonVirtualEnvReport(
        venv=str(venv_path),
        home=cfg.get("home"),
        system_site_packages=cfg.get("include-system-site-packages"),
        python_version=get_virtual_env_version(venv_path),
    )


def collect_python_version() -> PythonVersionReport:
    final_python_exe: str | None = None
    final_python_exe_error: str | None = None
    probed_version: str | None = None
    probed_version_error: str | None = None

    try:
        python_exe = find_current_python_executable()
    except Exception as e:
        final_python_exe_error = f"{type(e).__name__}: {e}"
    else:
        final_python_exe = str(python_exe)
        probed_version = probe_python_version(python_exe)
        if probed_version is None:
            probed_version_error = f"failed to run {python_exe}"

    return PythonVersionReport(
        final_python_exe=final_python_exe,
        final_python_exe_error=final_python_exe_error,
        probed_version=probed_version,
        probed_version_error=probed_version_error,
        hcli_interpreter_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        hcli_interpreter_path=sys.executable,
    )


def collect_python_version_mismatches(
    python_environment: PythonEnvironmentReport,
    python_version: PythonVersionReport,
) -> tuple[list[PythonVersionMismatchEntry], str | None]:
    """Find Python environments whose version disagrees with IDA's embedded Python.

    A virtualenv only redirects sys.path; it can't change the Python version IDA
    runs, which idapyswitch fixed when it registered a libpython. So a venv built
    for a different version silently can't provide packages to IDA.
    """
    probe = python_environment.idat_probe
    if probe is None:
        return [], None

    final_python_exe = Path(python_version.final_python_exe) if python_version.final_python_exe else None

    try:
        mismatches = find_python_version_mismatches(probe, final_python_exe)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"

    return [
        PythonVersionMismatchEntry(
            ida_version=mismatch.ida_version,
            other_version=mismatch.other_version,
            other_path=str(mismatch.other_path),
            other_source=mismatch.other_source,
        )
        for mismatch in mismatches
    ], None


def collect_notes(
    python_environment: PythonEnvironmentReport,
    idapython_virtualenv: IdaPythonVirtualEnvReport | None,
    python_version: PythonVersionReport,
) -> list[EnvironmentNote]:
    notes: list[EnvironmentNote] = []

    process_virtual_env = python_environment.virtual_env
    is_uv_cache = python_environment.virtual_env_is_uv_cache
    user_venv = python_environment.user_virtual_env
    is_hcli_own_venv = bool(process_virtual_env) and os.path.normcase(
        os.path.abspath(process_virtual_env or "")
    ) == os.path.normcase(os.path.abspath(sys.prefix))

    if is_uv_cache and user_venv:
        notes.append(
            EnvironmentNote(
                kind="diagnostic",
                text=f"$VIRTUAL_ENV is a uv cache overlay. Resolved user virtualenv: {user_venv}",
            )
        )
    elif is_uv_cache:
        notes.append(
            EnvironmentNote(
                kind="diagnostic",
                text=(
                    "$VIRTUAL_ENV is a uv cache overlay, not your virtualenv. No user virtualenvs were found on $PATH."
                ),
            )
        )
    elif process_virtual_env and is_hcli_own_venv:
        notes.append(
            EnvironmentNote(
                kind="diagnostic",
                text=(
                    f"$VIRTUAL_ENV ({process_virtual_env}) is the HCLI process environment, not the IDA Python "
                    f"environment. It is not used for plugin installation."
                ),
            )
        )
    elif process_virtual_env and not idapython_virtualenv:
        notes.append(
            EnvironmentNote(
                kind="diagnostic",
                text=(
                    f"$VIRTUAL_ENV is set ({process_virtual_env}) but was not detected inside IDA. "
                    f"To use this virtualenv with IDA, set $IDAPYTHON_VENV_EXECUTABLE to its interpreter."
                ),
            )
        )

    if not idapython_virtualenv:
        notes.append(
            EnvironmentNote(
                kind="hint",
                text=(
                    f"To use a virtualenv with IDA, run `{ENV.HCLI_BINARY_NAME} ida python create-environment`, "
                    f"or check the current setup with `{ENV.HCLI_BINARY_NAME} ida python doctor`."
                ),
            )
        )
    if not user_venv and not is_uv_cache and not idapython_virtualenv:
        notes.append(
            EnvironmentNote(
                kind="hint",
                text="To change IDA's Python, use idapyswitch to point at a different interpreter.",
            )
        )

    if python_environment.externally_managed:
        notes.append(
            EnvironmentNote(
                kind="warning",
                text=(
                    f"{python_environment.python_exe} is an externally-managed Python (PEP 668); pip will refuse "
                    "to install plugin dependencies into it directly. Point IDA at a virtual environment instead: "
                    f"`{ENV.HCLI_BINARY_NAME} ida python create-environment`."
                ),
            )
        )

    if python_version.probed_version:
        try:
            parts = python_version.probed_version.split(".")
            major, minor = int(parts[0]), int(parts[1])
            if (major, minor) <= (3, 9):
                notes.append(
                    EnvironmentNote(
                        kind="warning",
                        text=(
                            f"Python {python_version.probed_version} has reached end-of-life. "
                            "Many IDA plugins may not support it. "
                            "Consider upgrading to a newer Python and using idapyswitch to point IDA at it."
                        ),
                    )
                )
        except (ValueError, IndexError):
            pass

    return notes


def collect_environment_report() -> EnvironmentReport:
    known_installations = collect_known_installations()
    selected_installation = collect_selected_installation()

    if selected_installation.install_dir is None:
        return EnvironmentReport(
            known_installations=known_installations,
            selected_installation=selected_installation,
            architecture_and_version=None,
            python_environment=None,
            idapython_virtualenv=None,
            python_version=None,
            python_version_mismatches=[],
            python_version_mismatch_error=None,
            notes=[],
        )

    architecture_and_version = collect_architecture_and_version()
    python_environment = collect_python_environment()
    idapython_virtualenv = collect_idapython_virtualenv(python_environment.idat_probe)
    python_version = collect_python_version()
    python_version_mismatches, python_version_mismatch_error = collect_python_version_mismatches(
        python_environment, python_version
    )
    notes = collect_notes(python_environment, idapython_virtualenv, python_version)

    return EnvironmentReport(
        known_installations=known_installations,
        selected_installation=selected_installation,
        architecture_and_version=architecture_and_version,
        python_environment=python_environment,
        idapython_virtualenv=idapython_virtualenv,
        python_version=python_version,
        python_version_mismatches=python_version_mismatches,
        python_version_mismatch_error=python_version_mismatch_error,
        notes=notes,
    )
