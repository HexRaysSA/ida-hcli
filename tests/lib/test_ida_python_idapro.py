"""Tests for the idapro-based Python finder.

Only the pure derivation steps are covered: reading Python3TargetDLL needs a
real libida, but everything downstream of the libpython path is plain path and
filename logic that can be driven from a tmp_path layout.
"""

import os
import sys

import pytest

from hcli.lib.ida.python import idapro


def _make_posix_prefix(tmp_path, version="3.14", suffix=""):
    """Build a CPython-shaped prefix: lib/pythonX.Y/os.py plus bin/pythonX.Y."""
    prefix = tmp_path / "prefix"
    stdlib = prefix / "lib" / f"python{version}{suffix}"
    stdlib.mkdir(parents=True)
    (stdlib / "os.py").write_text("", encoding="utf-8")

    bin_dir = prefix / "bin"
    bin_dir.mkdir()
    exe = bin_dir / f"python{version}{suffix}"
    exe.write_text("", encoding="utf-8")

    libdir = prefix / "lib"
    libpython = libdir / f"libpython{version}{suffix}.so.1.0"
    libpython.write_text("", encoding="utf-8")

    return prefix, libpython, exe


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("libpython3.14.so.1.0", (3, 14, "")),
        ("libpython3.9.so", (3, 9, "")),
        ("libpython3.13d.so.1.0", (3, 13, "d")),
        ("libpython3.13t.so", (3, 13, "t")),
        ("python314.dll", (3, 14, "")),
        ("python313_d.dll", (3, 13, "_d")),
        ("PYTHON311.DLL", (3, 11, "")),
        ("libcrypto.so.3", None),
        ("", None),
    ],
)
def test_version_of(name, expected):
    assert idapro._version_of(f"/some/dir/{name}") == expected


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stdlib layout")
def test_prefix_of_walks_up_to_the_stdlib_landmark(tmp_path):
    prefix, libpython, _exe = _make_posix_prefix(tmp_path)

    assert idapro._prefix_of(str(libpython), (3, 14, "")) == str(prefix)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stdlib layout")
def test_prefix_of_returns_none_without_a_landmark(tmp_path):
    orphan = tmp_path / "lib" / "libpython3.14.so.1.0"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("", encoding="utf-8")

    assert idapro._prefix_of(str(orphan), (3, 14, "")) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stdlib layout")
def test_prefix_of_accepts_unsuffixed_stdlib_for_debug_builds(tmp_path):
    """Debian debug builds share the release stdlib directory."""
    prefix = tmp_path / "prefix"
    stdlib = prefix / "lib" / "python3.13"
    stdlib.mkdir(parents=True)
    (stdlib / "os.py").write_text("", encoding="utf-8")
    libpython = prefix / "lib" / "libpython3.13d.so.1.0"
    libpython.write_text("", encoding="utf-8")

    assert idapro._prefix_of(str(libpython), (3, 13, "d")) == str(prefix)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX interpreter layout")
def test_executable_of_prefers_the_versioned_interpreter(tmp_path):
    prefix, _libpython, exe = _make_posix_prefix(tmp_path)
    # a bare `python` alongside it must not win
    (prefix / "bin" / "python").write_text("", encoding="utf-8")

    assert idapro._executable_of(str(prefix), (3, 14, "")) == str(exe)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX interpreter layout")
def test_executable_of_falls_back_to_bare_python(tmp_path):
    prefix = tmp_path / "prefix"
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    fallback = bin_dir / "python"
    fallback.write_text("", encoding="utf-8")

    assert idapro._executable_of(str(prefix), (3, 14, "")) == str(fallback)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX interpreter layout")
def test_executable_of_returns_none_when_absent(tmp_path):
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)

    assert idapro._executable_of(str(prefix), (3, 14, "")) is None


def _make_venv(tmp_path, name="venv", cfg_version="3.14.2", cfg_key="version_info"):
    """A venv shaped the way detect_venv() looks for one: bin/python + ../pyvenv.cfg."""
    venv = tmp_path / name
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    exe = bin_dir / ("python.exe" if os.name == "nt" else "python")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)  # shutil.which, like search_path, needs it executable
    (venv / "pyvenv.cfg").write_text(f"home = /base\n{cfg_key} = {cfg_version}\n", encoding="utf-8")
    return venv, exe


def _activate(monkeypatch, venv, shell_activated=True):
    """Put the venv on $PATH the way the activate script does."""
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("IDAPYTHON_VENV_EXECUTABLE", raising=False)
    if shell_activated:
        monkeypatch.setenv("VIRTUAL_ENV_PROMPT", venv.name)
    else:
        monkeypatch.delenv("VIRTUAL_ENV_PROMPT", raising=False)


def test_detect_venv_finds_python_on_path(tmp_path, monkeypatch):
    """detect_venv()'s second branch: `python` on $PATH with ../pyvenv.cfg."""
    venv, exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv)

    assert idapro._detect_venv() == (exe, False)


def test_detect_venv_prefers_the_explicit_env_var(tmp_path, monkeypatch):
    """detect_venv()'s first branch wins, and needs no pyvenv.cfg at all."""
    venv, _exe = _make_venv(tmp_path)
    other = tmp_path / "elsewhere" / "python"
    other.parent.mkdir(parents=True)
    other.write_text("", encoding="utf-8")
    other.chmod(0o755)

    _activate(monkeypatch, venv)
    monkeypatch.setenv("IDAPYTHON_VENV_EXECUTABLE", str(other))

    assert idapro._detect_venv() == (other, True)


def test_detect_venv_ignores_a_nonexistent_explicit_path(tmp_path, monkeypatch):
    venv, exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv)
    monkeypatch.setenv("IDAPYTHON_VENV_EXECUTABLE", str(tmp_path / "missing" / "python"))

    assert idapro._detect_venv() == (exe, False)


def test_detect_venv_ignores_a_python_that_is_not_in_a_venv(tmp_path, monkeypatch):
    """No ../pyvenv.cfg means it is a plain interpreter, not a venv."""
    bin_dir = tmp_path / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    plain = bin_dir / ("python.exe" if os.name == "nt" else "python")
    plain.write_text("", encoding="utf-8")
    plain.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("IDAPYTHON_VENV_EXECUTABLE", raising=False)

    assert idapro._detect_venv() is None


def test_detect_venv_without_python_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("IDAPYTHON_VENV_EXECUTABLE", raising=False)

    assert idapro._detect_venv() is None


def test_matching_user_venv_python_returns_the_venv_when_versions_agree(tmp_path, monkeypatch):
    venv, exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv)

    assert idapro._matching_user_venv_python((3, 14, "")) == exe


def test_matching_user_venv_python_ignores_an_unactivated_path_venv(tmp_path, monkeypatch):
    """`uv run hcli` puts a venv on $PATH without the user working in it."""
    venv, _exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv, shell_activated=False)

    assert idapro._matching_user_venv_python((3, 14, "")) is None


def test_matching_user_venv_python_trusts_the_explicit_env_var_unactivated(tmp_path, monkeypatch):
    """IDAPYTHON_VENV_EXECUTABLE is deliberate, so it needs no shell activation."""
    venv, exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv, shell_activated=False)
    monkeypatch.setenv("IDAPYTHON_VENV_EXECUTABLE", str(exe))

    assert idapro._matching_user_venv_python((3, 14, "")) == exe


def test_matching_user_venv_python_ignores_a_mismatched_venv(tmp_path, monkeypatch):
    """A venv built on another version cannot be the one IDA's libpython loads."""
    venv, _exe = _make_venv(tmp_path, cfg_version="3.11.9")
    _activate(monkeypatch, venv)

    assert idapro._matching_user_venv_python((3, 14, "")) is None


@pytest.mark.parametrize("modifiers", ["t", "d"])
def test_matching_user_venv_python_refuses_abi_suffixed_libpython(tmp_path, monkeypatch, modifiers):
    """pyvenv.cfg records no ABI suffix, so 3.14 must not match libpython3.14t."""
    venv, _exe = _make_venv(tmp_path)
    _activate(monkeypatch, venv)

    assert idapro._matching_user_venv_python((3, 14, modifiers)) is None


def test_matching_user_venv_python_reads_the_stdlib_venv_key(tmp_path, monkeypatch):
    """The stdlib venv module writes `version`, uv writes `version_info`."""
    venv, exe = _make_venv(tmp_path, cfg_version="3.14.2", cfg_key="version")
    _activate(monkeypatch, venv)

    assert idapro._matching_user_venv_python((3, 14, "")) == exe


def test_matching_user_venv_python_without_a_version_in_pyvenv_cfg(tmp_path, monkeypatch):
    venv, _exe = _make_venv(tmp_path)
    (venv / "pyvenv.cfg").write_text("home = /base\n", encoding="utf-8")
    _activate(monkeypatch, venv)

    assert idapro._matching_user_venv_python((3, 14, "")) is None


def test_matching_user_venv_python_without_any_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("IDAPYTHON_VENV_EXECUTABLE", raising=False)

    assert idapro._matching_user_venv_python((3, 14, "")) is None
