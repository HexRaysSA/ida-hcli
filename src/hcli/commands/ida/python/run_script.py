from __future__ import annotations

import rich_click as click
from rich.markup import escape

import hcli.lib.ida.python
from hcli.lib.commands import PassthroughCommand
from hcli.lib.console import console
from hcli.lib.ida.python import ProbeError, ScriptNotFoundError

from ._common import get_python_exe


@click.command(cls=PassthroughCommand, context_settings={"ignore_unknown_options": True})
@click.argument("name")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def run_script(ctx: click.Context, name: str, args: tuple[str, ...]) -> None:
    """Run a script installed in IDA's Python environment.

    \b
    NAME: name of the script, like `speakeasy`
    ARGS: arguments passed through to the script

    HCLI exits with the status of the script.

    \b
    Examples:
      hcli ida python run-script speakeasy -t sample.exe
      hcli ida python run-script speakeasy --help
    """
    python_exe = get_python_exe()

    try:
        status = hcli.lib.ida.python.run_script(python_exe, name, args)
    except (ProbeError, ScriptNotFoundError, OSError) as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise click.Abort()

    ctx.exit(status)
