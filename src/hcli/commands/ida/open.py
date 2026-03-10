"""Open ida:// links in running IDA instances."""

from __future__ import annotations

from urllib.parse import ParseResult, urlparse

import rich_click as click
from rich.console import Console

from hcli.lib.ida.ipc import (
    IDAIPCClient,
    find_all_instances_with_info,
)
from hcli.lib.ida.ke import KEURLHandler
from hcli.lib.ida.launcher import IDALauncher, LaunchConfig
from hcli.lib.ida.resolve import URLHandler, _idb_names_match, _print, resolve_and_navigate

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


class DefaultURLHandler(URLHandler):
    """Handler for standard ida:// URLs (non-KE)."""

    def matches(self, parsed: ParseResult) -> bool:
        return True  # catch-all — must be last in the registry

    def handle(
        self,
        uri: str,
        parsed: ParseResult,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        # URL format: ida://<source>/<idb-name>/<resource>?<params>
        source_name = parsed.hostname or ""
        path_segments = [s for s in parsed.path.split("/") if s]

        if parsed.scheme != "ida" or (len(path_segments) <= 1 and not parsed.query):
            console.print(f"[red]Error: Unsupported ida:// URL: {uri}[/red]")
            console.print(
                "[yellow]Example: ida:///{idb-name}/{resource}?rva=0x0,"
                " e.g. ida:///example.i64/functions?rva=0x0[/yellow]"
            )
            raise click.Abort()

        if len(path_segments) >= 2:
            target_idb_name = path_segments[0]
        else:
            target_idb_name = ""  # relative URL, no IDB name

        if not target_idb_name:
            self._handle_relative_url(uri)
            return

        self._handle_named_idb(uri, target_idb_name, source_name, no_launch, timeout, skip_analysis)

    def _handle_relative_url(self, uri: str) -> None:
        """Relative URL — resolve to the single running instance."""
        instances = IDAIPCClient.discover_instances()
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
        _print(f"[dim]Sending command to IDA (PID {matching_instance.pid})...[/dim]")
        success, message = IDAIPCClient.send_open_ida_link(matching_instance.socket_path, uri)
        if success:
            _print(f"[green]Navigated to: {uri}[/green]")
        else:
            console.print(f"[red]Error: {message}[/red]")
            raise click.Abort()

    def _handle_named_idb(
        self,
        uri: str,
        target_idb_name: str,
        source_name: str,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        """Named IDB — find the file and delegate to resolve_and_navigate."""
        launcher = IDALauncher(
            LaunchConfig(
                socket_timeout=min(30.0, timeout * 0.25),
                idb_loaded_timeout=min(90.0, timeout * 0.75),
                skip_analysis_wait=skip_analysis,
            )
        )
        idb_path = launcher.find_idb_file(target_idb_name, source_name)

        if not idb_path:
            # Check if a running instance already has it open before failing
            instances = IDAIPCClient.discover_instances()
            for instance in instances:
                info = IDAIPCClient.query_instance(instance.socket_path)
                if info and info.has_idb and info.idb_name and _idb_names_match(info.idb_name, target_idb_name):
                    break
            else:
                # Not running either — print source-specific error
                if not no_launch:
                    if source_name and source_name != "localhost":
                        console.print(f"[red]IDB '{target_idb_name}' not found in source '{source_name}'.[/red]")
                    else:
                        console.print(f"[red]IDB '{target_idb_name}' not found in any source.[/red]")
                    console.print("[dim]Configure sources with: hcli ida source add <name> <path>[/dim]")

        resolve_and_navigate(
            uri=uri,
            target_idb_name=target_idb_name,
            idb_path=idb_path,
            no_launch=no_launch,
            timeout=timeout,
            skip_analysis=skip_analysis,
        )


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------

_HANDLERS: list[URLHandler] = [
    KEURLHandler(),
    DefaultURLHandler(),
]


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

    for handler in _HANDLERS:
        if handler.matches(parsed):
            handler.handle(uri, parsed, no_launch, timeout, skip_analysis)
            return
