from __future__ import annotations

import rich.status
import rich_click as click
from rich.markup import escape

from hcli.lib.console import console, stderr_console
from hcli.lib.ida.python import ProbeError, get_script_info, render_script_not_found

from ._common import get_python_exe


@click.command()
@click.argument("name")
def find_script(name: str) -> None:
    """Show the path of a script installed in IDA's Python environment.

    \b
    NAME: name of the script, like `speakeasy`

    Prints nothing and exits non-zero when the script isn't installed.
    """
    python_exe = get_python_exe()

    try:
        with rich.status.Status(f"looking for {name}", console=stderr_console):
            info = get_script_info(python_exe, name)
    except ProbeError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise click.Abort()

    if info.path is None:
        console.print(f"[red]{escape(render_script_not_found(info))}[/red]")
        raise click.Abort()

    console.print(str(info.path), highlight=False, soft_wrap=True)
