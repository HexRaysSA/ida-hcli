from __future__ import annotations

from pathlib import Path

import rich_click as click

from hcli.lib.api.asset import asset
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console


@auth_command()
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("-b", "--bucket", required=True, type=str, help="Bucket to upload to")
@click.option(
    "-m",
    "--metadata",
    required=True,
    type=str,
    multiple=True,
    help="Attach metadata as KEY=VALUE (repeatable). Example: -m version=9.2 -m category=ida-free",
)
@click.option(
    "--allowed-segments",
    required=False,
    default=None,
    help="Comma-separated list of allowed segments (e.g. segment1,segment2,segment3)",
)
@click.option(
    "--allowed-emails",
    required=False,
    default=None,
    help="Comma-separated list of allowed email addresses",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Upload a new version or overwrite the asset if it exists",
)
@async_command
async def put(
    path: Path,
    bucket: str,
    metadata: tuple[str, ...],
    allowed_segments: str | None,
    allowed_emails: str | None,
    force: bool = False,
) -> None:
    """Upload an asset to a bucket."""

    if not path.exists():
        console.print(f"[red]Error: File not found: {path}[/red]")
        raise click.Abort()

    if not path.is_file():
        console.print(f"[red]Error: Path is not a file: {path}[/red]")
        raise click.Abort()

    bucket_obj = await asset.get_bucket(bucket)
    if not bucket_obj:
        console.print(f"[red]Error: Bucket {bucket} does not exist[/red]")
        raise click.Abort()

    metadata_dict = {}
    for item in metadata:
        if "=" not in item:
            console.print(f"[red]Error: Metadata '{item}' is not in KEY=VALUE format[/red]")
            raise click.Abort()
        key, val = item.split("=", 1)
        metadata_dict[key] = val

    missing_fields = []
    for required_key in bucket_obj.requiredMetadata:
        if required_key not in metadata_dict:
            missing_fields.append(required_key)

    if missing_fields:
        console.print(f"[red]Error: Missing required metadata fields: {', '.join(missing_fields)}[/red]")
        console.print("[yellow]Required fields:[/yellow]")
        for key, field in bucket_obj.requiredMetadata.items():
            console.print(f"  - {key}: {field.description} (example: {field.example})")
        raise click.Abort()

    segments = allowed_segments.split(",") if allowed_segments else None
    emails = allowed_emails.split(",") if allowed_emails else None
    result = await asset.upload_asset(
        bucket=bucket,
        file_path=str(path),
        allowed_segments=segments,
        allowed_emails=emails,
        metadata=metadata_dict,
        force=force,
    )

    console.print("[green]✓ File uploaded successfully![/green]")
    console.print(f"[bold]Bucket:[/bold] {bucket}")
    console.print(f"[bold]Key:[/bold] {result.key}")
    console.print(f"[bold]Version:[/bold] {result.version}")
