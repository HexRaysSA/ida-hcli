import platform

import pytest

from hcli.lib.ida import _get_clean_ida_subprocess_env


def test_get_clean_ida_subprocess_env_removes_python_env_vars():
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONHOME": "/python-home",
        "PYTHONPATH": "/python-path",
        "VIRTUAL_ENV": "/tmp/venv",
        "__PYVENV_LAUNCHER__": "/tmp/venv/bin/python",
        "KEEP_ME": "1",
    }

    result = _get_clean_ida_subprocess_env(env)

    assert "PYTHONHOME" not in result
    assert "PYTHONPATH" not in result
    assert "VIRTUAL_ENV" not in result
    assert "__PYVENV_LAUNCHER__" not in result
    assert result["KEEP_ME"] == "1"
    assert result["PATH"] == env["PATH"]


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows-specific test")
def test_get_clean_ida_subprocess_env_removes_virtualenv_scripts_from_path():
    env = {
        "PATH": r"C:\repo\.venv\Scripts;C:\Windows\System32;C:\Tools",
        "VIRTUAL_ENV": r"C:\repo\.venv",
    }

    result = _get_clean_ida_subprocess_env(env)

    assert result["PATH"] == r"C:\Windows\System32;C:\Tools"
