import rich_click as click
from rich.console import Console


def __get_console() -> Console:
    """Get console instance with quiet mode support."""
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console()


def __get_stderr_console() -> Console:
    """Get console instance with quiet mode support."""
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True, stderr=True)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console(stderr=True)


# Global instances for convenience
console = __get_console()
stderr_console = __get_stderr_console()
