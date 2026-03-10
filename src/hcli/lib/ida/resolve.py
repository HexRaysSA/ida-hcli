"""Shared helpers for resolving IDA instances and navigating to ida:// URIs.

Centralises IDB name matching and the find-or-launch + navigate flow used
by both the default and KE URL handlers.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import rich_click as click
from rich.console import Console

from hcli.lib.ida.ipc import IDAIPCClient

console = Console()


def _print(msg: str) -> None:
    """Print only when running interactively."""
    if sys.stdin.isatty():
        console.print(msg)


def _strip_idb_extension(name: str) -> str:
    """Strip .i64 or .idb extension from filename."""
    lower = name.lower()
    if lower.endswith((".i64", ".idb")):
        return name[:-4]
    return name


def _idb_names_match(ida_idb_name: str, target_name: str) -> bool:
    """Check if IDA's IDB name matches the target name.

    Handles the case where target is 'foo.bin' but IDA reports 'foo.bin.i64'.
    """
    ida_base = _strip_idb_extension(ida_idb_name).lower()
    target_base = _strip_idb_extension(target_name).lower()
    return ida_base == target_base


def resolve_and_navigate(
    uri: str,
    target_idb_name: str,
    idb_path: Path | None,
    no_launch: bool,
    timeout: float,
    skip_analysis: bool,
    navigate: bool = True,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Find a running IDA instance with *target_idb_name* or launch a new one, then navigate.

    Args:
        uri: The original ``ida://`` URI (forwarded to IDA for navigation).
        target_idb_name: IDB filename to match against running instances.
        idb_path: Local path to the IDB/file.  If *None* and no running
            instance matches, the function aborts (caller prints specifics).
        no_launch: When *True*, refuse to launch IDA and abort if no match.
        timeout: Launch timeout in seconds.
        skip_analysis: Skip waiting for auto-analysis after launch.
        navigate: Whether to send ``open_ida_link`` after resolving.
        on_error: Optional callback invoked with an error message string on
            launch failure (e.g. KE shows a native error dialog).
    """
    _print(f"[dim]Looking for IDA instance with '{target_idb_name}'...[/dim]")

    # 1. Discover running instances and look for a match
    instances = IDAIPCClient.discover_instances()
    matching_instance = None
    all_idbs: list[str] = []

    for instance in instances:
        info = IDAIPCClient.query_instance(instance.socket_path)
        if info and info.has_idb:
            if info.idb_name:
                all_idbs.append(info.idb_name)
            if info.idb_name and _idb_names_match(info.idb_name, target_idb_name):
                matching_instance = info
                break

    # 2. Already running — skip launch
    if matching_instance is not None:
        _print(f"[dim]Found running instance (PID {matching_instance.pid})[/dim]")
    else:
        # 3. No match — decide whether to launch
        if no_launch:
            console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")
            if all_idbs:
                console.print("[dim]Currently open IDBs:[/dim]")
                for idb in all_idbs:
                    console.print(f"  - {idb}")
            console.print("[dim]Use without --no-launch to auto-launch IDA[/dim]")
            raise click.Abort()

        if idb_path is None:
            # Caller is responsible for printing a context-specific message
            raise click.Abort()

        # 4. Launch IDA
        from hcli.lib.ida.launcher import MIN_IPC_VERSION, IDALauncher, LaunchConfig, _parse_version_tuple

        console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")

        launcher = IDALauncher(
            LaunchConfig(
                socket_timeout=min(30.0, timeout * 0.25),
                idb_loaded_timeout=min(90.0, timeout * 0.75),
                skip_analysis_wait=skip_analysis,
            )
        )

        ida_version = launcher.get_ida_version()
        use_ipc = ida_version is None or _parse_version_tuple(ida_version) >= MIN_IPC_VERSION

        if not use_ipc:
            result = launcher.launch_only(
                idb_path,
                progress_callback=lambda msg: _print(f"[dim]{msg}[/dim]"),
            )
            if not result.success:
                error_msg = f"Failed to launch IDA: {result.error_message}"
                if on_error:
                    on_error(error_msg)
                console.print(f"[red]{error_msg}[/red]")
                raise click.Abort()
            console.print(
                f"[yellow]IDA {ida_version} does not support IPC, "
                "which is available starting with 9.4. "
                f"Launching IDA with {idb_path.name} "
                "(navigation to specific location not available).[/yellow]"
            )
            return

        # IDA 9.4+: launch and wait for IPC
        console.print(f"[dim]Found IDB: {idb_path}[/dim]")
        result = launcher.launch_and_wait(
            idb_path,
            timeout=timeout,
            progress_callback=lambda msg: _print(f"[dim]{msg}[/dim]"),
        )

        if not result.success or result.instance is None:
            error_msg = f"Failed to launch IDA: {result.error_message or 'Unknown error'}"
            if on_error:
                on_error(error_msg)
            console.print(f"[red]{error_msg}[/red]")
            raise click.Abort()

        matching_instance = result.instance

    # 5. Navigate (if requested)
    if navigate:
        assert matching_instance is not None
        _print(f"[dim]Sending command to IDA (PID {matching_instance.pid})...[/dim]")
        success, message = IDAIPCClient.send_open_ida_link(matching_instance.socket_path, uri)

        if success:
            _print(f"[green]Navigated to: {uri}[/green]")
        else:
            console.print(f"[red]Error: {message}[/red]")
            raise click.Abort()
    else:
        console.print(f"[green]Opening {idb_path or target_idb_name} with IDA[/green]")
