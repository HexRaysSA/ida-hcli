from __future__ import annotations

import rich_click as click
from rich.markup import escape

from hcli.lib.commands import PassthroughCommand
from hcli.lib.console import console
from hcli.lib.ida.python import run_in_python_environment

from ._common import get_python_exe


@click.command(cls=PassthroughCommand, context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def exec_python(ctx: click.Context, args: tuple[str, ...]) -> None:
    """Run IDA's Python interpreter, passing through all arguments.

    Without arguments, this starts an interactive interpreter.
    HCLI exits with the status of the interpreter.

    \b
    Examples:
      hcli ida python exec -m pip install requests
      hcli ida python exec -c "import requests; print('ok')"
      hcli ida python exec script.py --flag
      hcli ida python exec --version

    \b
    `--help` here describes this command; pass it to the interpreter with `--`:
      hcli ida python exec -- --help
    """
    python_exe = get_python_exe()

    try:
        status = run_in_python_environment(python_exe, [str(python_exe), *args])
    except OSError as e:
        console.print(f"[red]failed to run {escape(str(python_exe))}: {escape(str(e))}[/red]")
        raise click.Abort()

    ctx.exit(status)
