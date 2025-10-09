from __future__ import annotations

import json
from pathlib import Path

import rich_click as click

from hcli.lib.console import console
from hcli.lib.ida import (
    find_standard_installations,
    get_ida_config_path,
    is_ida_dir,
)


@click.command(name="set-default")
@click.argument("path", required=False)
def set_default_ida(path: str | None) -> None:
    """Set or show the default IDA installation directory."""
    config_path = get_ida_config_path()

    if path is None:
        if not config_path.exists():
            console.print("[yellow]No default IDA installation set.[/yellow]")
        else:
            doc = json.loads(config_path.read_text(encoding="utf-8"))
            default_path = doc.get("Paths", {}).get("ida-install-dir")
            if default_path:
                console.print(f"[green]Default IDA installation: {default_path}[/green]")
            else:
                console.print("[yellow]No default IDA installation set.[/yellow]")

        installations = find_standard_installations()
        if installations:
            console.print("\nAvailable installations:")
            for install_dir in installations:
                console.print(f"  - {install_dir}")
        else:
            console.print("\n[grey69]No standard installations found.[/grey69]")
        return

    install_dir = Path(path)

    if not install_dir.exists():
        console.print(f"[red]Path does not exist: {install_dir}[/red]")
        return

    if not is_ida_dir(install_dir):
        console.print(f"[red]Not a valid IDA installation directory: {install_dir}[/red]")
        console.print("[grey69]The directory must contain the IDA binary.[/grey69]")
        return

    if not config_path.exists():
        console.print("[yellow]Creating configuration...[/yellow]")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        _ = config_path.write_text(json.dumps({"Paths": {"ida-install-dir": str(install_dir.absolute())}}))
        console.print("[grey69]Wrote default ida-config.json[/grey69]")
        console.print(f"[green]Set default IDA installation: {install_dir.absolute()}[/green]")
    else:
        doc = json.loads(config_path.read_text(encoding="utf-8"))
        if "Paths" not in doc:
            doc["Paths"] = {}
        existing = doc["Paths"].get("ida-install-dir") or "(empty)"
        new = str(install_dir.absolute())
        doc["Paths"]["ida-install-dir"] = new
        _ = config_path.write_text(json.dumps(doc), encoding="utf-8")
        console.print("[grey69]Updated ida-config.json:[/grey69]")
        console.print(f"[grey69]  default install path: {existing}[/grey69]")
        console.print(f"[grey69]                     -> {new}[/grey69]")
