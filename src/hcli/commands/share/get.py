from __future__ import annotations

from pathlib import Path

import rich_click as click

from hcli.lib.api.asset import asset
from hcli.lib.api.common import get_api_client
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@auth_command()
@click.argument("shortcode", metavar="SHORTCODE")
@click.option(
    "-o",
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    help="Output directory (default: current directory)",
)
@click.option(
    "-O",
    "--output-file",
    "output_file",
    type=click.Path(path_type=Path),
    help="Output file path (conflicts with --output-dir)",
)
@click.option("-f", "--force", is_flag=True, help="Overwrite existing files")
@async_command
async def get(shortcode: str, output_dir: Path | None, output_file: Path | None, force: bool) -> None:
    """Download a shared file using its shortcode."""

    # Validate options
    if output_dir and output_file:
        console.print("[red]Error: --output-dir and --output-file cannot be used together[/red]")
        raise click.Abort()

    try:
        # Get file information
        with console.status("[bold blue]Getting file information..."):
            file_info = await asset.get_shared_file_by_code(shortcode)

        if not file_info:
            console.print(f"[red]Error: File with shortcode '{shortcode}' not found[/red]")
            return

        # Determine download parameters
        if output_file:
            # Use specific output file
            download_dir = output_file.parent
            filename = output_file.name
        else:
            # Use output directory (or current directory)
            download_dir = output_dir or Path(".")
            filename = file_info.filename

        # Check if file already exists
        target_path = download_dir / filename
        if target_path.exists() and not force:
            console.print(f"[yellow]Warning: File already exists: {target_path}[/yellow]")
            if not click.confirm("Overwrite existing file?"):
                console.print("[yellow]Download cancelled[/yellow]")
                return

        # Download the file
        client = await get_api_client()
        if not file_info.url:
            console.print("[red]Error: No download URL available for file[/red]")
            return

        downloaded_path = await client.download_file(
            file_info.url,
            target_dir=download_dir,
            target_filename=filename,
            force=force,
            auth=True,
        )

        console.print("[green]✓ File downloaded successfully![/green]")
        console.print(f"[bold]File:[/bold] {file_info.filename}")
        console.print(f"[bold]Size:[/bold] {format_size(file_info.size)}")
        console.print(f"[bold]Saved to:[/bold] {downloaded_path}")

    except Exception as e:
        console.print(f"[red]Error downloading file: {e}[/red]")
        raise click.Abort()


def format_size(bytes_count: int) -> str:
    """Convert bytes to human-readable format."""
    sizes = ["B", "KB", "MB", "GB", "TB"]
    if bytes_count == 0:
        return "0 B"

    import math

    i = int(math.floor(math.log(bytes_count) / math.log(1024)))
    return f"{bytes_count / math.pow(1024, i):.1f} {sizes[i]}"
