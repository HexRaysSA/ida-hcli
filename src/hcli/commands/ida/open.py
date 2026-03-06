"""Open ida:// links in running IDA instances."""

from __future__ import annotations

import sys
from urllib.parse import urlparse

import rich_click as click
from rich.console import Console

from hcli.lib.ida.ipc import (
    IDAIPCClient,
    find_all_instances_with_info,
)
from hcli.lib.ida.launcher import MIN_IPC_VERSION, IDALauncher, LaunchConfig, _parse_version_tuple


def _strip_idb_extension(name: str) -> str:
    """Strip .i64 or .idb extension from filename."""
    lower = name.lower()
    if lower.endswith((".i64", ".idb")):
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
    help="List running IDA instances with IPC support (IDA 9.4+)",
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
def open_ida_link(uri: str | None, list_instances: bool, no_launch: bool, timeout: float, skip_analysis: bool) -> None:
    """Open an ida:// link in the appropriate IDA instance.

    For IDA 9.4+, this command finds a running IDA instance with the specified
    IDB and navigates to the location in the URI. If no matching instance is
    found, IDA is launched and the link is opened after startup.

    For older IDA versions, this command launches IDA with the IDB file.
    Navigation to specific locations requires IDA 9.4+ with IPC support.

    Use --list to show running IDA instances (requires IDA 9.4+ IPC).

    Example URIs:
        ida://malwares/trojan1.i64/functions?rva=0x1000  (named source)
        ida:///myfile.i64/functions?rva=0x1000  (relative: searches all sources)
        ida:///myfile.i64/addresses?rva=0x0  (open IDB and position at beginning)
        ida:///functions?rva=0x1000  (relative: sent to the only running instance)
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

    # URL format: ida://<source>/<idb-name>/<resource>?<params>
    # e.g., ida://malwares/trojan1.i64/functions?rva=0x1000
    # Relative: ida:///myfile.i64/functions?rva=0x1000 (no source)
    # Relative: ida:///functions?rva=0x1000 (no source, no idb name)
    source_name = parsed.hostname or ""  # "malwares", "localhost", or ""
    path_segments = [s for s in parsed.path.split("/") if s]

    if len(path_segments) >= 2:
        target_idb_name = path_segments[0]  # e.g., "myfile.i64"
    else:
        target_idb_name = ""  # relative URL, no IDB name

    # Discover running IDA instances
    instances = IDAIPCClient.discover_instances()

    if not target_idb_name:
        # Relative URL — resolve to the single running instance
        instances_with_idb = []
        for instance in instances:
            info = IDAIPCClient.query_instance(instance.socket_path)
            if info and info.has_idb:
                instances_with_idb.append(info)

        if len(instances_with_idb) == 0:
            console.print("[red]Error: No running IDA instances with an IDB loaded.[/red]")
            raise click.Abort()
        if len(instances_with_idb) > 1:
            console.print("[red]Error: Multiple IDA instances running, specify IDB name in URL[/red]")
            console.print("[yellow]Example: ida:///<idb-name>/...[/yellow]")
            console.print("[dim]Currently open IDBs:[/dim]")
            for inst in instances_with_idb:
                console.print(f"  - {inst.idb_name}")
            raise click.Abort()

        matching_instance = instances_with_idb[0]
    else:
        # Named IDB — find a matching instance
        _print(f"[dim]Looking for IDA instance with '{target_idb_name}'...[/dim]")

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
            launcher = IDALauncher(
                LaunchConfig(
                    socket_timeout=min(30.0, timeout * 0.25),
                    idb_loaded_timeout=min(90.0, timeout * 0.75),
                    skip_analysis_wait=skip_analysis,
                )
            )

            if no_launch:
                console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")
                if all_idbs:
                    console.print("[dim]Currently open IDBs:[/dim]")
                    for idb in all_idbs:
                        console.print(f"  - {idb}")
                ida_version = launcher.get_ida_version()
                if ida_version and _parse_version_tuple(ida_version) < MIN_IPC_VERSION:
                    console.print(
                        f"[dim]Your configured IDA ({ida_version}) does not support IPC. "
                        "Use without --no-launch to launch IDA with the IDB file.[/dim]"
                    )
                else:
                    console.print("[dim]Use without --no-launch to auto-launch IDA[/dim]")
                raise click.Abort()

            # Auto-launch IDA
            console.print(f"[yellow]No IDA instance has '{target_idb_name}' open.[/yellow]")

            # Find IDB file in sources
            idb_path = launcher.find_idb_file(target_idb_name, source_name)
            if not idb_path:
                if source_name and source_name != "localhost":
                    console.print(f"[red]IDB '{target_idb_name}' not found in source '{source_name}'.[/red]")
                else:
                    console.print(f"[red]IDB '{target_idb_name}' not found in any source.[/red]")
                console.print("[dim]Configure sources with: hcli ida source add <name> <path>[/dim]")
                raise click.Abort()

            console.print(f"[dim]Found IDB: {idb_path}[/dim]")

            # Check IDA version to decide between IPC and launch-only
            ida_version = launcher.get_ida_version()
            use_ipc = ida_version is None or _parse_version_tuple(ida_version) >= MIN_IPC_VERSION

            if not use_ipc:
                # Pre-IPC IDA: launch with IDB but no navigation
                result = launcher.launch_only(
                    idb_path,
                    progress_callback=lambda msg: console.print(f"[dim]{msg}[/dim]"),
                )
                if not result.success:
                    console.print(f"[red]Failed to launch IDA: {result.error_message}[/red]")
                    raise click.Abort()
                console.print(
                    f"[yellow]IDA {ida_version} does not support IPC. "
                    f"Launching IDA with {idb_path.name} "
                    "(navigation to specific location not available).[/yellow]"
                )
                return

            # IDA 9.4+: launch and wait for IPC
            result = launcher.launch_and_wait(
                idb_path,
                timeout=timeout,
                progress_callback=lambda msg: console.print(f"[dim]{msg}[/dim]"),
            )

            if not result.success or result.instance is None:
                console.print(f"[red]Failed to launch IDA: {result.error_message}[/red]")
                raise click.Abort()

            matching_instance = result.instance

    # Send open_ida_link command
    assert matching_instance is not None  # Should never be None at this point
    _print(f"[dim]Sending command to IDA (PID {matching_instance.pid})...[/dim]")
    success, message = IDAIPCClient.send_open_ida_link(matching_instance.socket_path, uri)

    if success:
        _print(f"[green]Navigated to: {uri}[/green]")
    else:
        console.print(f"[red]Error: {message}[/red]")
        raise click.Abort()
