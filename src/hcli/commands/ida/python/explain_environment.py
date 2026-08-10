from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

import rich_click as click
from pydantic import BaseModel
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import console, print_json
from hcli.lib.ida import (
    detect_binary_arch,
    find_current_ida_executable,
    find_current_ida_platform,
    find_standard_installations,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
    resolve_current_ida_install_directory,
    resolve_current_ida_version,
)
from hcli.lib.ida.python import (
    IdatProbe,
    PythonNotFoundError,
    PythonVersionMismatch,
    find_current_python_executable,
    find_python_version_mismatches,
    format_python_version_mismatch_warning,
    probe_current_python_info,
    resolve_current_python,
)
from hcli.lib.venv import (
    find_candidate_virtual_envs,
    get_virtual_env_version,
    is_uv_cache_virtual_env,
    parse_pyvenv_cfg,
    probe_python_version,
    resolve_user_virtual_env,
)


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

    if report.idapython_venv_executable:
        if report.idapython_venv_executable_exists:
            _kv("$IDAPYTHON_VENV_EXECUTABLE", _path(report.idapython_venv_executable))
        else:
            _kv("$IDAPYTHON_VENV_EXECUTABLE", f"{_path(report.idapython_venv_executable)}  [red](not found)[/red]")
    else:
        _kv("$IDAPYTHON_VENV_EXECUTABLE", "not set")

    if report.idat_probe_error:
        _err("idat probe", report.idat_probe_error)

    probe = report.idat_probe
    if probe is not None:
        console.print("  [bold]idat probe[/bold]: [green]success[/green]")
        _kv("  sys.prefix", _path(probe.prefix))
        _kv("  sys.base_prefix", _path(probe.base_prefix))
        _kv("  sys.executable", _path(probe.executable))
        _kv("  $VIRTUAL_ENV", _path(probe.virtual_env))
        _kv("  $IDAPYTHON_VENV_EXECUTABLE", _path(probe.idapython_venv_executable))
        _kv("  sys.version_info", f"{probe.version_major}.{probe.version_minor}")

    if report.python_exe:
        _kv("python exe", _path(report.python_exe), report.python_exe_source)
    elif report.python_exe_error:
        _err("python exe", report.python_exe_error)


def render_idapython_virtualenv_text(report: IdaPythonVirtualEnvReport | None, ida_python_version: str | None) -> None:
    console.print("[bold]IDAPython virtualenv[/bold]")

    if report is None:
        console.print("  [dim]none detected[/dim]")
        return

    _kv("venv", _path(report.venv), "activated by idapythonrc.py")
    if report.home is not None:
        _kv("  home", report.home)
    if report.system_site_packages is not None:
        _kv("  system site-packages", report.system_site_packages)

    if report.python_version:
        style = "yellow" if ida_python_version and report.python_version != ida_python_version else "green"
        _kv("  python version", f"[{style}]{report.python_version}[/{style}]")
    else:
        _err("  python version", "could not determine")


def render_python_version_text(report: PythonVersionReport) -> None:
    console.print("[bold]Python version[/bold]")

    if report.final_python_exe:
        _kv("final python exe", _path(report.final_python_exe))

    if report.probed_version:
        exe_name = escape(Path(report.final_python_exe or "").name)
        style = "green" if report.probed_version != report.hcli_interpreter_version else "yellow"
        _kv("probed version", f"[{style}]{report.probed_version}[/{style}]", f"running {exe_name}")
    elif report.final_python_exe_error or report.probed_version_error:
        _err("probed version", report.final_python_exe_error or report.probed_version_error or "")

    _kv("HCLI interpreter", report.hcli_interpreter_version, _path(report.hcli_interpreter_path))


def render_python_version_mismatches_text(report: EnvironmentReport) -> None:
    if report.python_version_mismatch_error:
        _err("version mismatch check", report.python_version_mismatch_error)

    if report.python_version_mismatches:
        mismatches = [
            PythonVersionMismatch(
                ida_version=entry.ida_version,
                other_version=entry.other_version,
                other_path=Path(entry.other_path),
                other_source=entry.other_source,
            )
            for entry in report.python_version_mismatches
        ]
        console.print(format_python_version_mismatch_warning(mismatches), highlight=False)
        console.print()


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

    probe = report.python_environment.idat_probe
    ida_python_version = f"{probe.version_major}.{probe.version_minor}" if probe else None
    render_idapython_virtualenv_text(report.idapython_virtualenv, ida_python_version)
    console.print()
    render_python_version_text(report.python_version)
    console.print()
    render_notes_text([note for note in report.notes if note.kind != "warning"])
    render_python_version_mismatches_text(report)
    render_notes_text([note for note in report.notes if note.kind == "warning"])


def render_environment_report_json(report: EnvironmentReport) -> None:
    print_json(report.model_dump(mode="json"))


@click.command()
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
def explain_environment(json_output: bool) -> None:
    """Show how the current IDA installation and Python version are detected. (experimental)"""
    # Diagnostics must consult IDA itself rather than potentially restating a stale
    # cross-process cache entry. The in-process cache is also cleared in case an
    # extension resolved Python before dispatching this command.
    os.environ["HCLI_DISABLE_PYTHON_CACHE"] = "1"
    probe_current_python_info.cache_clear()
    report = collect_environment_report()

    if json_output:
        render_environment_report_json(report)
    else:
        render_environment_report_text(report)
