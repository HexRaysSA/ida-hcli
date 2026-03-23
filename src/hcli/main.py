from __future__ import annotations

import logging
import os
import platform
import sys
import tempfile

# Ensure all Python subprocesses (pip, idat scripts, etc.) use UTF-8 on Windows,
# where the default encoding is typically a legacy codepage.
os.environ["PYTHONUTF8"] = "1"

APP_PROFILE_IMPORT_NAMES = {
    "click",
    "gotrue",
    "pip",
    "questionary",
    "requests",
    "rich",
    "rich_click",
    "supabase",
    "yaml",
}


def _build_cli():
    import rich_click as click
    from rich.logging import RichHandler

    from hcli.commands import register_commands
    from hcli.env import ENV
    from hcli.lib.console import console, stderr_console
    from hcli.lib.extensions import get_extensions
    from hcli.lib.update.version import BackgroundUpdateChecker, is_binary

    # Configure rich-click styling
    click.rich_click.USE_RICH_MARKUP = True

    # Global update checker instance
    update_checker: BackgroundUpdateChecker | None = None

    def get_help_text():
        """Generate help text with extensions information."""
        base_help = f"[bold blue]{ENV.HCLI_BINARY_NAME.upper()}[/bold blue] [dim](v{ENV.HCLI_VERSION}{ENV.HCLI_VERSION_EXTRA})[/dim]\n\n[yellow]!!!!! Hex-Rays Command-line interface for managing IDA installation, licenses and more.[/yellow]"

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
                from hcli.lib.api.common import (
                    APIError,
                    AuthenticationError,
                    NotFoundError,
                    RateLimitError,
                )
                from hcli.lib.util.io import NoSpaceError

                if isinstance(e, NoSpaceError):
                    console.print(f"[bold red]Error: No space left on device at {e.path}[/bold red]")
                    if e.required_bytes and e.available_bytes:
                        console.print(
                            f"  [dim]Required: {e.required_bytes} bytes, Available: {e.available_bytes} bytes[/dim]"
                        )

                    temp_dir = tempfile.gettempdir().lower()
                    path_str = str(e.path).lower()
                    is_temp_path = path_str.startswith(temp_dir) or "temp" in path_str

                    if is_temp_path:
                        env_var = "TMPDIR" if platform.system() != "Windows" else "TEMP/TMP"
                        console.print(
                            f"\n[yellow]Suggestion:[/yellow] If your temporary directory is full, you can use a different one by setting the [bold]{env_var}[/bold] environment variable."
                        )
                elif isinstance(e, AuthenticationError):
                    console.print(
                        "[red]Authentication failed. Please check your credentials or use 'hcli login'.[/red]"
                    )
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
                    if ENV.HCLI_DEBUG:
                        import traceback

                        console.print(f"[dim]{traceback.format_exc()}[/dim]")

                raise click.Abort()

    @click.pass_context
    def handle_command_completion(_ctx, _result, **_kwargs):
        """Handle command completion and show update notifications."""
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
        if is_binary() and not (disable_updates or ENV.HCLI_DISABLE_UPDATES):
            nonlocal update_checker

            update_checker = BackgroundUpdateChecker()
            update_checker.start_check()

        _ctx.ensure_object(dict)
        _ctx.obj["quiet"] = quiet
        _ctx.obj["auth"] = auth
        _ctx.obj["auth_credentials"] = auth_credentials

        if ENV.HCLI_DEBUG:
            handler = RichHandler(show_time=False, show_path=False, rich_tracebacks=True, console=stderr_console)
            logging.basicConfig(level=logging.DEBUG, format="%(message)s", datefmt="[%X]", handlers=[handler])

    register_commands(cli)
    for extension in get_extensions():
        extension["function"](cli)

    return cli


def _missing_app_profile_cli(error: ModuleNotFoundError):
    missing_dependency = error.name or "an optional dependency"

    def cli():
        binary_name = os.getenv("HCLI_BINARY_NAME", "hcli")
        print(
            f"{binary_name} requires the optional 'app' dependency profile. "
            f"Missing module: {missing_dependency}. "
            "Install it with `pip install 'ida-hcli[app]'`.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return cli


try:
    cli = _build_cli()
except ModuleNotFoundError as error:
    if error.name not in APP_PROFILE_IMPORT_NAMES:
        raise

    cli = _missing_app_profile_cli(error)

if __name__ == "__main__":
    cli()
