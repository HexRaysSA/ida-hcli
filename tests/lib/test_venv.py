import os
from pathlib import Path

from hcli.lib.venv import (
    find_candidate_virtual_envs,
    find_virtual_env_python,
    get_python_exe_candidates,
    is_uv_cache_virtual_env,
    read_virtual_env_version,
    resolve_user_virtual_env,
)


def _write_pyvenv_cfg(venv_dir: Path, content: str) -> None:
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "pyvenv.cfg").write_text(content, encoding="utf-8")
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(exist_ok=True)


def test_is_uv_cache_detects_uv_overlay(tmp_path):
    cache_dir = tmp_path / ".cache" / "uv" / "archive-v0" / "abc123"
    _write_pyvenv_cfg(
        cache_dir,
        "home = /usr/bin\nuv = 0.7.16\nrelocatable = true\nextends-environment = /home/user/.venv\n",
    )
    assert is_uv_cache_virtual_env(cache_dir) is True


def test_is_uv_cache_detects_extends_environment(tmp_path):
    venv = tmp_path / "ephemeral"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /home/user/.venv\n")
    assert is_uv_cache_virtual_env(venv) is True


def test_is_uv_cache_rejects_relocatable_user_venv(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\nrelocatable = true\n")
    assert is_uv_cache_virtual_env(venv) is False


def test_is_uv_cache_rejects_user_uv_venv(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\n")
    assert is_uv_cache_virtual_env(venv) is False


def test_is_uv_cache_rejects_stdlib_venv(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\ninclude-system-site-packages = false\n")
    assert is_uv_cache_virtual_env(venv) is False


def test_is_uv_cache_rejects_missing_pyvenv_cfg(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    assert is_uv_cache_virtual_env(venv) is False


def test_is_uv_cache_detects_archive_v0_parent(tmp_path):
    venv = tmp_path / "tmpXXXXXX" / "archive-v0" / "abc123" / "lib" / "python3.12"
    _write_pyvenv_cfg(venv.parent.parent, "home = /usr/bin\nuv = 0.7.16\n")
    assert is_uv_cache_virtual_env(venv.parent.parent) is True


def test_is_uv_cache_detects_builds_v0_parent(tmp_path):
    venv = tmp_path / "tmpXXXXXX" / "builds-v0" / "abc123"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\n")
    assert is_uv_cache_virtual_env(venv) is True


def test_is_uv_cache_rejects_nonexistent_path(tmp_path):
    assert is_uv_cache_virtual_env(tmp_path / "does-not-exist") is False


def test_find_candidates_discovers_venv_on_path(tmp_path, monkeypatch):
    venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    fake_prefix = tmp_path / "hcli-venv"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))

    monkeypatch.setenv("PATH", str(venv / "bin"))

    candidates = find_candidate_virtual_envs()
    assert len(candidates) == 1
    assert candidates[0].path == venv
    assert candidates[0].source == "PATH"


def test_find_candidates_excludes_current_prefix(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(venv)}))
    monkeypatch.setenv("PATH", str(venv / "bin"))

    candidates = find_candidate_virtual_envs()
    assert len(candidates) == 0


def test_find_candidates_deduplicates(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    fake_prefix = tmp_path / "other"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))

    path = os.pathsep.join([str(venv / "bin"), str(venv / "bin")])
    monkeypatch.setenv("PATH", path)

    candidates = find_candidate_virtual_envs()
    assert len(candidates) == 1


def test_find_candidates_skips_non_venv_bin_dirs(tmp_path, monkeypatch):
    bin_dir = tmp_path / "just-a-bin" / "bin"
    bin_dir.mkdir(parents=True)

    fake_prefix = tmp_path / "other"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))
    monkeypatch.setenv("PATH", str(bin_dir))

    candidates = find_candidate_virtual_envs()
    assert len(candidates) == 0


def test_resolve_user_venv_returns_non_uv_virtual_env(tmp_path, monkeypatch):
    user_venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(user_venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(user_venv))
    fake_prefix = tmp_path / "other"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))

    assert resolve_user_virtual_env() == user_venv


def test_resolve_user_venv_returns_none_when_hcli_own(tmp_path, monkeypatch):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(venv)}))

    assert resolve_user_virtual_env() is None


def test_resolve_user_venv_recovers_from_uv_cache(tmp_path, monkeypatch):
    uv_cache_venv = tmp_path / "uv-cache"
    _write_pyvenv_cfg(uv_cache_venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /somewhere\n")

    user_venv = tmp_path / "project" / ".venv"
    _write_pyvenv_cfg(user_venv, "home = /usr/bin\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(uv_cache_venv))
    monkeypatch.setenv("PATH", os.pathsep.join([str(uv_cache_venv / "bin"), str(user_venv / "bin")]))

    fake_prefix = tmp_path / "other"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))

    assert resolve_user_virtual_env() == user_venv


def test_resolve_user_venv_skips_uv_cache_candidates(tmp_path, monkeypatch):
    uv_cache_venv = tmp_path / "uv-cache"
    _write_pyvenv_cfg(uv_cache_venv, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /somewhere\n")

    uv_archive = tmp_path / "uv-archive"
    _write_pyvenv_cfg(uv_archive, "home = /usr/bin\nuv = 0.7.16\nextends-environment = /elsewhere\n")

    monkeypatch.setenv("VIRTUAL_ENV", str(uv_cache_venv))
    monkeypatch.setenv("PATH", str(uv_archive / "bin"))

    fake_prefix = tmp_path / "other"
    fake_prefix.mkdir()
    monkeypatch.setattr("hcli.lib.venv.sys", type("FakeSys", (), {"prefix": str(fake_prefix)}))

    assert resolve_user_virtual_env() is None


def test_resolve_user_venv_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert resolve_user_virtual_env() is None


def _venv_bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def _write_venv_python(venv_dir: Path, name: str) -> Path:
    bin_dir = _venv_bin_dir(venv_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    python = bin_dir / name
    python.write_text("", encoding="utf-8")
    return python


def test_find_virtual_env_python_finds_interpreter(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")
    python = _write_venv_python(venv, "python.exe" if os.name == "nt" else "python3")

    assert find_virtual_env_python(venv) == python


def test_find_virtual_env_python_falls_back_to_unversioned_name(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")
    python = _write_venv_python(venv, "python.exe" if os.name == "nt" else "python")

    assert find_virtual_env_python(venv) == python


def test_find_virtual_env_python_returns_none_when_missing(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    assert find_virtual_env_python(venv) is None


def test_get_python_exe_candidates_prefers_versioned_name(tmp_path):
    candidates = get_python_exe_candidates(tmp_path, "3.12")

    if os.name == "nt":
        assert candidates == [tmp_path / "Scripts" / "python.exe", tmp_path / "python.exe"]
    else:
        assert candidates == [
            tmp_path / "bin" / "python3.12",
            tmp_path / "bin" / "python3",
            tmp_path / "bin" / "python",
        ]


def test_read_virtual_env_version_reads_stdlib_version_key(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nversion = 3.12.7\n")

    assert read_virtual_env_version(venv) == "3.12"


def test_read_virtual_env_version_reads_uv_version_info_key(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nuv = 0.7.16\nversion_info = 3.14.0\n")

    assert read_virtual_env_version(venv) == "3.14"


def test_read_virtual_env_version_returns_none_without_version(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\n")

    assert read_virtual_env_version(venv) is None


def test_read_virtual_env_version_returns_none_when_malformed(tmp_path):
    venv = tmp_path / ".venv"
    _write_pyvenv_cfg(venv, "home = /usr/bin\nversion = unknown\n")

    assert read_virtual_env_version(venv) is None


def test_read_virtual_env_version_returns_none_without_pyvenv_cfg(tmp_path):
    assert read_virtual_env_version(tmp_path / "missing") is None
