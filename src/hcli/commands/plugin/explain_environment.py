from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import rich_click as click
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import console
from hcli.lib.ida import (
    MissingCurrentInstallationDirectory,
    detect_binary_arch,
    find_current_ida_executable,
    find_current_ida_install_directory,
    find_current_ida_platform,
    find_standard_installations,
    get_ida_config,
    get_ida_config_path,
    parse_version_from_dir_name,
    parse_version_from_ida_pro_py,
    run_py_in_current_idapython,
)
from hcli.lib.ida.python import (
    GET_PYTHON_INFO_PY,
    PythonNotFoundError,
    _derive_python_exe,
    detect_current_python_version,
    find_current_python_executable,
)
from hcli.lib.venv import find_candidate_virtual_envs, is_uv_cache_virtual_env, resolve_user_virtual_env


def _path(p: object) -> str:
    return f"[repr.path]{escape(str(p))}[/repr.path]"


def _kv(key: str, value: str, via: str | None = None) -> None:
    if via:
        console.print(f"  [bold]{key}[/bold]: {value}  [dim](via {via})[/dim]")
    else:
        console.print(f"  [bold]{key}[/bold]: {value}")


def _err(key: str, error: str) -> None:
    console.print(f"  [bold]{key}[/bold]: [red]{escape(error)}[/red]")


@click.command(hidden=True)
def explain_environment() -> None:
    """Show how the current IDA installation and Python version are detected."""

    # --- Known installations ---

    console.print("[bold]Known IDA installations[/bold]")
    try:
        installations = find_standard_installations()
        if installations:
            for path in sorted(installations):
                version = parse_version_from_ida_pro_py(path) or parse_version_from_dir_name(path) or "?"
                console.print(f"  {_path(path)}  [dim](v{version})[/dim]")
        else:
            console.print("  [dim]none found[/dim]")
    except Exception as e:
        _err("scan", str(e))

    console.print()

    # --- Selected installation ---

    console.print("[bold]Selected installation[/bold]")

    env_install_dir = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR") or ENV.HCLI_CURRENT_IDA_INSTALL_DIR
    if env_install_dir:
        _kv("install dir", _path(env_install_dir), "$HCLI_CURRENT_IDA_INSTALL_DIR")
    else:
        config_path = get_ida_config_path()
        try:
            config = get_ida_config()
            if config.paths.installation_directory:
                _kv("install dir", _path(config.paths.installation_directory), str(config_path))
            else:
                _err("install dir", f"not configured in {config_path}")
        except Exception as e:
            _err("install dir", str(e))

    try:
        install_dir = find_current_ida_install_directory()
        _kv("resolved dir", _path(install_dir))
    except MissingCurrentInstallationDirectory as e:
        _err("resolved dir", str(e))
        console.print()
        return

    console.print()

    # --- Architecture and version ---

    console.print("[bold]Architecture and version[/bold]")

    try:
        ida_binary = find_current_ida_executable()
        _kv("ida binary", _path(ida_binary))

        arch = detect_binary_arch(ida_binary)
        _kv("binary arch", arch or "unknown", f"{escape(ida_binary.name)} binary header")
    except Exception as e:
        _err("ida binary", str(e))

    try:
        platform = find_current_ida_platform()
        _kv("platform", platform)
    except Exception as e:
        _err("platform", str(e))

    env_version = os.environ.get("HCLI_CURRENT_IDA_VERSION") or ENV.HCLI_CURRENT_IDA_VERSION
    if env_version:
        _kv("ida version", env_version, "$HCLI_CURRENT_IDA_VERSION")
    else:
        sdk_version = parse_version_from_ida_pro_py(install_dir)
        dir_version = parse_version_from_dir_name(install_dir)
        if sdk_version:
            _kv("ida version", sdk_version, "python/ida_pro.py SDK docstring")
        elif dir_version:
            _kv("ida version", dir_version, "directory name")
        else:
            _err("ida version", "could not determine")

    console.print()

    # --- Python detection ---

    console.print("[bold]Python environment[/bold]")

    process_virtual_env = os.environ.get("VIRTUAL_ENV")
    is_uv_cache = process_virtual_env is not None and is_uv_cache_virtual_env(process_virtual_env)
    if process_virtual_env and is_uv_cache:
        _kv("$VIRTUAL_ENV", f"{_path(process_virtual_env)}  [dim](uv cache)[/dim]")
    elif process_virtual_env:
        _kv("$VIRTUAL_ENV", _path(process_virtual_env))
    else:
        _kv("$VIRTUAL_ENV", "not set")

    user_venv = resolve_user_virtual_env()
    if user_venv:
        _kv("user virtualenv", _path(user_venv), "resolved from $PATH")

    path_venvs = find_candidate_virtual_envs()
    non_uv_candidates = [c for c in path_venvs if not is_uv_cache_virtual_env(c.path)]
    if non_uv_candidates:
        for candidate in non_uv_candidates:
            _kv("  candidate venv", f"{_path(candidate.path)}  [dim](via {candidate.source})[/dim]")

    info: dict | None = None
    env_python = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE") or ENV.HCLI_CURRENT_IDA_PYTHON_EXE
    if env_python:
        _kv("python exe", _path(env_python), "$HCLI_CURRENT_IDA_PYTHON_EXE")
    else:
        _kv("HCLI_CURRENT_IDA_PYTHON_EXE", "not set")

        try:
            info = run_py_in_current_idapython(GET_PYTHON_INFO_PY)
            console.print("  [bold]idat probe[/bold]: [green]success[/green]")
            _kv("  sys.prefix", _path(info["prefix"]))
            _kv("  sys.base_prefix", _path(info["base_prefix"]))
            _kv("  sys.executable", _path(info.get("executable")))
            _kv("  $VIRTUAL_ENV", _path(info.get("virtual_env")))
            _kv("  $IDAPYTHON_VENV_EXECUTABLE", _path(info.get("idapython_venv_executable")))
            _kv("  sys.version_info", f"{info['version_major']}.{info['version_minor']}")

            try:
                derived = _derive_python_exe(info)
                _kv("derived exe", _path(derived))
            except PythonNotFoundError as e:
                _err("derived exe", str(e))

        except Exception as e:
            _err("idat probe", f"{type(e).__name__}: {e}")

    console.print()

    # --- IDAPython virtualenv ---

    console.print("[bold]IDAPython virtualenv[/bold]")

    ida_venv = info.get("virtual_env") if info else None

    if ida_venv:
        venv_path = Path(ida_venv)
        _kv("venv", _path(venv_path), "activated by idapythonrc.py")
        pyvenv_cfg = venv_path / "pyvenv.cfg"
        if pyvenv_cfg.is_file():
            for line in pyvenv_cfg.read_text().splitlines():
                if line.startswith("home"):
                    _kv("  home", line.split("=", 1)[1].strip())
                elif line.startswith("include-system-site-packages"):
                    _kv("  system site-packages", line.split("=", 1)[1].strip())
    else:
        console.print("  [dim]none detected[/dim]")

    console.print()

    # --- Final Python version ---

    console.print("[bold]Python version[/bold]")

    try:
        python_exe = find_current_python_executable()
        _kv("final python exe", _path(python_exe))

        result = subprocess.run(
            [str(python_exe), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        _kv("probed version", result.stdout.strip(), f"running {escape(python_exe.name)}")
    except Exception as e:
        _err("probed version", f"{type(e).__name__}: {e}")

    interpreter_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    _kv("hcli interpreter", interpreter_version, _path(sys.executable))

    try:
        final = detect_current_python_version()
        style = "green" if final != interpreter_version else "yellow"
        _kv("final version", f"[{style}]{final}[/{style}]")
    except Exception as e:
        _err("final version", f"{type(e).__name__}: {e}")

    console.print()
    is_hcli_own_venv = process_virtual_env and os.path.normcase(
        os.path.abspath(process_virtual_env)
    ) == os.path.normcase(os.path.abspath(sys.prefix))

    if is_uv_cache and user_venv:
        console.print(
            f"[dim]Note: $VIRTUAL_ENV is a uv cache overlay. Resolved user virtualenv: {escape(str(user_venv))}[/dim]",
            highlight=False,
        )
        console.print()
    elif is_uv_cache:
        console.print(
            "[dim]Note: $VIRTUAL_ENV is a uv cache overlay, not your virtualenv. "
            "No user virtualenvs were found on $PATH.[/dim]",
            highlight=False,
        )
        console.print()
    elif process_virtual_env and is_hcli_own_venv:
        console.print(
            f"[dim]Note: $VIRTUAL_ENV ({escape(process_virtual_env)}) "
            f"is the hcli process environment, not the IDA Python environment. "
            f"It is not used for plugin installation.[/dim]",
            highlight=False,
        )
        console.print()
    elif process_virtual_env and not ida_venv:
        console.print(
            f"[dim]Note: $VIRTUAL_ENV is set ({escape(process_virtual_env)}) "
            f"but was not detected inside IDA. "
            f"To use this virtualenv with IDA, activate it via idapythonrc.py.[/dim]",
            highlight=False,
        )
        console.print()

    if not ida_venv:
        console.print(
            "[dim]To use a virtualenv with IDA, see: "
            "https://community.hex-rays.com/t/using-a-virtualenv-for-idapython/261/5[/dim]",
            highlight=False,
        )
    if not user_venv and not is_uv_cache and not ida_venv:
        console.print("[dim]To change IDA's Python, use idapyswitch to point at a different interpreter.[/dim]")
