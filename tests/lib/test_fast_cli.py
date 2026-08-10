from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from hcli import fast_cli


def _write_python_cache(cache_root: Path, python_exe: Path, tracked_path: Path) -> None:
    fingerprint = {
        "paths": [fast_cli._path_signature(tracked_path)],
        "environment_hash": fast_cli._environment_hash(),
        "windows_idapython_target": None,
    }
    doc = {
        "version": 1,
        "created_at": time.time(),
        "key": fast_cli._python_cache_key(fingerprint),
        "fingerprint": fingerprint,
        "python_exe": str(python_exe),
        "info": {},
    }
    path = cache_root / "ida" / "python-info.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_fast_find_script_uses_validated_caches(tmp_path, monkeypatch, capsys):
    cache_root = tmp_path / "cache"
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    tracked_path = tmp_path / "ida-config"
    tracked_path.write_text("configured", encoding="utf-8")
    script = tmp_path / "worker"
    script.write_text("", encoding="utf-8")
    metadata = tmp_path / "entry_points.txt"
    metadata.write_text("worker = package:main", encoding="utf-8")

    monkeypatch.setenv("HCLI_CACHE_DIR", str(cache_root))
    monkeypatch.delenv("HCLI_DEBUG", raising=False)
    monkeypatch.setattr(fast_cli, "_windows_idapython_target", lambda: None)
    _write_python_cache(cache_root, python_exe, tracked_path)
    fast_cli.cache_script_result(python_exe, "worker", script, (metadata,))

    assert fast_cli.try_fast_find_script(["ida", "python", "find-script", "worker"])
    assert capsys.readouterr().out.strip() == str(script)


def test_fast_run_script_forwards_arguments_and_status(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    python_exe = Path(sys.executable)
    tracked_path = tmp_path / "ida-config"
    tracked_path.write_text("configured", encoding="utf-8")
    metadata = tmp_path / "entry_points.txt"
    metadata.write_text("worker = package:main", encoding="utf-8")

    monkeypatch.setenv("HCLI_CACHE_DIR", str(cache_root))
    monkeypatch.delenv("HCLI_DEBUG", raising=False)
    monkeypatch.setattr(fast_cli, "_windows_idapython_target", lambda: None)
    _write_python_cache(cache_root, python_exe, tracked_path)
    fast_cli.cache_script_result(python_exe, "worker", python_exe, (metadata,))

    status = fast_cli.try_fast_run_script(
        ["ida", "python", "run-script", "worker", "--", "-c", "import sys; sys.exit(7)"]
    )
    assert status == 7


def test_fast_find_script_rejects_changed_metadata(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    tracked_path = tmp_path / "ida-config"
    tracked_path.write_text("configured", encoding="utf-8")
    script = tmp_path / "worker"
    script.write_text("", encoding="utf-8")
    metadata = tmp_path / "entry_points.txt"
    metadata.write_text("worker = package:main", encoding="utf-8")

    monkeypatch.setenv("HCLI_CACHE_DIR", str(cache_root))
    monkeypatch.delenv("HCLI_DEBUG", raising=False)
    monkeypatch.setattr(fast_cli, "_windows_idapython_target", lambda: None)
    _write_python_cache(cache_root, python_exe, tracked_path)
    fast_cli.cache_script_result(python_exe, "worker", script, (metadata,))

    metadata.write_text("worker = other_package:main", encoding="utf-8")
    assert not fast_cli.try_fast_find_script(["ida", "python", "find-script", "worker"])


def test_fast_find_script_is_disabled_for_debugging(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    tracked_path = tmp_path / "ida-config"
    tracked_path.write_text("configured", encoding="utf-8")

    monkeypatch.setenv("HCLI_CACHE_DIR", str(cache_root))
    monkeypatch.setenv("HCLI_DEBUG", "1")
    monkeypatch.setattr(fast_cli, "_windows_idapython_target", lambda: None)
    _write_python_cache(cache_root, python_exe, tracked_path)

    assert not fast_cli.try_fast_find_script(["ida", "python", "find-script", "worker"])


def test_python_cache_expires(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    python_exe = tmp_path / "python"
    python_exe.write_text("", encoding="utf-8")
    tracked_path = tmp_path / "ida-config"
    tracked_path.write_text("configured", encoding="utf-8")

    monkeypatch.setenv("HCLI_CACHE_DIR", str(cache_root))
    monkeypatch.delenv("HCLI_DEBUG", raising=False)
    monkeypatch.setattr(fast_cli, "_windows_idapython_target", lambda: None)
    _write_python_cache(cache_root, python_exe, tracked_path)
    cache_path = cache_root / "ida" / "python-info.json"
    doc = json.loads(cache_path.read_text(encoding="utf-8"))
    doc["created_at"] = time.time() - fast_cli._CACHE_MAX_AGE_SECONDS - 1
    cache_path.write_text(json.dumps(doc), encoding="utf-8")

    assert fast_cli._load_valid_python_cache() is None
