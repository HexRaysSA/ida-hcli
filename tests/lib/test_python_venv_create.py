import os
import subprocess
import sys
from pathlib import Path

import pytest

from hcli.lib.ida.python.environment import System, get_venv_python_path
from hcli.lib.ida.python.venv_create import (
    VenvCreationError,
    append_to_shell_profile,
    create_virtual_environment,
    detect_shell,
    get_shell_profile_path,
    inspect_target,
    plan_virtual_environment,
    render_profile_line,
    validate_created_virtual_environment,
    validate_python_version_string,
)

THIS_VERSION = f"{sys.version_info.major}.{sys.version_info.minor}"
SYSTEM: System = "windows" if os.name == "nt" else "linux"


@pytest.fixture(scope="session")
def seeded_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("target") / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(root)], check=True, capture_output=True)
    return root


def test_inspect_target_missing(tmp_path: Path):
    inspection = inspect_target(tmp_path / "venv", THIS_VERSION)
    assert inspection.kind == "missing"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert inspect_target(empty, THIS_VERSION).kind == "missing"


def test_inspect_target_rejects_files_and_unrelated_directories(tmp_path: Path):
    a_file = tmp_path / "venv"
    a_file.write_text("not a venv")
    assert inspect_target(a_file, THIS_VERSION).kind == "not-a-venv"

    a_dir = tmp_path / "notes"
    a_dir.mkdir()
    (a_dir / "readme.txt").write_text("hi")
    inspection = inspect_target(a_dir, THIS_VERSION)
    assert inspection.kind == "not-a-venv"
    assert "contains files" in inspection.reason


def test_inspect_target_recognizes_healthy_and_wrong_version_venvs(seeded_venv: Path):
    healthy = inspect_target(seeded_venv, THIS_VERSION)
    assert healthy.kind == "healthy-venv"
    assert healthy.version == THIS_VERSION
    assert healthy.python_exe is not None

    assert inspect_target(seeded_venv, None).kind == "healthy-venv"

    wrong = inspect_target(seeded_venv, "2.7")
    assert wrong.kind == "wrong-version-venv"
    assert wrong.version == THIS_VERSION
    assert "2.7" in wrong.reason


def test_inspect_target_flags_venv_without_interpreter(tmp_path: Path):
    root = tmp_path / "venv"
    root.mkdir()
    (root / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.12.0\n")
    inspection = inspect_target(root, "3.12")
    assert inspection.kind == "broken-venv"
    assert "interpreter is missing" in inspection.reason


def test_validate_python_version_string():
    validate_python_version_string("3.12")
    validate_python_version_string("3.9")
    for bad in ("3", "3.12.1", "py3.12", "", "3.x"):
        with pytest.raises(ValueError):
            validate_python_version_string(bad)


TARGET = Path("/idausr/venv")
REGISTERED = Path("/usr/bin/python3.12")
UV = Path("/usr/local/bin/uv")


def test_plan_prefers_uv_with_registered_python():
    plan = plan_virtual_environment(
        TARGET,
        "3.12",
        registered_python=REGISTERED,
        uv_exe=UV,
        path_python=Path("/other/python3.12"),
    )
    assert plan.tool == "uv"
    assert plan.build_command() == [str(UV), "venv", "--seed", "--python", str(REGISTERED), str(TARGET)]


def test_plan_uv_downloads_when_no_interpreter_is_known():
    plan = plan_virtual_environment(TARGET, "3.12", registered_python=None, uv_exe=Path("uv"), path_python=None)
    assert plan.build_command() == ["uv", "venv", "--seed", "--python", "3.12", str(TARGET)]


def test_plan_falls_back_to_stdlib_venv():
    plan = plan_virtual_environment(
        TARGET,
        "3.12",
        registered_python=None,
        uv_exe=None,
        path_python=REGISTERED,
    )
    assert plan.tool == "venv"
    assert plan.build_command() == [str(REGISTERED), "-m", "venv", str(TARGET)]


def test_plan_fails_without_uv_or_interpreter():
    with pytest.raises(VenvCreationError, match="uv is not installed"):
        plan_virtual_environment(Path("/idausr/venv"), "3.12", registered_python=None, uv_exe=None, path_python=None)


def test_create_virtual_environment_with_stdlib_venv(tmp_path: Path):
    target = tmp_path / "idausr" / "venv"
    plan = plan_virtual_environment(
        target,
        THIS_VERSION,
        registered_python=Path(sys.executable),
        uv_exe=None,
        path_python=None,
    )

    python_exe = create_virtual_environment(plan, SYSTEM)

    assert (target / "pyvenv.cfg").is_file()
    assert python_exe.is_file()
    assert python_exe.resolve() == get_venv_python_path(target, SYSTEM).resolve()
    assert subprocess.run([str(python_exe), "-c", "import pip"], capture_output=True, check=False).returncode == 0
    assert inspect_target(target, THIS_VERSION).kind == "healthy-venv"


def test_validate_created_virtual_environment_rejects_wrong_version(seeded_venv: Path):
    assert validate_created_virtual_environment(seeded_venv, THIS_VERSION).is_file()
    with pytest.raises(VenvCreationError, match=r"Python 2\.7 was required"):
        validate_created_virtual_environment(seeded_venv, "2.7")


def test_validate_created_virtual_environment_rejects_non_venv(tmp_path: Path):
    with pytest.raises(VenvCreationError, match=r"no pyvenv\.cfg"):
        validate_created_virtual_environment(tmp_path, THIS_VERSION)


def test_detect_shell_and_profile_paths():
    home = Path("/home/user")
    assert detect_shell("/bin/zsh") == "zsh"
    assert detect_shell("/usr/bin/fish") == "fish"
    assert detect_shell(None) == "unknown"
    assert detect_shell("/bin/nu") == "unknown"

    assert get_shell_profile_path("zsh", home) == home / ".zshrc"
    assert get_shell_profile_path("bash", home) == home / ".bashrc"
    assert get_shell_profile_path("fish", home) == home / ".config" / "fish" / "config.fish"
    assert get_shell_profile_path("unknown", home) is None


def test_render_profile_line_per_shell():
    assert render_profile_line("X", "/v/bin/python", "zsh") == 'export X="/v/bin/python"'
    assert render_profile_line("X", "/v/bin/python", "fish") == 'set -gx X "/v/bin/python"'


def test_append_to_shell_profile_is_idempotent(tmp_path: Path):
    profile = tmp_path / ".zshrc"
    line = 'export IDAPYTHON_VENV_EXECUTABLE="/v/bin/python"'

    assert append_to_shell_profile(profile, line)
    assert profile.read_text() == line + "\n"

    assert not append_to_shell_profile(profile, line)
    assert profile.read_text() == line + "\n"

    profile.write_text("alias ll='ls -l'")
    assert append_to_shell_profile(profile, line)
    assert profile.read_text() == "alias ll='ls -l'\n" + line + "\n"


def test_append_to_shell_profile_creates_parent_dirs(tmp_path: Path):
    profile = tmp_path / ".config" / "fish" / "config.fish"
    assert append_to_shell_profile(profile, 'set -gx X "1"')
    assert profile.read_text() == 'set -gx X "1"\n'
