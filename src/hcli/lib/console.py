import sys

import rich_click as click
from rich.console import Console


def _get_console() -> Console:
    """Get console instance with quiet mode support."""
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console()


def _get_stderr_console() -> Console:
    """Get console instance with quiet mode support."""
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True, stderr=True)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console(stderr=True)


console = _get_console()
stderr_console = _get_stderr_console()


def _sync_console_streams():
    """
    helper to reset the console file handles.

    useful in pytest environment, where click test integration may manipulate stdout/err
    see #190
    """
    console.file = sys.stdout
    stderr_console.file = sys.stderr


_sync_console_streams()
