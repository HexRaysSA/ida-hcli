import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fixtures import get_base_python_exe, set_env_var, unset_env_var

from hcli.lib.ida.python import ResolvedPython
from hcli.lib.ida.python.environment import (
    EnvironmentFinding,
    PythonEnvironmentError,
    PythonEnvironmentState,
    check_python_environment,
    collect_python_environment_state,
    does_idapythonrc_activate_venv,
    format_environment_warnings,
    get_venv_python_path,
    has_errors,
    identify_setup_pattern,
    render_set_env_var_command,
    validate_python_environment,
    venv_executable_points_at,
)

IDAUSR = Path("/home/user/.idapro")
VENV = IDAUSR / "venv"
VENV_PYTHON = VENV / "bin" / "python"


RECOMMENDED_STATE = PythonEnvironmentState(
    python_exe=VENV_PYTHON,
    source="$IDAPYTHON_VENV_EXECUTABLE",
    system="linux",
    idausr=IDAUSR,
    venv_root=VENV,
    pip_available=True,
    python_version="3.12",
    ida_python_version="3.12",
    externally_managed=False,
    uv_ephemeral=False,
    idapython_venv_executable=VENV_PYTHON,
    idapython_venv_executable_exists=True,
    shell_virtual_env=None,
    idapythonrc_path=None,
    idapythonrc_activates_venv=False,
    base_prefix=Path("/usr"),
    conda=False,
)


def make_state(**overrides) -> PythonEnvironmentState:
    """A properly configured Linux environment; override fields to break it."""
    return dataclasses.replace(RECOMMENDED_STATE, **overrides)


def finding_ids(findings: list[EnvironmentFinding]) -> list[str]:
    return [f.id for f in findings]


def test_recommended_setup_has_no_findings():
    assert check_python_environment(make_state()) == []
    assert identify_setup_pattern(make_state()).id == "properly-configured"


def test_default_setup_reports_no_venv_error():
    state = make_state(
        python_exe=Path("/usr/bin/python3"),
        source="derived from idat probe",
        venv_root=None,
        idapython_venv_executable=None,
    )
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv"]
    assert has_errors(findings)
    assert identify_setup_pattern(state).id == "default"

    hint = findings[0].fix_hint
    assert "create-environment" in hint
    assert str(VENV) in hint
    assert "--python 3.12" in hint
    assert f'export IDAPYTHON_VENV_EXECUTABLE="{VENV_PYTHON}"' in hint


def test_no_venv_exe_var_is_only_a_warning_when_venv_is_in_use():
    state = make_state(source="$HCLI_CURRENT_IDA_PYTHON_EXE", idapython_venv_executable=None)
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv-exe-var"]
    assert not has_errors(findings)
    assert identify_setup_pattern(state).id == "venv-not-configured"


def test_no_venv_exe_var_is_not_reported_without_a_venv():
    state = make_state(python_exe=Path("/usr/bin/python3"), venv_root=None, idapython_venv_executable=None)
    assert "no-venv-exe-var" not in finding_ids(check_python_environment(state))


def test_venv_exe_var_pointing_at_a_different_venv_warns():
    other = Path("/opt/other-venv/bin/python")
    state = make_state(idapython_venv_executable=other)
    assert not venv_executable_points_at(state)
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv-exe-var"]
    assert str(other) in findings[0].summary


def test_dangling_venv_exe_var_is_an_error_when_probe_falls_through():
    gone = Path("/home/user/.idapro/venv/bin/python")
    state = make_state(
        python_exe=Path("/usr/bin/python3"),
        source="derived from idat probe",
        venv_root=None,
        idapython_venv_executable=gone,
        idapython_venv_executable_exists=False,
    )
    findings = check_python_environment(state)
    ids = finding_ids(findings)
    assert "venv-exe-not-found" in ids
    assert "no-venv" in ids
    assert ids.index("venv-exe-not-found") < ids.index("no-venv")
    assert findings[0].severity == "error"
    assert str(gone) in findings[0].summary
    assert "create-environment" in findings[0].fix_hint
    assert identify_setup_pattern(state).id == "dangling-venv-exe"


def test_dangling_venv_exe_var_suppresses_no_venv_exe_var_warning():
    gone = Path("/home/user/.idapro/old-venv/bin/python")
    state = make_state(
        idapython_venv_executable=gone,
        idapython_venv_executable_exists=False,
    )
    ids = finding_ids(check_python_environment(state))
    assert "venv-exe-not-found" in ids
    assert "no-venv-exe-var" not in ids


def test_venv_exe_var_matches_when_naming_a_different_interpreter_alias():
    state = make_state(idapython_venv_executable=VENV / "bin" / "python3.12")
    assert venv_executable_points_at(state)
    assert check_python_environment(state) == []


def test_version_mismatch_is_an_error_with_both_versions_in_message():
    state = make_state(python_version="3.13", ida_python_version="3.12")
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["version-mismatch"]
    assert "3.12" in findings[0].summary
    assert "3.13" in findings[0].summary
    assert "idapyswitch" in findings[0].fix_hint
    assert identify_setup_pattern(state).id == "configured-with-problems"


def test_unknown_ida_version_skips_the_mismatch_check():
    assert check_python_environment(make_state(ida_python_version=None)) == []


def test_missing_pip_is_an_error_and_suggests_uv_seed_for_venvs():
    findings = check_python_environment(make_state(pip_available=False))
    assert finding_ids(findings) == ["no-pip"]
    assert "uv venv --seed" in findings[0].fix_hint
    assert "ensurepip" in findings[0].fix_hint


def test_unknown_pip_state_is_not_reported():
    assert check_python_environment(make_state(pip_available=None)) == []


def test_externally_managed_base_python_reports_both_errors():
    state = make_state(
        python_exe=Path("/opt/homebrew/bin/python3.12"),
        source="derived from idat probe",
        venv_root=None,
        pip_available=None,
        externally_managed=True,
        idapython_venv_executable=None,
        base_prefix=Path("/opt/homebrew/Cellar/python@3.12/3.12.4/Frameworks/Python.framework/Versions/3.12"),
    )
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv", "externally-managed"]
    assert identify_setup_pattern(state).id == "homebrew"


def test_uv_ephemeral_is_an_error_and_recognized_pattern():
    cache_venv = Path("/home/user/.cache/uv/archive-v0/abc")
    state = make_state(
        python_exe=cache_venv / "bin" / "python",
        source="derived from idat probe",
        venv_root=cache_venv,
        uv_ephemeral=True,
        idapython_venv_executable=None,
    )
    findings = check_python_environment(state)
    assert findings[0].id == "uv-ephemeral"
    assert findings[0].severity == "error"
    assert identify_setup_pattern(state).id == "uv-ephemeral"


def test_idapythonrc_activation_is_a_warning_and_recognized_pattern():
    state = make_state(
        python_exe=Path("/usr/bin/python3"),
        source="derived from idat probe",
        venv_root=None,
        idapython_venv_executable=None,
        idapythonrc_path=IDAUSR / "idapythonrc.py",
        idapythonrc_activates_venv=True,
    )
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv", "idapythonrc-venv"]
    assert [f.severity for f in findings] == ["error", "warning"]
    assert identify_setup_pattern(state).id == "idapythonrc-venv"


def test_shell_activated_venv_pattern():
    state = make_state(
        source="derived from idat probe",
        idapython_venv_executable=None,
        shell_virtual_env=VENV,
    )
    assert identify_setup_pattern(state).id == "shell-activated-venv"


def test_conda_pattern_is_reported_as_no_venv():
    state = make_state(
        python_exe=Path("/home/user/miniconda3/envs/ida/bin/python"),
        source="derived from idat probe",
        venv_root=None,
        idapython_venv_executable=None,
        conda=True,
    )
    findings = check_python_environment(state)
    assert finding_ids(findings) == ["no-venv"]
    assert "conda" in findings[0].detail
    assert identify_setup_pattern(state).id == "conda"


def test_windows_store_shim_pattern():
    shim = Path(r"C:\Users\user\AppData\Local\Microsoft\WindowsApps\python.exe")
    state = make_state(
        python_exe=shim,
        source="derived from idat probe",
        system="windows",
        venv_root=None,
        idapython_venv_executable=None,
    )
    assert identify_setup_pattern(state).id == "windows-store"


def test_windows_fix_hints_use_setx_and_scripts_layout():
    idausr = Path(r"C:\Users\user\AppData\Roaming\Hex-Rays\IDA Pro")
    state = make_state(
        python_exe=Path(r"C:\Python312\python.exe"),
        source="derived from idat probe",
        system="windows",
        idausr=idausr,
        venv_root=None,
        idapython_venv_executable=None,
    )
    hint = check_python_environment(state)[0].fix_hint
    assert "setx IDAPYTHON_VENV_EXECUTABLE" in hint
    assert str(idausr / "venv" / "Scripts" / "python.exe") in hint
    assert "export" not in hint


def test_render_set_env_var_command_per_platform():
    assert render_set_env_var_command("X", "/a b", "linux") == 'export X="/a b"'
    assert render_set_env_var_command("X", "/a b", "mac") == 'export X="/a b"'
    assert render_set_env_var_command("X", r"C:\a b", "windows") == r'setx X "C:\a b"'


def test_get_venv_python_path_per_platform():
    assert get_venv_python_path(Path("/v"), "linux") == Path("/v/bin/python")
    assert get_venv_python_path(Path("v"), "windows") == Path("v") / "Scripts" / "python.exe"


def test_format_environment_warnings_distinguishes_errors_from_warnings():
    assert format_environment_warnings([]) == ""

    warnings_only = check_python_environment(make_state(idapython_venv_executable=None))
    text = format_environment_warnings(warnings_only)
    assert text.startswith("[bold yellow]Warning:")
    assert "IDAPYTHON_VENV_EXECUTABLE is not set" in text
    assert text.endswith("ida python doctor` for details and fixes.")

    with_errors = check_python_environment(make_state(pip_available=False, idapython_venv_executable=None))
    text = format_environment_warnings(with_errors)
    assert text.startswith("[bold red]Error:")
    assert "pip is not available" in text
    assert "IDAPYTHON_VENV_EXECUTABLE is not set" in text


def test_does_idapythonrc_activate_venv(tmp_path: Path):
    rc = tmp_path / "idapythonrc.py"
    rc.write_text("import idaapi\nprint('hello')\n")
    assert not does_idapythonrc_activate_venv(rc)

    rc.write_text("import site\nsite.addsitedir('/home/user/venv/lib/python3.12/site-packages')\n")
    assert does_idapythonrc_activate_venv(rc)

    rc.write_text("exec(open('/home/user/venv/bin/activate_this.py').read())\n")
    assert does_idapythonrc_activate_venv(rc)

    assert not does_idapythonrc_activate_venv(tmp_path / "missing.py")


@pytest.fixture(scope="session")
def real_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A venv built from the test interpreter, with pip, cached for the session."""
    root = tmp_path_factory.mktemp("idausr") / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(root)], check=True, capture_output=True)
    return root


def venv_python(root: Path) -> Path:
    return root / "Scripts" / "python.exe" if os.name == "nt" else root / "bin" / "python"


def test_collect_state_from_a_real_venv_via_venv_executable_var(real_venv: Path, monkeypatch: pytest.MonkeyPatch):
    exe = venv_python(real_venv)
    idausr = real_venv.parent
    set_env_var(monkeypatch, "HCLI_IDAUSR", str(idausr))
    set_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE", str(exe))
    unset_env_var(monkeypatch, "VIRTUAL_ENV")
    (idausr / "idapythonrc.py").write_text("import site\nsite.addsitedir('x')\n")

    resolved = ResolvedPython(exe, "$IDAPYTHON_VENV_EXECUTABLE")
    state = collect_python_environment_state(resolved, probe_ida=False)

    assert state.venv_root is not None
    assert state.venv_root.resolve() == real_venv.resolve()
    assert state.pip_available is True
    assert state.python_version == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert state.ida_python_version is None
    assert not state.externally_managed
    assert not state.uv_ephemeral
    assert state.idapython_venv_executable == exe
    assert state.idapython_venv_executable_exists
    assert state.idapythonrc_activates_venv
    assert venv_executable_points_at(state)

    assert finding_ids(check_python_environment(state)) == ["idapythonrc-venv"]


def test_collect_state_flags_missing_var_for_hcli_override(real_venv: Path, monkeypatch: pytest.MonkeyPatch):
    exe = venv_python(real_venv)
    set_env_var(monkeypatch, "HCLI_IDAUSR", str(real_venv.parent))
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")
    unset_env_var(monkeypatch, "VIRTUAL_ENV")
    (real_venv.parent / "idapythonrc.py").unlink(missing_ok=True)

    resolved = ResolvedPython(exe, "$HCLI_CURRENT_IDA_PYTHON_EXE")
    state = collect_python_environment_state(resolved, probe_ida=True)
    assert state.ida_python_version is None
    assert finding_ids(check_python_environment(state)) == ["no-venv-exe-var"]

    # warnings only: validation prints but does not raise
    validate_python_environment(resolved)


def test_validate_python_environment_rejects_base_interpreter_without_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    set_env_var(monkeypatch, "HCLI_IDAUSR", str(tmp_path))
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")
    unset_env_var(monkeypatch, "VIRTUAL_ENV")

    resolved = ResolvedPython(get_base_python_exe(), "$HCLI_CURRENT_IDA_PYTHON_EXE")

    with pytest.raises(PythonEnvironmentError) as excinfo:
        validate_python_environment(resolved)

    assert "no-venv" in [f.id for f in excinfo.value.findings]
    assert "ida python doctor" in str(excinfo.value)
