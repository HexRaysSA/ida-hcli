from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import rich_click as click
from rich.markup import escape

from hcli.env import ENV
from hcli.lib.console import console, print_json
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
    build_console_script_search_dirs,
    detect_current_python_version,
    find_current_python_executable,
    probe_console_script_info,
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
@click.option("--json", "json_output", is_flag=True, default=False, help="output machine-readable JSON")
def explain_environment(json_output: bool) -> None:
    """Show how the current IDA installation and Python version are detected."""

    report: dict[str, Any] = {}

    def emit(text: str, **kwargs) -> None:
        if not json_output:
            console.print(text, **kwargs)

    # --- Known installations ---

    emit("[bold]Known IDA installations[/bold]")
    installations_report: dict[str, Any] = {"installations": [], "error": None}
    report["known_installations"] = installations_report
    try:
        installations = find_standard_installations()
        if installations:
            for path in sorted(installations):
                version = parse_version_from_ida_pro_py(path) or parse_version_from_dir_name(path) or None
                installations_report["installations"].append({"path": str(path), "version": version})
                emit(f"  {_path(path)}  [dim](v{version or '?'})[/dim]")
        elif not json_output:
            console.print("  [dim]none found[/dim]")
    except Exception as e:
        installations_report["error"] = str(e)
        if not json_output:
            _err("scan", str(e))

    emit("")

    # --- Selected installation ---

    emit("[bold]Selected installation[/bold]")
    selected_report: dict[str, Any] = {
        "install_dir": None,
        "install_dir_source": None,
        "install_dir_error": None,
        "resolved_dir": None,
        "resolved_dir_error": None,
    }
    report["selected_installation"] = selected_report

    env_install_dir = os.environ.get("HCLI_CURRENT_IDA_INSTALL_DIR") or ENV.HCLI_CURRENT_IDA_INSTALL_DIR
    if env_install_dir:
        selected_report["install_dir"] = str(env_install_dir)
        selected_report["install_dir_source"] = "$HCLI_CURRENT_IDA_INSTALL_DIR"
        if not json_output:
            _kv("install dir", _path(env_install_dir), "$HCLI_CURRENT_IDA_INSTALL_DIR")
    else:
        config_path = get_ida_config_path()
        try:
            config = get_ida_config()
            if config.paths.installation_directory:
                selected_report["install_dir"] = str(config.paths.installation_directory)
                selected_report["install_dir_source"] = str(config_path)
                if not json_output:
                    _kv("install dir", _path(config.paths.installation_directory), str(config_path))
            else:
                selected_report["install_dir_error"] = f"not configured in {config_path}"
                if not json_output:
                    _err("install dir", f"not configured in {config_path}")
        except Exception as e:
            selected_report["install_dir_error"] = str(e)
            if not json_output:
                _err("install dir", str(e))

    install_dir: Path | None = None
    try:
        install_dir = find_current_ida_install_directory()
        selected_report["resolved_dir"] = str(install_dir)
        if not json_output:
            _kv("resolved dir", _path(install_dir))
    except MissingCurrentInstallationDirectory as e:
        selected_report["resolved_dir_error"] = str(e)
        if not json_output:
            _err("resolved dir", str(e))
            console.print()
        if json_output:
            print_json(report)
        return

    emit("")

    # --- Architecture and version ---

    emit("[bold]Architecture and version[/bold]")
    arch_report: dict[str, Any] = {
        "ida_binary": None,
        "ida_binary_error": None,
        "binary_arch": None,
        "binary_arch_error": None,
        "platform": None,
        "platform_error": None,
        "ida_version": None,
        "ida_version_source": None,
        "ida_version_error": None,
    }
    report["architecture_and_version"] = arch_report

    try:
        ida_binary = find_current_ida_executable()
        arch_report["ida_binary"] = str(ida_binary)
        if not json_output:
            _kv("ida binary", _path(ida_binary))

        arch = detect_binary_arch(ida_binary)
        arch_report["binary_arch"] = arch
        if not json_output:
            _kv("binary arch", arch or "unknown", f"{escape(ida_binary.name)} binary header")
    except Exception as e:
        arch_report["ida_binary_error"] = str(e)
        if not json_output:
            _err("ida binary", str(e))

    try:
        platform_ = find_current_ida_platform()
        arch_report["platform"] = platform_
        if not json_output:
            _kv("platform", platform_)
    except Exception as e:
        arch_report["platform_error"] = str(e)
        if not json_output:
            _err("platform", str(e))

    env_version = os.environ.get("HCLI_CURRENT_IDA_VERSION") or ENV.HCLI_CURRENT_IDA_VERSION
    if env_version:
        arch_report["ida_version"] = env_version
        arch_report["ida_version_source"] = "$HCLI_CURRENT_IDA_VERSION"
        if not json_output:
            _kv("ida version", env_version, "$HCLI_CURRENT_IDA_VERSION")
    else:
        sdk_version = parse_version_from_ida_pro_py(install_dir)
        dir_version = parse_version_from_dir_name(install_dir)
        if sdk_version:
            arch_report["ida_version"] = sdk_version
            arch_report["ida_version_source"] = "python/ida_pro.py SDK docstring"
            if not json_output:
                _kv("ida version", sdk_version, "python/ida_pro.py SDK docstring")
        elif dir_version:
            arch_report["ida_version"] = dir_version
            arch_report["ida_version_source"] = "directory name"
            if not json_output:
                _kv("ida version", dir_version, "directory name")
        else:
            arch_report["ida_version_error"] = "could not determine"
            if not json_output:
                _err("ida version", "could not determine")

    emit("")

    # --- Python detection ---

    emit("[bold]Python environment[/bold]")
    python_env_report: dict[str, Any] = {
        "virtual_env": None,
        "virtual_env_is_uv_cache": False,
        "user_virtual_env": None,
        "candidate_virtual_envs": [],
        "python_exe": None,
        "python_exe_source": None,
        "idat_probe": None,
        "idat_probe_error": None,
        "derived_exe": None,
        "derived_exe_error": None,
    }
    report["python_environment"] = python_env_report

    process_virtual_env = os.environ.get("VIRTUAL_ENV")
    is_uv_cache = process_virtual_env is not None and is_uv_cache_virtual_env(process_virtual_env)
    python_env_report["virtual_env"] = process_virtual_env
    python_env_report["virtual_env_is_uv_cache"] = is_uv_cache
    if not json_output:
        if process_virtual_env and is_uv_cache:
            _kv("$VIRTUAL_ENV", f"{_path(process_virtual_env)}  [dim](uv cache)[/dim]")
        elif process_virtual_env:
            _kv("$VIRTUAL_ENV", _path(process_virtual_env))
        else:
            _kv("$VIRTUAL_ENV", "not set")

    user_venv = resolve_user_virtual_env()
    python_env_report["user_virtual_env"] = str(user_venv) if user_venv else None
    if user_venv and not json_output:
        _kv("user virtualenv", _path(user_venv), "resolved from $PATH")

    path_venvs = find_candidate_virtual_envs()
    non_uv_candidates = [c for c in path_venvs if not is_uv_cache_virtual_env(c.path)]
    python_env_report["candidate_virtual_envs"] = [{"path": str(c.path), "source": c.source} for c in non_uv_candidates]
    if non_uv_candidates and not json_output:
        for candidate in non_uv_candidates:
            _kv("  candidate venv", f"{_path(candidate.path)}  [dim](via {candidate.source})[/dim]")

    info: dict | None = None
    env_python = os.environ.get("HCLI_CURRENT_IDA_PYTHON_EXE") or ENV.HCLI_CURRENT_IDA_PYTHON_EXE
    if env_python:
        python_env_report["python_exe"] = str(env_python)
        python_env_report["python_exe_source"] = "$HCLI_CURRENT_IDA_PYTHON_EXE"
        if not json_output:
            _kv("python exe", _path(env_python), "$HCLI_CURRENT_IDA_PYTHON_EXE")
    else:
        if not json_output:
            _kv("HCLI_CURRENT_IDA_PYTHON_EXE", "not set")

        try:
            info = run_py_in_current_idapython(GET_PYTHON_INFO_PY)
            python_env_report["idat_probe"] = info
            if not json_output:
                console.print("  [bold]idat probe[/bold]: [green]success[/green]")
                _kv("  sys.prefix", _path(info["prefix"]))
                _kv("  sys.base_prefix", _path(info["base_prefix"]))
                _kv("  sys.executable", _path(info.get("executable")))
                _kv("  $VIRTUAL_ENV", _path(info.get("virtual_env")))
                _kv("  $IDAPYTHON_VENV_EXECUTABLE", _path(info.get("idapython_venv_executable")))
                _kv("  sys.version_info", f"{info['version_major']}.{info['version_minor']}")

            try:
                derived = _derive_python_exe(info)
                python_env_report["derived_exe"] = str(derived)
                if not json_output:
                    _kv("derived exe", _path(derived))
            except PythonNotFoundError as e:
                python_env_report["derived_exe_error"] = str(e)
                if not json_output:
                    _err("derived exe", str(e))

        except Exception as e:
            python_env_report["idat_probe_error"] = f"{type(e).__name__}: {e}"
            if not json_output:
                _err("idat probe", f"{type(e).__name__}: {e}")

    emit("")

    # --- IDAPython virtualenv ---

    emit("[bold]IDAPython virtualenv[/bold]")
    ida_venv_report: dict[str, Any] | None = None
    report["idapython_virtualenv"] = ida_venv_report

    ida_venv = info.get("virtual_env") if info else None

    if ida_venv:
        venv_path = Path(ida_venv)
        ida_venv_report = {"venv": str(venv_path), "home": None, "system_site_packages": None}
        report["idapython_virtualenv"] = ida_venv_report
        if not json_output:
            _kv("venv", _path(venv_path), "activated by idapythonrc.py")
        pyvenv_cfg = venv_path / "pyvenv.cfg"
        if pyvenv_cfg.is_file():
            for line in pyvenv_cfg.read_text().splitlines():
                if line.startswith("home"):
                    ida_venv_report["home"] = line.split("=", 1)[1].strip()
                    if not json_output:
                        _kv("  home", ida_venv_report["home"])
                elif line.startswith("include-system-site-packages"):
                    ida_venv_report["system_site_packages"] = line.split("=", 1)[1].strip()
                    if not json_output:
                        _kv("  system site-packages", ida_venv_report["system_site_packages"])
    elif not json_output:
        console.print("  [dim]none detected[/dim]")

    emit("")

    # --- Final Python version ---

    emit("[bold]Python version[/bold]")
    python_version_report: dict[str, Any] = {
        "final_python_exe": None,
        "final_python_exe_error": None,
        "probed_version": None,
        "probed_version_error": None,
        "hcli_interpreter_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "hcli_interpreter_path": sys.executable,
        "final_version": None,
        "final_version_error": None,
    }
    report["python_version"] = python_version_report

    python_exe: Path | None = None
    try:
        python_exe = find_current_python_executable()
        python_version_report["final_python_exe"] = str(python_exe)
        if not json_output:
            _kv("final python exe", _path(python_exe))

        result = subprocess.run(
            [str(python_exe), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        python_version_report["probed_version"] = result.stdout.strip()
        if not json_output:
            _kv("probed version", result.stdout.strip(), f"running {escape(python_exe.name)}")
    except Exception as e:
        python_version_report["final_python_exe_error" if python_exe is None else "probed_version_error"] = (
            f"{type(e).__name__}: {e}"
        )
        if not json_output:
            _err("probed version", f"{type(e).__name__}: {e}")

    if not json_output:
        interpreter_version = python_version_report["hcli_interpreter_version"]
        _kv("hcli interpreter", interpreter_version, _path(sys.executable))

    try:
        final = detect_current_python_version()
        python_version_report["final_version"] = final
        if not json_output:
            style = "green" if final != python_version_report["hcli_interpreter_version"] else "yellow"
            _kv("final version", f"[{style}]{final}[/{style}]")
    except Exception as e:
        python_version_report["final_version_error"] = f"{type(e).__name__}: {e}"
        if not json_output:
            _err("final version", f"{type(e).__name__}: {e}")

    emit("")

    # --- Console scripts (e.g. locating a pip-installed script like `speakeasy`) ---

    emit("[bold]Console scripts[/bold]")
    console_scripts_report: dict[str, Any] = {
        "python_exe": None,
        "prefix": None,
        "base_prefix": None,
        "scripts_dir": None,
        "os_name": None,
        "search_dirs": [],
        "error": None,
    }
    report["console_scripts"] = console_scripts_report

    if python_exe is None:
        console_scripts_report["error"] = "no final python exe was detected above"
        if not json_output:
            _err("search dirs", "no final python exe was detected above")
    else:
        try:
            csi = probe_console_script_info(python_exe)
            search_dirs = build_console_script_search_dirs(
                csi["prefix"], csi["base_prefix"], csi["scripts_dir"], csi["os_name"]
            )
            console_scripts_report.update(
                {
                    "python_exe": str(python_exe),
                    "prefix": csi["prefix"],
                    "base_prefix": csi["base_prefix"],
                    "scripts_dir": csi["scripts_dir"],
                    "os_name": csi["os_name"],
                    "search_dirs": search_dirs,
                }
            )
            if not json_output:
                _kv("prefix", _path(csi["prefix"]))
                _kv("base_prefix", _path(csi["base_prefix"]))
                _kv("scripts dir", _path(csi["scripts_dir"]))
                for directory in search_dirs:
                    _kv("  search dir", _path(directory))
        except Exception as e:
            console_scripts_report["error"] = f"{type(e).__name__}: {e}"
            if not json_output:
                _err("search dirs", f"{type(e).__name__}: {e}")

    emit("")
    is_hcli_own_venv = process_virtual_env and os.path.normcase(
        os.path.abspath(process_virtual_env)
    ) == os.path.normcase(os.path.abspath(sys.prefix))

    notes: list[str] = []
    report["notes"] = notes

    if is_uv_cache and user_venv:
        note = f"$VIRTUAL_ENV is a uv cache overlay. Resolved user virtualenv: {user_venv}"
        notes.append(note)
        if not json_output:
            console.print(
                f"[dim]Note: $VIRTUAL_ENV is a uv cache overlay. Resolved user virtualenv: {escape(str(user_venv))}[/dim]",
                highlight=False,
            )
            console.print()
    elif is_uv_cache:
        note = "$VIRTUAL_ENV is a uv cache overlay, not your virtualenv. No user virtualenvs were found on $PATH."
        notes.append(note)
        if not json_output:
            console.print(
                "[dim]Note: $VIRTUAL_ENV is a uv cache overlay, not your virtualenv. "
                "No user virtualenvs were found on $PATH.[/dim]",
                highlight=False,
            )
            console.print()
    elif process_virtual_env and is_hcli_own_venv:
        note = (
            f"$VIRTUAL_ENV ({process_virtual_env}) is the hcli process environment, not the IDA Python "
            f"environment. It is not used for plugin installation."
        )
        notes.append(note)
        if not json_output:
            console.print(
                f"[dim]Note: $VIRTUAL_ENV ({escape(process_virtual_env)}) "
                f"is the hcli process environment, not the IDA Python environment. "
                f"It is not used for plugin installation.[/dim]",
                highlight=False,
            )
            console.print()
    elif process_virtual_env and not ida_venv:
        note = (
            f"$VIRTUAL_ENV is set ({process_virtual_env}) but was not detected inside IDA. "
            f"To use this virtualenv with IDA, activate it via idapythonrc.py."
        )
        notes.append(note)
        if not json_output:
            console.print(
                f"[dim]Note: $VIRTUAL_ENV is set ({escape(process_virtual_env)}) "
                f"but was not detected inside IDA. "
                f"To use this virtualenv with IDA, activate it via idapythonrc.py.[/dim]",
                highlight=False,
            )
            console.print()

    if not ida_venv:
        notes.append(
            "To use a virtualenv with IDA, see: https://community.hex-rays.com/t/using-a-virtualenv-for-idapython/261/5"
        )
        if not json_output:
            console.print(
                "[dim]To use a virtualenv with IDA, see: "
                "https://community.hex-rays.com/t/using-a-virtualenv-for-idapython/261/5[/dim]",
                highlight=False,
            )
    if not user_venv and not is_uv_cache and not ida_venv:
        notes.append("To change IDA's Python, use idapyswitch to point at a different interpreter.")
        if not json_output:
            console.print("[dim]To change IDA's Python, use idapyswitch to point at a different interpreter.[/dim]")

    try:
        final_version = detect_current_python_version()
        parts = final_version.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if (major, minor) <= (3, 9):
            notes.append(
                f"Python {final_version} has reached end-of-life. "
                "Many IDA plugins may not support it. "
                "Consider upgrading to a newer Python and using idapyswitch to point IDA at it."
            )
            if not json_output:
                console.print()
                console.print(
                    f"[bold yellow]Warning:[/bold yellow] Python {final_version} has reached end-of-life. "
                    "Many IDA plugins may not support it. "
                    "Consider upgrading to a newer Python and using idapyswitch to point IDA at it.",
                )
    except Exception:
        pass

    if json_output:
        print_json(report)
