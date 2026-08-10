"""Tests for finding and running scripts installed in a Python environment.

These build a real virtualenv containing a distribution that declares console
scripts, so no IDA installation is needed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path

import pytest

from hcli.lib.ida.python import (
    ProbeError,
    ScriptNotFoundError,
    get_environment_for_python,
    get_script_info,
    run_in_python_environment,
    run_script,
)

MODULE_PY = """
import sys


def main():
    print("hello from " + sys.argv[0] + ": " + " ".join(sys.argv[1:]))
    return 0
"""

METADATA = """Metadata-Version: 2.1
Name: hcli-test-pkg
Version: 1.2.3
"""

# - installed: has a wrapper in the scripts directory, like most installs
# - recorded: has a wrapper somewhere else, discoverable only via RECORD
# - shadowed: recorded, but another script of the same name sits in the scripts directory
# - orphan: declared, but no wrapper was installed anywhere
ENTRY_POINTS_TXT = """[console_scripts]
hcli-test-installed = hcli_test_pkg:main
hcli-test-recorded = hcli_test_pkg:main
hcli-test-shadowed = hcli_test_pkg:main
hcli-test-orphan = hcli_test_pkg:main
"""


@dataclass(frozen=True)
class PythonEnv:
    root: Path
    python_exe: Path
    scripts_dir: Path
    purelib: Path
    custom_bin: Path


def _get_venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _write_wrapper(directory: Path, name: str, python_exe: Path) -> Path:
    """Install a console script wrapper the way an installer would."""
    directory.mkdir(parents=True, exist_ok=True)

    impl = directory / f"{name}-impl.py"
    impl.write_text(f"import sys\nfrom hcli_test_pkg import main\nsys.argv[0] = {name!r}\nsys.exit(main())\n")

    if os.name == "nt":
        wrapper = directory / f"{name}.bat"
        wrapper.write_text(f'@echo off\n"{python_exe}" "{impl}" %*\n')
    else:
        wrapper = directory / name
        wrapper.write_text(f'#!/bin/sh\nexec "{python_exe}" "{impl}" "$@"\n')
        wrapper.chmod(0o755)

    return wrapper


@pytest.fixture(scope="session")
def python_env(tmp_path_factory) -> PythonEnv:
    root = tmp_path_factory.mktemp("python-env")
    venv_dir = root / "venv"
    venv.create(venv_dir, with_pip=False, symlinks=os.name != "nt")

    python_exe = _get_venv_python(venv_dir)
    paths = json.loads(
        subprocess.run(
            [
                str(python_exe),
                "-c",
                (
                    "import json, sysconfig; "
                    "print(json.dumps({'purelib': sysconfig.get_path('purelib'), "
                    "'scripts': sysconfig.get_path('scripts')}))"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )

    purelib = Path(paths["purelib"])
    scripts_dir = Path(paths["scripts"])
    custom_bin = root / "custom-bin"

    purelib.mkdir(parents=True, exist_ok=True)
    (purelib / "hcli_test_pkg.py").write_text(MODULE_PY)

    dist_info = purelib / "hcli_test_pkg-1.2.3.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(METADATA)
    (dist_info / "entry_points.txt").write_text(ENTRY_POINTS_TXT)

    _write_wrapper(scripts_dir, "hcli-test-installed", python_exe)
    _write_wrapper(scripts_dir, "hcli-test-shadowed", python_exe)
    recorded = [
        _write_wrapper(custom_bin, "hcli-test-recorded", python_exe),
        _write_wrapper(custom_bin, "hcli-test-shadowed", python_exe),
    ]

    # RECORD paths are relative to the directory containing the .dist-info
    lines = ["hcli_test_pkg.py,,"] + [f"{Path(os.path.relpath(p, purelib)).as_posix()},," for p in recorded]
    (dist_info / "RECORD").write_text("\n".join(lines) + "\n")

    return PythonEnv(
        root=root,
        python_exe=python_exe,
        scripts_dir=scripts_dir,
        purelib=purelib,
        custom_bin=custom_bin,
    )


def test_find_script_in_scripts_directory(python_env: PythonEnv):
    """A wrapper that RECORD doesn't mention is found in the scripts directory."""
    info = get_script_info(python_env.python_exe, "hcli-test-installed")
    assert info.path is not None
    assert info.path.parent.resolve() == python_env.scripts_dir.resolve()


def test_find_script_via_record(python_env: PythonEnv):
    """A wrapper installed outside the scripts directory is found via the distribution's RECORD."""
    info = get_script_info(python_env.python_exe, "hcli-test-recorded")
    assert info.path is not None
    assert info.path.parent.resolve() == python_env.custom_bin.resolve()


def test_find_script_prefers_record(python_env: PythonEnv):
    """RECORD names the wrapper belonging to the distribution, so it wins over a same-named file."""
    info = get_script_info(python_env.python_exe, "hcli-test-shadowed")
    assert info.path is not None
    assert info.path.parent.resolve() == python_env.custom_bin.resolve()


def test_find_script_reports_entry_point(python_env: PythonEnv):
    info = get_script_info(python_env.python_exe, "hcli-test-installed")
    assert info.entry_point is not None
    assert info.entry_point.value == "hcli_test_pkg:main"
    assert info.entry_point.group == "console_scripts"
    assert info.entry_point.distribution == "hcli-test-pkg"
    assert info.entry_point.version == "1.2.3"


def test_find_script_without_wrapper(python_env: PythonEnv):
    """A declared script with no wrapper on disk has no path, but is still described."""
    info = get_script_info(python_env.python_exe, "hcli-test-orphan")
    assert info.path is None
    assert info.entry_point is not None


def test_find_script_missing(python_env: PythonEnv):
    """Nothing found, and the reported search path stays inside the environment.

    A virtualenv's python links to the base interpreter, whose bin/ isn't the venv's.
    """
    info = get_script_info(python_env.python_exe, "hcli-test-missing")
    assert info.path is None
    assert info.entry_point is None
    assert info.scripts_dirs[0].resolve() == python_env.scripts_dir.resolve()


def test_get_script_info_bad_interpreter(tmp_path: Path):
    with pytest.raises(ProbeError):
        get_script_info(tmp_path / "does-not-exist", "hcli-test-installed")


def test_run_script_wrapper(python_env: PythonEnv, capfd):
    status = run_script(python_env.python_exe, "hcli-test-installed", ["alpha", "beta"])
    assert status == 0
    assert "hello from hcli-test-installed: alpha beta" in capfd.readouterr().out


def test_run_script_falls_back_to_entry_point(python_env: PythonEnv, capfd):
    """A script with no wrapper on disk is invoked through its entry point."""
    status = run_script(python_env.python_exe, "hcli-test-orphan", ["gamma"])
    assert status == 0
    assert "hello from hcli-test-orphan: gamma" in capfd.readouterr().out


def test_run_script_missing(python_env: PythonEnv):
    with pytest.raises(ScriptNotFoundError):
        run_script(python_env.python_exe, "hcli-test-missing")


def test_run_in_python_environment_returns_status(python_env: PythonEnv):
    argv = [str(python_env.python_exe), "-c", "import sys; sys.exit(7)"]
    assert run_in_python_environment(python_env.python_exe, argv) == 7


def test_get_environment_for_python_venv(python_env: PythonEnv, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(sys.prefix)))
    monkeypatch.setenv("PYTHONHOME", "/nonsense")

    env = get_environment_for_python(python_env.python_exe)

    assert env["VIRTUAL_ENV"] == str(python_env.root / "venv")
    assert "PYTHONHOME" not in env
    assert env["PATH"].split(os.pathsep)[0] == str(python_env.python_exe.parent)


def test_get_environment_for_python_not_a_venv(tmp_path: Path, monkeypatch):
    """hcli's own virtualenv must not leak into a system interpreter."""
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(sys.prefix)))

    env = get_environment_for_python(tmp_path / "bin" / "python")

    assert "VIRTUAL_ENV" not in env
