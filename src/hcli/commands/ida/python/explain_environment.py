from __future__ import annotations

from pathlib import Path

import rich_click as click
from rich.markup import escape

from hcli.lib.console import console, print_json
from hcli.lib.ida.python import PythonVersionMismatch, format_python_version_mismatch_warning
from hcli.lib.ida.python.environment import (
    ArchitectureAndVersionReport,
    EnvironmentNote,
    EnvironmentReport,
    IdaPythonVirtualEnvReport,
    KnownInstallationsReport,
    PythonEnvironmentReport,
    PythonVersionReport,
    SelectedInstallationReport,
    collect_environment_report,
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

    if report.externally_managed:
        _kv("externally managed", "[red]yes (PEP 668)[/red]")


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
    report = collect_environment_report()

    if json_output:
        render_environment_report_json(report)
    else:
        render_environment_report_text(report)
