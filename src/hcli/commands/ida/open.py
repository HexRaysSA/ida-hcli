"""Open ida:// links in running IDA instances."""

from __future__ import annotations

from urllib.parse import urlparse

import sys

import rich_click as click
from rich.console import Console

from hcli.lib.ida.ipc import (
    IDAIPCClient,
    find_all_instances_with_info,
    find_instance_for_idb,
)
from hcli.lib.ida.launcher import IDALauncher, LaunchConfig


def _strip_idb_extension(name: str) -> str:
    """Strip .i64 or .idb extension from filename."""
    lower = name.lower()
    if lower.endswith('.i64') or lower.endswith('.idb'):
        return name[:-4]
    return name


def _idb_names_match(ida_idb_name: str, target_name: str) -> bool:
    """Check if IDA's IDB name matches the target name."""
    ida_base = _strip_idb_extension(ida_idb_name).lower()
    target_base = _strip_idb_extension(target_name).lower()
    return ida_base == target_base

console = Console()

def _print(msg: str) -> None:
    """Print message only when running interactively (stdin is a TTY).

    When invoked via protocol handler (xdg-open), stdin is not a TTY and
    xdg-open returns immediately, spawning hcli asynchronously. Any output
    would appear after the shell prompt, cluttering the terminal.
    """
    if sys.stdin.isatty():
        console.print(msg)


def _list_running_instances() -> None:
    """List all running IDA instances with IPC sockets."""
    instances = find_all_instances_with_info()

    if not instances:
        console.print("[yellow]No running IDA instances found.[/yellow]")
        return

    console.print(f"[green]Found {len(instances)} IDA instance(s):[/green]")
    for instance in instances:
        if instance.has_idb:
            console.print(f"  PID {instance.pid}: {instance.idb_name} ({instance.idb_path})")
        else:
            console.print(f"  PID {instance.pid}: [dim]no IDB loaded[/dim]")


@click.command(name="open")
@click.argument("uri", required=False)
@click.option(
    "--list",
    "list_instances",
    is_flag=True,
    help="List all running IDA instances with IPC sockets",
)
@click.option(
    "--no-launch",
    is_flag=True,
    help="Don't auto-launch IDA if no matching instance is found",
)
@click.option(
    "--timeout",
    type=float,
    default=120.0,
    help="Timeout in seconds for IDA startup (default: 120)",
)
@click.option(
    "--skip-analysis",
    is_flag=True,
    help="Don't wait for auto-analysis to complete after launching IDA",
)
def open_link(uri: str | None, list_instances: bool, no_launch: bool, timeout: float, skip_analysis: bool) -> None:
    """Open an ida:// link in the appropriate IDA instance.

    This command finds a running IDA instance that has the specified IDB open
    and sends it an open_link command to navigate to the specified location.

    If no matching instance is found and --no-launch is not set, IDA will be
    launched with the IDB file (searched in configured idb.search-paths).

    Use --list to show all running IDA instances.

    Example URIs:
        ida://myfile.idb/functions?ea=0x401000
        ida://sample.i64/addresses?ea=0x401000&view=disasm
        ida://target.idb/types?name=MyStruct
    """
    if list_instances:
        _list_running_instances()
        return

    if not uri:
        console.print("[red]Error: No URI provided[/red]")
        raise click.Abort()

    # Parse URL to extract IDB name
    parsed = urlparse(uri)

    if parsed.scheme != "ida":
        console.print(f"[red]Error: Expected ida:// URL, got {parsed.scheme}://[/red]")
        raise click.Abort()

    target_idb_name = parsed.netloc  # e.g., "myfile.idb" from ida://myfile.idb/...

    if not target_idb_name:
        console.print("[red]Error: No IDB name in URL[/red]")
        console.print("[yellow]Expected format: ida://<idb-name>/<resource>?<params>[/yellow]")
        raise click.Abort()

    # Discover running IDA instances
    _print(f"[dim]Looking for IDA instance with '{target_idb_name}'...[/dim]")
    instances = IDAIPCClient.discover_instances()

    # Query each instance to find one with the matching IDB
    matching_instance = None
    all_idbs = []

    for instance in instances:
        info = IDAIPCClient.query_instance(instance.socket_path)
        if info and info.has_idb:
            all_idbs.append(info.idb_name)
            if info.idb_name and _idb_names_match(info.idb_name, target_idb_name):
                matching_instance = info
                break

    if not matching_instance:
        if no_launch:
            console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")
            if all_idbs:
                console.print("[dim]Currently open IDBs:[/dim]")
                for idb in all_idbs:
                    console.print(f"  - {idb}")
            console.print("[dim]Use without --no-launch to auto-launch IDA[/dim]")
            raise click.Abort()

        # Auto-launch IDA
        console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")

        launcher = IDALauncher(
            LaunchConfig(
                socket_timeout=min(30.0, timeout * 0.25),
                idb_loaded_timeout=min(90.0, timeout * 0.75),
                skip_analysis_wait=skip_analysis,
            )
        )

        # Find IDB file in search paths
        idb_path = launcher.find_idb_file(target_idb_name)
        if not idb_path:
            console.print(f"[red]IDB '{target_idb_name}' not found in search paths.[/red]")
            console.print("[dim]Configure paths with: hcli idb search-path add <path>[/dim]")
            raise click.Abort()

        console.print(f"[dim]Found IDB: {idb_path}[/dim]")

        # Launch IDA and wait for it to be ready
        result = launcher.launch_and_wait(
            idb_path,
            timeout=timeout,
            progress_callback=lambda msg: console.print(f"[dim]{msg}[/dim]"),
        )

        if not result.success or result.instance is None:
            console.print(f"[red]Failed to launch IDA: {result.error_message}[/red]")
            raise click.Abort()

        matching_instance = result.instance

    # Send open_link command
    assert matching_instance is not None  # Should never be None at this point
    _print(f"[dim]Sending command to IDA (PID {matching_instance.pid})...[/dim]")
    success, message = IDAIPCClient.send_open_link(matching_instance.socket_path, uri)

    if success:
        _print(f"[green]Navigated to: {uri}[/green]")
    else:
        console.print(f"[red]Error: {message}[/red]")
        raise click.Abort()
