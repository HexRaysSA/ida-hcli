import os
import types
from pathlib import Path

import pytest

from hcli.lib.venv import (
    find_candidate_virtual_envs,
    find_virtual_env_python,
    is_uv_cache_virtual_env,
    read_virtual_env_version,
    resolve_user_virtual_env,
)


def _write_pyvenv_cfg(venv_dir: Path, content: str) -> None:
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(content, encoding="utf-8")
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(exist_ok=True)


def _fake_hcli_prefix(monkeypatch, prefix: Path) -> None:
    monkeypatch.setattr("hcli.lib.venv.sys", types.SimpleNamespace(prefix=str(prefix)))


def test_is_uv_cache_detects_extends_environment(tmp_path):
    venv = tmp_path / "ephemeral"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /home/user/.venv\n")
    assert is_uv_cache_virtual_env(venv) is True


@pytest.mark.parametrize("internal_dir", ["archive-v0", "builds-v0"])
def test_is_uv_cache_detects_internal_cache_layout(tmp_path, internal_dir):
    venv = tmp_path / "tmpXXXXXX" / internal_dir / "abc123"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\n")
    assert is_uv_cache_virtual_env(venv) is True


@pytest.mark.parametrize(
    "cfg",
    [
        "home = /usr/bin\nuv = 0.7.16\nrelocatable = true\n",
        "home = /usr/bin\nuv = 0.7.16\n",
        "home = /usr/bin\ninclude-system-site-packages = false\n",
        None,
    ],
)
def test_is_uv_cache_rejects_user_virtual_envs(tmp_path, cfg):
    venv = tmp_path / ".venv"
    if cfg is None:
        venv.mkdir()
    else:
        _write_pyvenv_cfg(venv, cfg)
    assert is_uv_cache_virtual_env(venv) is False


def test_is_uv_cache_rejects_nonexistent_path(tmp_path):
    assert is_uv_cache_virtual_env(tmp_path / "does-not-exist") is False


def test_find_candidates_reports_other_venvs_on_path_once(tmp_path, monkeypatch):
    user_venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(user_venv, "home = /usr/bin\n")

    own_venv = tmp_path / "hcli-venv"
    _write_pyvenv_cfg(own_venv, "home = /usr/bin\n")
    _fake_hcli_prefix(monkeypatch, own_venv)

    plain_bin = tmp_path / "just-a-bin" / "bin"
    plain_bin.mkdir(parents=True)

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [
                "",
                str(tmp_path / "missing" / "bin"),
                str(tmp_path / "usr" / "local"),
                str(plain_bin),
                str(own_venv / "bin"),
                str(user_venv / "bin"),
                str(user_venv / "bin"),
            ]
        ),
    )

    candidates = find_candidate_virtual_envs()
    assert [c.path for c in candidates] == [user_venv]
    assert candidates[0].source == "PATH"


def test_resolve_user_venv_returns_non_uv_virtual_env(tmp_path, monkeypatch):
    user_venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(user_venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(user_venv))
    _fake_hcli_prefix(monkeypatch, tmp_path / "other")

    assert resolve_user_virtual_env() == user_venv


def test_resolve_user_venv_returns_none_when_hcli_own(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    _fake_hcli_prefix(monkeypatch, venv)

    assert resolve_user_virtual_env() is None


def test_resolve_user_venv_recovers_from_uv_cache(tmp_path, monkeypatch):
    uv_cache_venv = tmp_path / "uv-cache"
    _write_pyvenv_cfg(uv_cache_venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /somewhere\n")

    user_venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(user_venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(uv_cache_venv))
    monkeypatch.setenv("PATH", os.pathsep.join([str(uv_cache_venv / "bin"), str(user_venv / "bin")]))
    _fake_hcli_prefix(monkeypatch, tmp_path / "other")

    assert resolve_user_virtual_env() == user_venv


def test_resolve_user_venv_skips_uv_cache_candidates(tmp_path, monkeypatch):
    uv_cache_venv = tmp_path / "uv-cache"
    _write_pyvenv_cfg(uv_cache_venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /somewhere\n")

    uv_archive = tmp_path / "uv-archive"
    _write_pyvenv_cfg(uv_archive, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /elsewhere\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(uv_cache_venv))
    monkeypatch.setenv("PATH", str(uv_archive / "bin"))
    _fake_hcli_prefix(monkeypatch, tmp_path / "other")

    assert resolve_user_virtual_env() is None


def test_resolve_user_venv_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert resolve_user_virtual_env() is None


@pytest.mark.parametrize("name", ["python.exe"] if os.name == "nt" else ["python3", "python"])
def test_find_virtual_env_python_finds_interpreter(tmp_path, name):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = bin_dir / name
    python.write_text("", encoding="utf-8")

    assert find_virtual_env_python(venv) == python


def test_find_virtual_env_python_returns_none_when_missing(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    assert find_virtual_env_python(venv) is None


@pytest.mark.parametrize(
    "cfg,expected",
    [
        ("home = /usr/bin\nversion = 3.12.7\n", "3.12"),
        ("home = /usr/bin\nuv = 0.7.16\nversion_info = 3.14.0\n", "3.14"),
        ("home = /usr/bin\n", None),
        ("home = /usr/bin\nversion = unknown\n", None),
        ("home = /usr/bin\nversion = 3\n", None),
    ],
)
def test_read_virtual_env_version(tmp_path, cfg, expected):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, cfg)

    assert read_virtual_env_version(venv) == expected


def test_read_virtual_env_version_returns_none_without_pyvenv_cfg(tmp_path):
    assert read_virtual_env_version(tmp_path / "missing") is None
