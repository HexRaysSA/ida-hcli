from __future__ import annotations

import logging

import rich_click as click
from rich.logging import RichHandler

from hcli.commands import register_commands
from hcli.env import ENV
from hcli.lib.console import console, stderr_console
from hcli.lib.extensions import get_extensions
from hcli.lib.update.version import BackgroundUpdateChecker

# Configure rich-click styling
click.rich_click.USE_RICH_MARKUP = True

# Global update checker instance
update_checker: BackgroundUpdateChecker | None = None


def get_help_text():
    """Generate help text with extensions information."""
    base_help = f"[bold blue]{ENV.HCLI_BINARY_NAME.upper()}[/bold blue] [dim](v{ENV.HCLI_VERSION}{ENV.HCLI_VERSION_EXTRA})[/dim]\n\n[yellow]Hex-Rays Command-line interface for managing IDA installation, licenses and more.[/yellow]"

    # Check for available extensions
    extensions = get_extensions()

    if extensions:
        extensions_list = ", ".join([f"{ext['name']} [dim]\\[v{ext['version']}][/dim]" for ext in extensions])
        base_help += f"\n\n[bold green]Extensions:[/bold green] [cyan]{extensions_list}[/cyan]"

    return base_help


class MainGroup(click.RichGroup):
    """Custom Rich Click Group with global exception handling."""

    def main(self, *args, **kwargs):
        """Override main to add global exception handling."""
        try:
            return super().main(*args, **kwargs)
        except Exception as e:
            # Import here to avoid circular imports
            from hcli.lib.api.common import APIError, AuthenticationError, NotFoundError, RateLimitError

            if isinstance(e, AuthenticationError):
                console.print("[red]Authentication failed. Please check your credentials or use 'hcli login'.[/red]")
            elif isinstance(e, NotFoundError):
                console.print(f"[red]Resource not found: {e}[/red]")
            elif isinstance(e, RateLimitError):
                console.print("[red]Rate limit exceeded. Please try again later.[/red]")
            elif isinstance(e, APIError):
                console.print(f"[red]API Error: {e}[/red]")
            elif isinstance(e, KeyboardInterrupt):
                console.print("\n[yellow]Operation cancelled by user[/yellow]")
            else:
                console.print(f"[red]Unexpected error: {e}[/red]")
                # Optionally include debug info in debug mode
                if ENV.HCLI_DEBUG:
                    import traceback

                    console.print(f"[dim]{traceback.format_exc()}[/dim]")

            raise click.Abort()


@click.pass_context
def handle_command_completion(_ctx, _result, **_kwargs):
    """Handle command completion and show update notifications."""
    # Show update message if available (result callback only runs on success)
    update_msg = update_checker.get_result(timeout=2.0) if update_checker else None
    if update_msg:
        console.print(update_msg, markup=True)


@click.group(help=get_help_text(), cls=MainGroup, result_callback=handle_command_completion)
@click.version_option(version=f"{ENV.HCLI_VERSION}{ENV.HCLI_VERSION_EXTRA}", package_name="ida-hcli")
@click.option("--quiet", "-q", is_flag=True, help="Run without prompting the user")
@click.option("--auth", "-a", help="Force authentication type (interactive|key)", default=None)
@click.option("--auth-credentials", "-s", help="Force specific credentials by name", default=None)
@click.option("--disable-updates", is_flag=True, help="Disable automatic update checking")
@click.pass_context
def cli(_ctx, quiet, auth, auth_credentials, disable_updates: bool):
    """Main CLI entry point with background update checking."""
    if not (disable_updates or ENV.HCLI_DISABLE_UPDATES):
        global update_checker

        # Initialize update checker
        update_checker = BackgroundUpdateChecker()

        # Start background check (non-blocking)
        update_checker.start_check()

    _ctx.ensure_object(dict)
    _ctx.obj["quiet"] = quiet
    _ctx.obj["auth"] = auth
    _ctx.obj["auth_credentials"] = auth_credentials

    if ENV.HCLI_DEBUG:
        handler = RichHandler(show_time=False, show_path=False, rich_tracebacks=True, console=stderr_console)
        logging.basicConfig(level=logging.DEBUG, format="%(message)s", datefmt="[%X]", handlers=[handler])


# register subcommands
register_commands(cli)
# Register extensions dynamically
for extension in get_extensions():
    extension["function"](cli)

if __name__ == "__main__":
    cli()
