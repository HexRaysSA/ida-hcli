"""Minimal, standard-library-only fast paths for latency-sensitive commands."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_CACHE_VERSION = 1
_CACHE_MAX_AGE_SECONDS = 300
_CACHE_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _cache_enabled() -> bool:
    debug = os.environ.get("HCLI_DEBUG", "").strip().lower() in _CACHE_TRUE_VALUES
    disabled = os.environ.get("HCLI_DISABLE_PYTHON_CACHE", "").strip().lower() in _CACHE_TRUE_VALUES
    return not debug and not disabled


def _cache_directory() -> Path:
    override = os.environ.get("HCLI_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache" / "hex-rays" / "hcli"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "hex-rays" / "hcli"
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "hex-rays" / "hcli" / "cache"
    raise ValueError(f"unsupported platform: {sys.platform}")


def _path_signature(path: Path) -> list[str | int | None]:
    try:
        stat = path.stat()
    except OSError:
        return [str(path.absolute()), None, None]
    return [str(path.absolute()), stat.st_mtime_ns, stat.st_size]


def _environment_hash() -> str:
    encoded = json.dumps(sorted(os.environ.items()), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _windows_idapython_target() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Hex-Rays\IDA") as key:
            value, _ = winreg.QueryValueEx(key, "Python3TargetDLL")
            return str(value)
    except (OSError, ImportError):
        return None


def _python_cache_key(fingerprint: dict) -> str:
    encoded = json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_valid_python_cache() -> dict | None:
    if not _cache_enabled():
        return None
    try:
        doc = json.loads((_cache_directory() / "ida" / "python-info.json").read_text(encoding="utf-8"))
        if doc.get("version") != _CACHE_VERSION:
            return None
        age = time.time() - float(doc["created_at"])
        if age < 0 or age > _CACHE_MAX_AGE_SECONDS:
            return None
        fingerprint = doc["fingerprint"]
        if fingerprint.get("environment_hash") != _environment_hash():
            return None
        if fingerprint.get("windows_idapython_target") != _windows_idapython_target():
            return None
        for expected in (*fingerprint["paths"], *doc.get("result_paths", ())):
            if _path_signature(Path(expected[0])) != expected:
                return None
        if doc.get("key") != _python_cache_key(fingerprint):
            return None
        if not Path(doc["python_exe"]).is_file():
            return None
        return doc
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None


def _script_cache_path(name: str) -> Path:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    return _cache_directory() / "ida" / "scripts" / f"{digest}.json"


def cache_script_result(python_exe: Path, name: str, path: Path, dependency_paths: tuple[Path, ...]) -> None:
    """Cache an exact, metadata-verified script result for the lightweight entrypoint."""
    python_doc = _load_valid_python_cache()
    if python_doc is None or os.path.normcase(os.path.abspath(python_doc["python_exe"])) != os.path.normcase(
        os.path.abspath(python_exe)
    ):
        return

    cache_path = _script_cache_path(name)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tracked_paths = tuple(dict.fromkeys((path, *dependency_paths)))
    doc = {
        "version": _CACHE_VERSION,
        "python_key": python_doc["key"],
        "name": name,
        "path": str(path),
        "paths": [_path_signature(item) for item in tracked_paths],
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=cache_path.parent, prefix="script-", suffix=".tmp", delete=False
        ) as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
            temporary_path = Path(f.name)
        os.replace(temporary_path, cache_path)
    except OSError:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _find_cached_script(name: str) -> Path | None:
    python_doc = _load_valid_python_cache()
    if python_doc is None:
        return None
    try:
        doc = json.loads(_script_cache_path(name).read_text(encoding="utf-8"))
        if (
            doc.get("version") != _CACHE_VERSION
            or doc.get("python_key") != python_doc["key"]
            or doc.get("name") != name
        ):
            return None
        for expected in doc["paths"]:
            if _path_signature(Path(expected[0])) != expected:
                return None
        path = Path(doc["path"])
        return path if path.is_file() else None
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, UnicodeError):
        return None


def _command_args(argv: list[str]) -> list[str]:
    args = list(argv)
    while args and args[0] in ("--disable-updates", "--quiet", "-q"):
        args.pop(0)
    return args


def parse_find_script_invocation(argv: list[str]) -> str | None:
    """Return the requested script name for a simple find-script invocation."""
    args = _command_args(argv)
    if len(args) != 4 or args[:3] != ["ida", "python", "find-script"]:
        return None
    return args[3]


def parse_run_script_invocation(argv: list[str]) -> tuple[str, list[str]] | None:
    """Return the script name and arguments for a simple run-script invocation."""
    args = _command_args(argv)
    if len(args) < 4 or args[:3] != ["ida", "python", "run-script"]:
        return None
    script_args = args[4:]
    if script_args[:1] == ["--"]:
        script_args = script_args[1:]
    return args[3], script_args


def try_fast_find_script(argv: list[str]) -> bool:
    """Handle a cache-valid ``ida python find-script`` invocation without importing Click."""
    if not _cache_enabled():
        return False

    name = parse_find_script_invocation(argv)
    if name is None:
        return False

    path = _find_cached_script(name)
    if path is None:
        return False
    print(path)
    return True


def _environment_for_python(python_exe: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)

    parent = python_exe.parent
    venv_root = parent.parent if parent.name in ("bin", "Scripts") else None
    if venv_root is not None and (venv_root / "pyvenv.cfg").is_file():
        env["VIRTUAL_ENV"] = str(venv_root)
    else:
        env.pop("VIRTUAL_ENV", None)

    path = env.get("PATH")
    env["PATH"] = f"{parent}{os.pathsep}{path}" if path else str(parent)
    return env


def try_fast_run_script(argv: list[str]) -> int | None:
    """Run a metadata-validated cached script without importing the Click CLI."""
    if not _cache_enabled():
        return None
    invocation = parse_run_script_invocation(argv)
    if invocation is None:
        return None

    name, args = invocation
    python_doc = _load_valid_python_cache()
    path = _find_cached_script(name)
    if python_doc is None or path is None:
        return None

    python_exe = Path(python_doc["python_exe"])
    command = [str(path), *args] if os.access(path, os.X_OK) else [str(python_exe), str(path), *args]
    try:
        return subprocess.run(command, check=False, env=_environment_for_python(python_exe)).returncode
    except OSError:
        # Fall back to the full command, which revalidates metadata and can provide
        # the normal detailed error message.
        return None
