from __future__ import annotations

from pathlib import Path

import rich.status
import rich_click as click
from rich.markup import escape

from hcli.lib.console import console, stderr_console
from hcli.lib.ida.python import PythonNotFoundError, find_current_python_executable


def get_python_exe() -> Path:
    """Resolve the Python interpreter that IDA loads, reporting failures to the user.

    Raises:
        click.Abort: if the interpreter can't be detected.
    """
    try:
        with rich.status.Status("finding IDA's Python interpreter", console=stderr_console):
            return find_current_python_executable()
    except PythonNotFoundError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise click.Abort()
