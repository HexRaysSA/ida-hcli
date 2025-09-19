import os
import platform

import rich_click as click
from rich.console import Console


def __get_console() -> Console:
    """Get console instance with quiet mode support and proper Windows encoding handling."""
    # Handle Windows console encoding issues
    console_kwargs = {}
    if platform.system() == "Windows":
        # Force UTF-8 mode on Windows for better Unicode support
        console_kwargs["force_terminal"] = True
        console_kwargs["legacy_windows"] = False
        # Disable box characters if terminal can't handle Unicode
        console_kwargs["no_color"] = os.environ.get("NO_COLOR", "").lower() in ("1", "true", "yes")
        
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True, **console_kwargs)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console(**console_kwargs)


def __get_stderr_console() -> Console:
    """Get console instance with quiet mode support and proper Windows encoding handling."""
    # Handle Windows console encoding issues
    console_kwargs = {"stderr": True}
    if platform.system() == "Windows":
        # Force UTF-8 mode on Windows for better Unicode support
        console_kwargs["force_terminal"] = True
        console_kwargs["legacy_windows"] = False
        # Disable box characters if terminal can't handle Unicode
        console_kwargs["no_color"] = os.environ.get("NO_COLOR", "").lower() in ("1", "true", "yes")
        
    try:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and ctx.obj.get("quiet", False):
            return Console(quiet=True, **console_kwargs)
    except RuntimeError:
        # No context available, return default console
        pass
    return Console(**console_kwargs)


# Global instances for convenience
console = __get_console()
stderr_console = __get_stderr_console()
