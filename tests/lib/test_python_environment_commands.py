import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import get_python_exe_for_venv, set_env_var, unset_env_var

from hcli.commands.ida.python import python as python_group
from hcli.commands.ida.python.create_environment import CreateEnvironmentError, run_create_environment

THIS_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"


def _run(args: list[str]):
    runner = CliRunner(mix_stderr=False)
    return runner.invoke(python_group, args)


def _create(python_version: str | None):
    return run_create_environment(
        path=None, python_version=python_version, configure=False, interactive=False, quiet=False
    )


def test_doctor_json_warns_when_venv_is_not_configured_for_ida(virtual_ida_environment_with_venv, monkeypatch):
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")

    result = _run(["doctor", "--json"])
    assert result.exit_code == 0, result.output

    report = json.loads(result.stdout)
    venv = Path(os.environ["HCLI_IDAUSR"]) / "venv"
    assert Path(report["python_exe"]).resolve() == get_python_exe_for_venv(venv).resolve()
    assert report["python_exe_source"] == "$HCLI_CURRENT_IDA_PYTHON_EXE"
    assert Path(report["venv_root"]).resolve() == venv.resolve()
    assert report["pip_available"] is True
    assert report["python_version"] == THIS_VERSION
    assert report["ida_python_version"] is None
    assert [f["id"] for f in report["findings"]] == ["no-venv-exe-var"]
    assert report["pattern"]["id"] == "shell-activated-venv"
    assert report["ok"] is True


def test_doctor_passes_a_properly_configured_environment(virtual_ida_environment_with_venv, monkeypatch):
    set_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE", os.environ["HCLI_CURRENT_IDA_PYTHON_EXE"])

    result = _run(["doctor", "--json"])
    assert result.exit_code == 0, result.output

    report = json.loads(result.stdout)
    assert report["findings"] == []
    assert report["pattern"]["id"] == "properly-configured"
    assert report["ok"] is True

    result = _run(["doctor"])
    assert result.exit_code == 0, result.output
    assert "matches the recommended setup" in " ".join(result.stdout.split())


def test_doctor_fails_for_a_base_interpreter(virtual_ida_environment, monkeypatch):
    base_exe = Path(sys._base_executable)  # type: ignore[attr-defined]
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_PYTHON_EXE", str(base_exe))
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")

    result = _run(["doctor", "--json"])
    assert result.exit_code == 1, result.output

    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "no-venv" in [f["id"] for f in report["findings"]]
    assert any("create-environment" in f["fix_hint"] for f in report["findings"])


@pytest.fixture
def no_ida(tmp_path: Path, monkeypatch):
    """An IDA install directory with no idat, so probing IDA's Python fails fast."""
    fake = tmp_path / "ida"
    fake.mkdir()
    set_env_var(monkeypatch, "HCLI_CURRENT_IDA_INSTALL_DIR", str(fake))
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")
    yield


def test_create_environment_creates_venv_at_idausr_and_is_idempotent(virtual_ida_environment, no_ida):
    idausr = Path(os.environ["HCLI_IDAUSR"])

    result = _run(["create-environment", "--json", "--no-configure", "--python-version", THIS_VERSION])
    assert result.exit_code == 0, result.output

    created = json.loads(result.stdout)
    assert Path(created["venv_path"]) == idausr / "venv"
    assert created["created"] is True
    assert created["python_version"] == THIS_VERSION
    assert created["python_version_source"] == "--python-version"
    assert created["configured"] is False
    assert "IDAPYTHON_VENV_EXECUTABLE" in created["set_command"]

    python_exe = Path(created["python_exe"])
    assert python_exe.is_file()
    assert (idausr / "venv" / "pyvenv.cfg").is_file()

    result = _run(["create-environment", "--json", "--no-configure", "--python-version", THIS_VERSION])
    assert result.exit_code == 0, result.output
    again = json.loads(result.stdout)
    assert again["created"] is False
    assert Path(again["python_exe"]).resolve() == python_exe.resolve()


def test_create_environment_refuses_wrong_version_venv(virtual_ida_environment_with_venv, no_ida):
    with pytest.raises(CreateEnvironmentError) as excinfo:
        _create("2.7")
    message = str(excinfo.value)
    assert f"is a Python {THIS_VERSION} virtual environment, but IDA runs Python 2.7" in message
    assert "never deletes" in message


def test_create_environment_refuses_unrelated_directory(virtual_ida_environment, no_ida):
    target = Path(os.environ["HCLI_IDAUSR"]) / "venv"
    target.mkdir()
    (target / "notes.txt").write_text("mine")

    with pytest.raises(CreateEnvironmentError, match="does not overwrite existing files"):
        _create(THIS_VERSION)
    assert (target / "notes.txt").read_text() == "mine"


def test_create_environment_needs_a_version_without_ida(virtual_ida_environment, no_ida):
    with pytest.raises(CreateEnvironmentError, match="--python-version"):
        _create(None)


def test_no_python_environment_check_silences_exec_warning(virtual_ida_environment_with_venv, monkeypatch):
    unset_env_var(monkeypatch, "IDAPYTHON_VENV_EXECUTABLE")

    result = _run(["exec", "-c", "print('ok')"])
    assert result.exit_code == 0, result.output
    assert "IDAPYTHON_VENV_EXECUTABLE is not set" in " ".join(result.stderr.split())

    result = _run(["--no-python-environment-check", "exec", "-c", "print('ok')"])
    assert result.exit_code == 0, result.output
    assert "IDAPYTHON_VENV_EXECUTABLE" not in result.stderr
