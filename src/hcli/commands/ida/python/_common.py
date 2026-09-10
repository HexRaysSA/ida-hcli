from __future__ import annotations

from pathlib import Path

import rich.status
import rich_click as click
from rich.markup import escape

from hcli.lib.console import console, stderr_console
from hcli.lib.ida.python import PythonNotFoundError, resolve_current_python
from hcli.lib.ida.python.environment import warn_python_environment


def is_python_environment_check_disabled(ctx: click.Context | None) -> bool:
    """Whether `--no-python-environment-check` was given on an enclosing group."""
    while ctx is not None:
        if isinstance(ctx.obj, dict) and ctx.obj.get("no_python_environment_check"):
            return True
        ctx = ctx.parent
    return False


def get_python_exe() -> Path:
    """Resolve the Python interpreter that IDA loads, reporting failures to the user.

    A non-recommended environment produces a warning on stderr but never
    stops the command: `exec` and `run-script` are the tools a user reaches for
    to repair that same environment, so they must keep working in it.

    Raises:
        click.Abort: if the interpreter can't be detected.
    """
    try:
        with rich.status.Status("finding IDA's Python interpreter", console=stderr_console):
            resolved = resolve_current_python()
    except PythonNotFoundError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise click.Abort()

    if not is_python_environment_check_disabled(click.get_current_context(silent=True)):
        warn_python_environment(resolved)

    return resolved.exe
