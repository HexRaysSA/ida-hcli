import sys
from collections.abc import Mapping

import rich_click as click
from rich.console import Console


def _is_quiet_context() -> bool:
    """Check if quiet mode is enabled in the current Click context.

    ctx.obj is arbitrary in Click and may not implement .get(), causing
    AttributeError when hcli is imported as a library. Guard with Mapping
    instead of assuming dict.
    """
    try:
        ctx = click.get_current_context(silent=True)
        return bool(ctx and isinstance(ctx.obj, Mapping) and ctx.obj.get("quiet", False))
    except RuntimeError:
        return False


def _get_console() -> Console:
    """Get console instance with quiet mode support."""
    return Console(quiet=True) if _is_quiet_context() else Console()


def _get_stderr_console() -> Console:
    """Get stderr console instance with quiet mode support."""
    return Console(quiet=True, stderr=True) if _is_quiet_context() else Console(stderr=True)


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
