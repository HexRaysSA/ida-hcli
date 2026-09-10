from __future__ import annotations

import logging
import shutil

import rich.status
import rich_click as click
from pydantic import BaseModel
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import console, print_json, stderr_console
from hcli.lib.ida import get_ida_user_dir
from hcli.lib.ida.python import PythonNotFoundError, ResolvedPython, resolve_current_python
from hcli.lib.ida.python.environment import (
    EnvironmentFinding,
    PythonEnvironmentState,
    SetupPattern,
    check_python_environment,
    collect_architecture_and_version,
    collect_python_environment_state,
    collect_selected_installation,
    get_recommended_venv_dir,
    get_venv_python_path,
    has_errors,
    identify_setup_pattern,
    render_set_env_var_command,
)
from hcli.lib.venv import find_virtual_env_python, get_virtual_env_version

logger = logging.getLogger(__name__)


class FindingModel(BaseModel):
    id: str
    severity: str
    summary: str
    detail: str
    fix_hint: str

    @classmethod
    def from_finding(cls, finding: EnvironmentFinding) -> FindingModel:
        return cls(
            id=finding.id,
            severity=finding.severity,
            summary=finding.summary,
            detail=finding.detail,
            fix_hint=finding.fix_hint,
        )


class SetupPatternModel(BaseModel):
    id: str
    name: str
    description: str

    @classmethod
    def from_pattern(cls, pattern: SetupPattern) -> SetupPatternModel:
        return cls(id=pattern.id, name=pattern.name, description=pattern.description)


class DoctorReport(BaseModel):
    ida_install_dir: str | None
    ida_install_dir_source: str | None
    ida_install_dir_error: str | None
    ida_version: str | None
    ida_platform: str | None
    idausr: str | None
    python_exe: str | None
    python_exe_source: str | None
    python_exe_error: str | None
    python_version: str | None
    ida_python_version: str | None
    venv_root: str | None
    pip_available: bool | None
    externally_managed: bool | None
    idapython_venv_executable: str | None
    pattern: SetupPatternModel | None
    findings: list[FindingModel]
    notes: list[str]
    ok: bool


def collect_context_notes(state: PythonEnvironmentState) -> list[str]:
    """Observations that aren't problems by themselves but explain the findings or the fix."""
    notes: list[str] = []

    recommended = get_recommended_venv_dir(state.idausr)
    if state.idausr is not None and (recommended / "pyvenv.cfg").is_file():
        in_use = state.venv_root is not None and state.venv_root.resolve() == recommended.resolve()
        if not in_use:
            version = get_virtual_env_version(recommended)
            python_exe = find_virtual_env_python(recommended) or get_venv_python_path(recommended, state.system)
            set_var = render_set_env_var_command("IDAPYTHON_VENV_EXECUTABLE", str(python_exe), state.system)
            notes.append(
                f"A virtual environment already exists at {recommended}"
                f"{f' (Python {version})' if version else ''}, but IDA is not configured to use it. "
                f"If its version is correct, set: {set_var}"
            )

    if shutil.which("uv"):
        notes.append("uv is on PATH. `create-environment` uses `uv venv --seed`, which can also download Python.")
    else:
        notes.append(
            "uv is not on PATH. `create-environment` uses the stdlib venv module and needs a matching Python "
            "installation. To install uv, see https://docs.astral.sh/uv/."
        )

    if state.system == "mac":
        notes.append(
            "On macOS, a shell profile applies only to IDA started from that shell, not from Finder or the Dock. "
            "Use `launchctl setenv IDAPYTHON_VENV_EXECUTABLE <path>`, or start IDA from a terminal."
        )
    elif state.system == "windows":
        notes.append(
            "On Windows, `setx` sets the variable for your user account. Programs started afterwards, including "
            "IDA from the Start menu, see it."
        )

    return notes


def build_doctor_report() -> DoctorReport:
    selected = collect_selected_installation()
    arch = collect_architecture_and_version() if selected.install_dir else None

    resolved: ResolvedPython | None = None
    python_exe_error: str | None = None
    try:
        resolved = resolve_current_python()
    except PythonNotFoundError as e:
        python_exe_error = str(e)

    idausr: str | None
    try:
        idausr = str(get_ida_user_dir())
    except ValueError:
        idausr = None

    if resolved is None:
        findings = [
            EnvironmentFinding(
                id="python-not-found",
                severity="error",
                summary="HCLI cannot determine IDA's Python interpreter",
                detail=python_exe_error or "",
                fix_hint=(
                    f"Run `{ENV.HCLI_BINARY_NAME} ida python create-environment` to create a virtual environment "
                    "and set IDAPYTHON_VENV_EXECUTABLE. Or set HCLI_CURRENT_IDA_PYTHON_EXE to the interpreter "
                    "that IDA uses."
                ),
            )
        ]
        return DoctorReport(
            ida_install_dir=selected.install_dir,
            ida_install_dir_source=selected.install_dir_source,
            ida_install_dir_error=selected.install_dir_error,
            ida_version=arch.ida_version if arch else None,
            ida_platform=arch.platform if arch else None,
            idausr=idausr,
            python_exe=None,
            python_exe_source=None,
            python_exe_error=python_exe_error,
            python_version=None,
            ida_python_version=None,
            venv_root=None,
            pip_available=None,
            externally_managed=None,
            idapython_venv_executable=ENV.IDAPYTHON_VENV_EXECUTABLE,
            pattern=None,
            findings=[FindingModel.from_finding(f) for f in findings],
            notes=[],
            ok=False,
        )

    state = collect_python_environment_state(resolved, probe_ida=True)
    findings = check_python_environment(state)
    pattern = identify_setup_pattern(state)

    return DoctorReport(
        ida_install_dir=selected.install_dir,
        ida_install_dir_source=selected.install_dir_source,
        ida_install_dir_error=selected.install_dir_error,
        ida_version=arch.ida_version if arch else None,
        ida_platform=arch.platform if arch else None,
        idausr=str(state.idausr) if state.idausr else None,
        python_exe=str(state.python_exe),
        python_exe_source=state.source,
        python_exe_error=None,
        python_version=state.python_version,
        ida_python_version=state.ida_python_version,
        venv_root=str(state.venv_root) if state.venv_root else None,
        pip_available=state.pip_available,
        externally_managed=state.externally_managed,
        idapython_venv_executable=str(state.idapython_venv_executable) if state.idapython_venv_executable else None,
        pattern=SetupPatternModel.from_pattern(pattern),
        findings=[FindingModel.from_finding(f) for f in findings],
        notes=collect_context_notes(state),
        ok=not has_errors(findings),
    )


def _kv(key: str, value: str | None, via: str | None = None) -> None:
    shown = escape(value) if value else "[dim]unknown[/dim]"
    suffix = f" [dim](via {escape(via)})[/dim]" if via else ""
    console.print(f"  {key}: {shown}{suffix}", highlight=False)


def render_doctor_report_text(report: DoctorReport) -> None:
    console.print("[bold]IDA installation[/bold]")
    if report.ida_install_dir:
        _kv("directory", report.ida_install_dir, report.ida_install_dir_source)
    else:
        _kv("directory", None)
        if report.ida_install_dir_error:
            console.print(f"    [red]{escape(report.ida_install_dir_error)}[/red]", highlight=False)
    _kv("version", report.ida_version)
    _kv("platform", report.ida_platform)
    _kv("user directory ($IDAUSR)", report.idausr)
    console.print()

    console.print("[bold]IDA's Python[/bold]")
    if report.python_exe:
        _kv("interpreter", report.python_exe, report.python_exe_source)
    else:
        _kv("interpreter", None)
        if report.python_exe_error:
            console.print(f"    [red]{escape(report.python_exe_error)}[/red]", highlight=False)
    _kv("interpreter version", report.python_version)
    _kv("IDA's embedded Python", report.ida_python_version)
    _kv("virtual environment", report.venv_root or ("none" if report.python_exe else None))
    if report.pip_available is not None:
        _kv("pip", "available" if report.pip_available else "missing")
    if report.externally_managed:
        _kv("externally managed (PEP 668)", "yes")
    _kv("$IDAPYTHON_VENV_EXECUTABLE", report.idapython_venv_executable or ("not set" if report.python_exe else None))
    console.print()

    if report.pattern:
        console.print(f"[bold]Setup:[/bold] {escape(report.pattern.name)}")
        console.print(f"  {escape(report.pattern.description)}", highlight=False)
        console.print()

    if not report.findings:
        console.print("[green]IDA's Python environment matches the recommended setup.[/green]")
        console.print(f"You can install plugins with Python dependencies with `{ENV.HCLI_BINARY_NAME} plugin install`.")
    else:
        errors = [f for f in report.findings if f.severity == "error"]
        warnings = [f for f in report.findings if f.severity == "warning"]
        if errors:
            console.print(f"[bold red]Errors ({len(errors)})[/bold red]")
            for finding in errors:
                _render_finding(finding, "red")
        if warnings:
            console.print(f"[bold yellow]Warnings ({len(warnings)})[/bold yellow]")
            for finding in warnings:
                _render_finding(finding, "yellow")

    if report.notes:
        console.print("[bold]Notes[/bold]")
        for note in report.notes:
            console.print(f"  - {escape(note)}", highlight=False)


def _render_finding(finding: FindingModel, color: str) -> None:
    console.print(f"  [{color}]*[/{color}] [bold]{escape(finding.summary)}[/bold] [dim]({finding.id})[/dim]")
    for line in finding.detail.splitlines():
        console.print(f"      {escape(line)}", highlight=False)
    console.print("      [bold]Fix:[/bold]")
    for line in finding.fix_hint.splitlines():
        console.print(f"      {escape(line)}", highlight=False)
    console.print()


@click.command()
@click.option("--json", "json_output", is_flag=True, help="Output the report as JSON.")
def doctor(json_output: bool) -> None:
    """Check IDA's Python environment against the recommended setup.

    The recommended setup is a virtual environment with pip, selected by
    IDAPYTHON_VENV_EXECUTABLE. Its Python version must match the one that
    idapyswitch registered for IDA. Each problem comes with a fix.

    Exits with status 1 when a problem prevents installing plugin dependencies.
    """
    with rich.status.Status("inspecting IDA's Python environment", console=stderr_console):
        report = build_doctor_report()

    if json_output:
        print_json(report.model_dump(mode="json"))
    else:
        render_doctor_report_text(report)

    if not report.ok:
        raise click.exceptions.Exit(1)
