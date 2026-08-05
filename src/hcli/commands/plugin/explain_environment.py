from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

import rich_click as click
from pydantic import BaseModel
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import console, print_json
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    detect_binary_arch,
    find_current_ida_executable,
    find_current_ida_install_directory,
    find_current_ida_platform,
    find_standard_installations,
    get_ida_config,
    get_ida_config_path,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
    run_py_in_current_idapython,
)
from hcli.lib.ida.python import (
    GET_PYTHON_INFO_PY,
    PythonNotFoundError,
    _derive_python_exe,
    detect_current_python_version,
    find_current_python_executable,
)
from hcli.lib.venv import find_candidate_virtual_envs, is_uv_cache_virtual_env, resolve_user_virtual_env


class InstallationEntry(BaseModel):
    path: str
    version: str | None = None


class KnownInstallationsReport(BaseModel):
    installations: list[InstallationEntry] = []
    error: str | None = None


class SelectedInstallationReport(BaseModel):
    install_dir: str | None = None
    install_dir_source: str | None = None
    install_dir_error: str | None = None
    resolved_dir: str | None = None
    resolved_dir_error: str | None = None


class ArchitectureAndVersionReport(BaseModel):
    ida_binary: str | None = None
    ida_binary_error: str | None = None
    binary_arch: str | None = None
    binary_arch_error: str | None = None
    platform: str | None = None
    platform_error: str | None = None
    ida_version: str | None = None
    ida_version_source: str | None = None
    ida_version_error: str | None = None


class CandidateVirtualEnv(BaseModel):
    path: str
    source: str


class IdatProbe(BaseModel):
    """What IDA's embedded Python reports about itself, via GET_PYTHON_INFO_PY."""

    frozen: bool = False
    prefix: str
    base_prefix: str
    executable: str | None = None
    virtual_env: str | None = None
    idapython_venv_executable: str | None = None
    version_major: int
    version_minor: int


class PythonEnvironmentReport(BaseModel):
    virtual_env: str | None = None
    virtual_env_is_uv_cache: bool = False
    user_virtual_env: str | None = None
    candidate_virtual_envs: list[CandidateVirtualEnv] = []
    python_exe: str | None = None
    python_exe_source: str | None = None
    idat_probe: IdatProbe | None = None
    idat_probe_error: str | None = None
    derived_exe: str | None = None
    derived_exe_error: str | None = None


class IdaPythonVirtualEnvReport(BaseModel):
    venv: str
    home: str | None = None
    system_site_packages: str | None = None


class PythonVersionReport(BaseModel):
    final_python_exe: str | None = None
    final_python_exe_error: str | None = None
    probed_version: str | None = None
    probed_version_error: str | None = None
    hcli_interpreter_version: str
    hcli_interpreter_path: str
    final_version: str | None = None
    final_version_error: str | None = None


class EnvironmentNote(BaseModel):
    kind: Literal["diagnostic", "hint", "warning"]
    text: str


class EnvironmentReport(BaseModel):
    experimental: bool = True
    known_installations: KnownInstallationsReport
    selected_installation: SelectedInstallationReport
    # the sections below need an installation directory, so they're absent when it can't be resolved.
    architecture_and_version: ArchitectureAndVersionReport | None = None
    python_environment: PythonEnvironmentReport | None = None
    idapython_virtualenv: IdaPythonVirtualEnvReport | None = None
    python_version: PythonVersionReport | None = None
    notes: list[EnvironmentNote] = []


def collect_known_installations() -> KnownInstallationsReport:
    report = KnownInstallationsReport()

    try:
        for path in sorted(find_standard_installations()):
            version = parse_version_from_ida_pro_py(path) or parse_version_from_dir_name(path) or None
            report.installations.append(InstallationEntry(path=str(path), version=version))
    except Exception as e:
        report.error = str(e)

    return report


def collect_selected_installation() -> SelectedInstallationReport:
    report = SelectedInstallationReport()

    env_install_dir = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR") or ENV.HCLI_CURRENT_IDA_INSTALL_DIR
    if env_install_dir:
        report.install_dir = str(env_install_dir)
        report.install_dir_source = "$HCLI_CURRENT_IDA_INSTALL_DIR"
    else:
        config_path = get_ida_config_path()
        try:
            config = get_ida_config()
            if config.paths.installation_directory:
                report.install_dir = str(config.paths.installation_directory)
                report.install_dir_source = str(config_path)
            else:
                report.install_dir_error = f"not configured in {config_path}"
        except Exception as e:
            report.install_dir_error = str(e)

    try:
        report.resolved_dir = str(find_current_ida_install_directory())
    except MissingCurrentInstallationDirectory as e:
        report.resolved_dir_error = str(e)

    return report


def collect_architecture_and_version(install_dir: Path) -> ArchitectureAndVersionReport:
    report = ArchitectureAndVersionReport()

    try:
        ida_binary = find_current_ida_executable()
        report.ida_binary = str(ida_binary)
    except Exception as e:
        report.ida_binary_error = str(e)
    else:
        try:
            report.binary_arch = detect_binary_arch(ida_binary)
        except Exception as e:
            report.binary_arch_error = str(e)

    try:
        report.platform = find_current_ida_platform()
    except Exception as e:
        report.platform_error = str(e)

    env_version = os.environ.get("HCLI_CURRENT_IDA_VERSION") or ENV.HCLI_CURRENT_IDA_VERSION
    if env_version:
        report.ida_version = env_version
        report.ida_version_source = "$HCLI_CURRENT_IDA_VERSION"
    else:
        sdk_version = parse_version_from_ida_pro_py(install_dir)
        dir_version = parse_version_from_dir_name(install_dir)
        if sdk_version:
            report.ida_version = sdk_version
            report.ida_version_source = "python/ida_pro.py SDK docstring"
        elif dir_version:
            report.ida_version = dir_version
            report.ida_version_source = "directory name"
        else:
            report.ida_version_error = "could not determine"

    return report


def collect_python_environment() -> PythonEnvironmentReport:
    report = PythonEnvironmentReport()

    process_virtual_env = os.environ.get("VIRTUAL_ENV")
    report.virtual_env = process_virtual_env
    report.virtual_env_is_uv_cache = process_virtual_env is not None and is_uv_cache_virtual_env(process_virtual_env)

    user_venv = resolve_user_virtual_env()
    report.user_virtual_env = str(user_venv) if user_venv else None

    report.candidate_virtual_envs = [
        CandidateVirtualEnv(path=str(candidate.path), source=candidate.source)
        for candidate in find_candidate_virtual_envs()
        if not is_uv_cache_virtual_env(candidate.path)
    ]

    env_python = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE") or ENV.HCLI_CURRENT_IDA_PYTHON_EXE
    if env_python:
        report.python_exe = str(env_python)
        report.python_exe_source = "$HCLI_CURRENT_IDA_PYTHON_EXE"
        return report

    idapython_venv_exe = os.environ.get("IDAPYTHON_VENV_EXECUTABLE") or ENV.IDAPYTHON_VENV_EXECUTABLE
    if idapython_venv_exe and Path(idapython_venv_exe).is_file():
        report.python_exe = str(idapython_venv_exe)
        report.python_exe_source = "$IDAPYTHON_VENV_EXECUTABLE"
        return report

    try:
        info = run_py_in_current_idapython(GET_PYTHON_INFO_PY)
        report.idat_probe = IdatProbe.model_validate(info)

        try:
            report.derived_exe = str(_derive_python_exe(info))
        except PythonNotFoundError as e:
            report.derived_exe_error = str(e)
    except Exception as e:
        report.idat_probe_error = f"{type(e).__name__}: {e}"

    return report


def collect_idapython_virtualenv(probe: IdatProbe | None) -> IdaPythonVirtualEnvReport | None:
    ida_venv = probe.virtual_env if probe else None
    if not ida_venv:
        return None

    venv_path = Path(ida_venv)
    report = IdaPythonVirtualEnvReport(venv=str(venv_path))

    pyvenv_cfg = venv_path / "pyvenv.cfg"
    if pyvenv_cfg.is_file():
        for line in pyvenv_cfg.read_text().splitlines():
            if line.startswith("home"):
                report.home = line.split("=", 1)[1].strip()
            elif line.startswith("include-system-site-packages"):
                report.system_site_packages = line.split("=", 1)[1].strip()

    return report


def collect_python_version() -> PythonVersionReport:
    report = PythonVersionReport(
        hcli_interpreter_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        hcli_interpreter_path=sys.executable,
    )

    python_exe: Path | None = None
    try:
        python_exe = find_current_python_executable()
        report.final_python_exe = str(python_exe)

        result = subprocess.run(
            [str(python_exe), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        report.probed_version = result.stdout.strip()
    except Exception as e:
        if python_exe is None:
            report.final_python_exe_error = f"{type(e).__name__}: {e}"
        else:
            report.probed_version_error = f"{type(e).__name__}: {e}"

    try:
        report.final_version = detect_current_python_version()
    except Exception as e:
        report.final_version_error = f"{type(e).__name__}: {e}"

    return report


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
                    f"$VIRTUAL_ENV ({process_virtual_env}) is the hcli process environment, not the IDA Python "
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
                    f"To use this virtualenv with IDA, activate it via idapythonrc.py."
                ),
            )
        )

    if not idapython_virtualenv:
        notes.append(
            EnvironmentNote(
                kind="hint",
                text=(
                    "To use a virtualenv with IDA, see: "
                    "https://community.hex-rays.com/t/using-a-virtualenv-for-idapython/261/5"
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

    if python_version.final_version:
        try:
            parts = python_version.final_version.split(".")
            major, minor = int(parts[0]), int(parts[1])
            if (major, minor) <= (3, 9):
                notes.append(
                    EnvironmentNote(
                        kind="warning",
                        text=(
                            f"Python {python_version.final_version} has reached end-of-life. "
                            "Many IDA plugins may not support it. "
                            "Consider upgrading to a newer Python and using idapyswitch to point IDA at it."
                        ),
                    )
                )
        except (ValueError, IndexError):
            pass

    return notes


def collect_environment_report() -> EnvironmentReport:
    report = EnvironmentReport(
        known_installations=collect_known_installations(),
        selected_installation=collect_selected_installation(),
    )

    if report.selected_installation.resolved_dir is None:
        return report

    report.architecture_and_version = collect_architecture_and_version(Path(report.selected_installation.resolved_dir))
    report.python_environment = collect_python_environment()
    report.idapython_virtualenv = collect_idapython_virtualenv(report.python_environment.idat_probe)
    report.python_version = collect_python_version()
    report.notes = collect_notes(report.python_environment, report.idapython_virtualenv, report.python_version)

    return report


def _path(p: object) -> str:
    return f"[repr.path]{escape(str(p))}[/repr.path]"


def _kv(key: str, value: str, via: str | None = None) -> None:
    if via:
        console.print(f"  [bold]{key}[/bold]: {value}  [dim](via {via})[/dim]")
    else:
        console.print(f"  [bold]{key}[/bold]: {value}")


def _err(key: str, error: str) -> None:
    console.print(f"  [bold]{key}[/bold]: [red]{escape(error)}[/red]")


def render_known_installations_text(report: KnownInstallationsReport) -> None:
    console.print("[bold]Known IDA installations[/bold]")

    for installation in report.installations:
        console.print(f"  {_path(installation.path)}  [dim](v{installation.version or '?'})[/dim]")

    if report.error:
        _err("scan", report.error)
    elif not report.installations:
        console.print("  [dim]none found[/dim]")


def render_selected_installation_text(report: SelectedInstallationReport) -> None:
    console.print("[bold]Selected installation[/bold]")

    if report.install_dir:
        _kv("install dir", _path(report.install_dir), report.install_dir_source)
    elif report.install_dir_error:
        _err("install dir", report.install_dir_error)

    if report.resolved_dir:
        _kv("resolved dir", _path(report.resolved_dir))
    elif report.resolved_dir_error:
        _err("resolved dir", report.resolved_dir_error)


def render_architecture_and_version_text(report: ArchitectureAndVersionReport) -> None:
    console.print("[bold]Architecture and version[/bold]")

    if report.ida_binary:
        _kv("ida binary", _path(report.ida_binary))
    if report.ida_binary_error:
        _err("ida binary", report.ida_binary_error)

    if report.binary_arch_error:
        _err("binary arch", report.binary_arch_error)
    elif report.ida_binary:
        ida_binary_name = escape(Path(report.ida_binary).name)
        _kv("binary arch", report.binary_arch or "unknown", f"{ida_binary_name} binary header")

    if report.platform:
        _kv("platform", report.platform)
    elif report.platform_error:
        _err("platform", report.platform_error)

    if report.ida_version:
        _kv("ida version", report.ida_version, report.ida_version_source)
    elif report.ida_version_error:
        _err("ida version", report.ida_version_error)


def render_python_environment_text(report: PythonEnvironmentReport) -> None:
    console.print("[bold]Python environment[/bold]")

    if report.virtual_env and report.virtual_env_is_uv_cache:
        _kv("$VIRTUAL_ENV", f"{_path(report.virtual_env)}  [dim](uv cache)[/dim]")
    elif report.virtual_env:
        _kv("$VIRTUAL_ENV", _path(report.virtual_env))
    else:
        _kv("$VIRTUAL_ENV", "not set")

    if report.user_virtual_env:
        _kv("user virtualenv", _path(report.user_virtual_env), "resolved from $PATH")

    for candidate in report.candidate_virtual_envs:
        _kv("  candidate venv", f"{_path(candidate.path)}  [dim](via {candidate.source})[/dim]")

    idapython_venv_exe = os.environ.get("IDAPYTHON_VENV_EXECUTABLE") or ENV.IDAPYTHON_VENV_EXECUTABLE
    if idapython_venv_exe:
        exists = Path(idapython_venv_exe).is_file()
        if exists:
            _kv("$IDAPYTHON_VENV_EXECUTABLE", _path(idapython_venv_exe))
        else:
            _kv("$IDAPYTHON_VENV_EXECUTABLE", f"{_path(idapython_venv_exe)}  [red](not found)[/red]")
    else:
        _kv("$IDAPYTHON_VENV_EXECUTABLE", "not set")

    if report.python_exe:
        _kv("python exe", _path(report.python_exe), report.python_exe_source)
        return

    _kv("HCLI_CURRENT_IDA_PYTHON_EXE", "not set")

    if report.idat_probe_error:
        _err("idat probe", report.idat_probe_error)
        return

    probe = report.idat_probe
    if probe is None:
        return

    console.print("  [bold]idat probe[/bold]: [green]success[/green]")
    _kv("  sys.prefix", _path(probe.prefix))
    _kv("  sys.base_prefix", _path(probe.base_prefix))
    _kv("  sys.executable", _path(probe.executable))
    _kv("  $VIRTUAL_ENV", _path(probe.virtual_env))
    _kv("  $IDAPYTHON_VENV_EXECUTABLE", _path(probe.idapython_venv_executable))
    _kv("  sys.version_info", f"{probe.version_major}.{probe.version_minor}")

    if report.derived_exe:
        _kv("derived exe", _path(report.derived_exe))
    elif report.derived_exe_error:
        _err("derived exe", report.derived_exe_error)


def render_idapython_virtualenv_text(report: IdaPythonVirtualEnvReport | None) -> None:
    console.print("[bold]IDAPython virtualenv[/bold]")

    if report is None:
        console.print("  [dim]none detected[/dim]")
        return

    _kv("venv", _path(report.venv), "activated by idapythonrc.py")
    if report.home is not None:
        _kv("  home", report.home)
    if report.system_site_packages is not None:
        _kv("  system site-packages", report.system_site_packages)


def render_python_version_text(report: PythonVersionReport) -> None:
    console.print("[bold]Python version[/bold]")

    if report.final_python_exe:
        _kv("final python exe", _path(report.final_python_exe))

    if report.probed_version:
        exe_name = escape(Path(report.final_python_exe or "").name)
        _kv("probed version", report.probed_version, f"running {exe_name}")
    elif report.final_python_exe_error or report.probed_version_error:
        _err("probed version", report.final_python_exe_error or report.probed_version_error or "")

    _kv("hcli interpreter", report.hcli_interpreter_version, _path(report.hcli_interpreter_path))

    if report.final_version:
        style = "green" if report.final_version != report.hcli_interpreter_version else "yellow"
        _kv("final version", f"[{style}]{report.final_version}[/{style}]")
    elif report.final_version_error:
        _err("final version", report.final_version_error)


def render_notes_text(notes: list[EnvironmentNote]) -> None:
    for note in notes:
        if note.kind == "warning":
            console.print(f"[bold yellow]Warning:[/bold yellow] {escape(note.text)}", highlight=False)
            console.print()
        elif note.kind == "diagnostic":
            console.print(f"[dim]Note: {escape(note.text)}[/dim]", highlight=False)
            console.print()
        else:
            console.print(f"[dim]{escape(note.text)}[/dim]", highlight=False)


def render_environment_report_text(report: EnvironmentReport) -> None:
    render_known_installations_text(report.known_installations)
    console.print()
    render_selected_installation_text(report.selected_installation)
    console.print()

    if report.architecture_and_version is None or report.python_environment is None or report.python_version is None:
        return

    render_architecture_and_version_text(report.architecture_and_version)
    console.print()
    render_python_environment_text(report.python_environment)
    console.print()
    render_idapython_virtualenv_text(report.idapython_virtualenv)
    console.print()
    render_python_version_text(report.python_version)
    console.print()
    render_notes_text(report.notes)


def render_environment_report_json(report: EnvironmentReport) -> None:
    print_json(report.model_dump(mode="json"))


@click.command(hidden=True)
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
def explain_environment(json_output: bool) -> None:
    """Show how the current IDA installation and Python version are detected. (experimental)"""
    report = collect_environment_report()

    if json_output:
        render_environment_report_json(report)
    else:
        render_environment_report_text(report)
