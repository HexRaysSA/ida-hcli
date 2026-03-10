"""ida:// URL handler base class and concrete implementations.

Each handler implements ``matches`` (predicate) and ``handle`` (action).
The dispatcher in ``open.py`` iterates over registered handlers and calls
the first one whose ``matches`` returns *True*.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import ParseResult

import rich_click as click
from rich.console import Console

from hcli.lib.ida.ipc import IDAIPCClient
from hcli.lib.ida.launcher import IDALauncher, LaunchConfig
from hcli.lib.ida.resolve import _idb_names_match, _print, resolve_and_navigate

console = Console()


class URLHandler(ABC):
    """Base class for ida:// URL handlers."""

    @abstractmethod
    def matches(self, parsed: ParseResult) -> bool:
        """Return *True* if this handler should process the URL."""

    @abstractmethod
    def handle(self, uri: str, parsed: ParseResult, no_launch: bool, timeout: float, skip_analysis: bool) -> None:
        """Process the ida:// URL."""


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


class KEURLHandler(URLHandler):
    """Handler for KE URLs: ``ida://host/api/v1/buckets/{bucket}/resources/{key}``."""

    def matches(self, parsed: ParseResult) -> bool:
        return "/api/v1/buckets/" in parsed.path

    def handle(
        self,
        uri: str,
        parsed: ParseResult,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        """Download a resource from KE and launch IDA with it."""
        from hcli.lib.ida.ke import _ke_download_and_launch

        _ke_download_and_launch(uri, parsed, no_launch, timeout, skip_analysis)


# ---------------------------------------------------------------------------
# Handler registry — order matters: first match wins
# ---------------------------------------------------------------------------

HANDLERS: list[URLHandler] = [
    KEURLHandler(),
    DefaultURLHandler(),
]
