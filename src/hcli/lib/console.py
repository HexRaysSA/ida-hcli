import json
from collections.abc import Mapping
from typing import Any

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


def print_json(data: Any) -> None:
    """Print `data` as JSON via `console`, not `click.echo`.

    `click.echo` resolves its output stream independently of `console`, so it
    isn't covered by the stream pinning the test harness applies around
    `CliRunner` and can write to a stale stdout handle (see #190). Markup and
    highlighting are disabled so JSON's own brackets aren't parsed as Rich
    markup, and soft_wrap avoids Rich reflowing long lines.
    """
    console.print(json.dumps(data, indent=2), markup=False, highlight=False, soft_wrap=True)
