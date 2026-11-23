"""Tests for the idapro-based Python finder.

Only the pure derivation steps are covered: reading Python3TargetDLL needs a
real libida, but everything downstream of the libpython path is plain path and
filename logic that can be driven from a tmp_path layout.
"""

import sys

import pytest

from hcli.lib.ida.python import PythonNotFoundError, _may_run_in_virtualenv, idapro


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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX layout")
def test_find_python_executable_derives_from_the_configured_libpython(tmp_path, monkeypatch):
    _, libpython, exe = _make_posix_prefix(tmp_path)
    monkeypatch.setattr(idapro, "_read_target_dll", lambda: str(libpython))

    assert idapro.find_python_executable() == exe


def test_find_python_executable_without_a_configured_libpython(monkeypatch):
    monkeypatch.setattr(idapro, "_read_target_dll", lambda: None)

    with pytest.raises(PythonNotFoundError, match=idapro.REG_VALUE):
        idapro.find_python_executable()


def test_find_python_executable_with_an_unrecognized_libpython(monkeypatch):
    monkeypatch.setattr(idapro, "_read_target_dll", lambda: "/usr/lib/libcrypto.so.3")

    with pytest.raises(PythonNotFoundError, match="Python version"):
        idapro.find_python_executable()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX layout")
def test_find_python_executable_without_a_stdlib_around_the_library(tmp_path, monkeypatch):
    orphan = tmp_path / "libpython3.14.so.1.0"
    orphan.write_text("", encoding="utf-8")
    monkeypatch.setattr(idapro, "_read_target_dll", lambda: str(orphan))

    with pytest.raises(PythonNotFoundError, match="Python installation"):
        idapro.find_python_executable()


def test_rc_file_that_only_binds_keys_keeps_the_fast_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    (tmp_path / "idapythonrc.py").write_text("import ida_kernwin\n", encoding="utf-8")

    assert not _may_run_in_virtualenv()


@pytest.mark.parametrize("line", ["sys.prefix = venv", "site.addsitedir(x)", "exec(activate_this)"])
def test_rc_file_that_moves_the_interpreter_forces_the_probe(tmp_path, monkeypatch, line):
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    (tmp_path / "idapythonrc.py").write_text(line + "\n", encoding="utf-8")

    assert _may_run_in_virtualenv()


def test_unactivated_path_venv_keeps_the_fast_path(tmp_path, monkeypatch):
    """`uv run` puts a venv on $PATH without activating it in the shell."""
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path))
    bin_dir = tmp_path / "venv" / ("Scripts" if sys.platform == "win32" else "bin")
    bin_dir.mkdir(parents=True)
    (bin_dir.parent / "pyvenv.cfg").write_text("home = /base\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("VIRTUAL_ENV_PROMPT", raising=False)

    assert not _may_run_in_virtualenv()
