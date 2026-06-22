"""Plugin schema command."""

from __future__ import annotations

import json
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida.plugin import IDAMetadataDescriptor


@click.command(hidden=True)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Write the schema to this file instead of stdout.",
)
@click.option(
    "--indent",
    type=int,
    default=2,
    show_default=True,
    help="Indentation for the emitted JSON.",
)
def schema(output: Path | None, indent: int) -> None:
    """Print the JSON Schema for ida-plugin.json."""
    schema_obj = IDAMetadataDescriptor.model_json_schema(by_alias=True)
    payload = json.dumps(schema_obj, indent=indent)

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8", newline="\n")
        console.print(f"[green]wrote schema to {output}[/green]")
    else:
        click.echo(payload)
