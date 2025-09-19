from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import rich_click as click
from rich.console import Console

from hcli.lib.commands import async_command
from hcli.lib.config import config_store
from hcli.lib.ida import IdaProduct, get_default_ida_install_directory, get_ida_binary_path

console = Console()


@click.command(name="open", hidden=True)
@click.argument("url", required=True)
@async_command
async def open_url(url: str | None) -> None:
    """HCLI protocol handler for ida://"""

    if not url:
        console.print("[red]No URL provided[/red]")
        raise click.Abort()

    # Parse the URL
    parsed_url = urlparse(url)

    if parsed_url.scheme != "ida":
        console.print(f"[red]Unsupported URL scheme: {parsed_url.scheme}[/red]")
        raise click.Abort()

    # Extract source name (hostname) and file path
    source_name = parsed_url.netloc
    file_path = parsed_url.path.lstrip("/")  # Remove leading slash

    if not source_name:
        console.print("[red]No source name provided in URL[/red]")
        raise click.Abort()

    if not file_path:
        console.print("[red]No file path provided in URL[/red]")
        raise click.Abort()

    # Get ke sources from config
    sources: dict[str, str] = config_store.get_object("ke.sources", {}) or {}

    if source_name not in sources:
        console.print(f"[red]Source '{source_name}' not found. Available sources:[/red]")
        if sources:
            for name in sources.keys():
                console.print(f"  - {name}")
        else:
            console.print("  No sources configured. Use 'hcli ke source add' to add sources.")
        raise click.Abort()

    # Resolve full path
    source_path = Path(sources[source_name])
    full_path = source_path / file_path

    if not full_path.exists():
        console.print(f"[red]File not found: {full_path}[/red]")
        raise click.Abort()

    # Get IDA binary - try ke ida configuration first, then fallback to default
    ida_bin = None

    # First try to use ke ida configuration
    default_instance = config_store.get_string("ke.ida.default", "")
    if default_instance:
        instances: dict[str, str] = config_store.get_object("ke.ida.instances", {}) or {}
        if default_instance in instances:
            ida_dir_path = Path(instances[default_instance])
            try:
                ida_bin = get_ida_binary_path(ida_dir_path)
            except Exception:
                console.print(
                    f"[yellow]Default ke ida instance '{default_instance}' is invalid, trying fallback[/yellow]"
                )

    # Fallback to standard discovery
    if not ida_bin:
        try:
            ida_dir = get_default_ida_install_directory(IdaProduct("IDA Professional", 9, 2))
            ida_bin = get_ida_binary_path(ida_dir)
        except Exception as e:
            console.print(f"[red]IDA Pro not found: {e}[/red]")
            console.print("[yellow]Consider registering IDA instances with 'hcli ke ida add --auto'[/yellow]")
            console.print(f"[yellow]URL resolution successful: {url} -> {full_path}[/yellow]")
            raise click.Abort()

    # Log the URL to a temp file
    log_file = "/tmp/hcli_urls.log"
    timestamp = datetime.now().isoformat()

    with open(str(log_file), "a", encoding="utf-8") as f:
        f.write(f"{timestamp}: {url} -> {full_path} : {ida_bin}\n")

    console.print(f"[green]Opening {full_path} with IDA Pro[/green]")

    # Launch IDA with the resolved file path
    subprocess.Popen([ida_bin, str(full_path)])
