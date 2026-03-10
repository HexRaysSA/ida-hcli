"""Open ida:// links in running IDA instances."""

from __future__ import annotations

from urllib.parse import urlparse

import rich_click as click
from rich.console import Console

from hcli.lib.ida.handler import HANDLERS
from hcli.lib.ida.ipc import find_all_instances_with_info

console = Console()


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

    KE URLs (download from KE server and launch IDA):
        ida://host:port/api/v1/buckets/{bucket}/resources/{key}
    """
    if list_instances:
        _list_running_instances()
        return

    if not uri:
        console.print("[red]Error: No URI provided[/red]")
        raise click.Abort()

    parsed = urlparse(uri)

    for handler in HANDLERS:
        if handler.matches(parsed):
            handler.handle(uri, parsed, no_launch, timeout, skip_analysis)
            return
