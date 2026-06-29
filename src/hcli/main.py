from __future__ import annotations

import logging
import os
import platform
import tempfile

# Ensure all Python subprocesses (pip, idat scripts, etc.) use UTF-8 on Windows,
# where the default encoding is typically a legacy codepage.
os.environ["PYTHONUTF8"] = "1"

import rich_click as click
from rich.logging import RichHandler

import hcli.lib.console
from hcli.commands import register_commands
from hcli.env import ENV
from hcli.lib.console import console, stderr_console
from hcli.lib.extensions import get_extensions
from hcli.lib.update.version import BackgroundUpdateChecker, is_binary

# Configure rich-click styling
click.rich_click.USE_RICH_MARKUP = True

# Global update checker instance
update_checker: BackgroundUpdateChecker | None = None


def _get_status_section() -> str:
    """Build a status summary from cheap local state (no network calls)."""
    try:
        from pathlib import Path

        from hcli.lib.config import config_store
        from hcli.lib.constants.auth import CONFIG_CREDENTIALS, CredentialsConfig
        from hcli.lib.ida import (
            _normalize_install_dir,
            get_ida_config,
            is_ida_dir,
            is_idalib_capable_installation,
            parse_instance_version,
        )

        lines: list[str] = []

        # -- Auth --
        if ENV.HCLI_API_KEY:
            lines.append("  Auth:  [green]API key configured[/green] [dim](HCLI_API_KEY)[/dim]")
        else:
            email = None
            config_data = config_store.get_object(CONFIG_CREDENTIALS)
            if config_data:
                try:
                    creds = CredentialsConfig(**config_data)
                    default_cred = creds.get_default_credentials()
                    if default_cred and default_cred.email:
                        email = default_cred.email
                except Exception:
                    pass
            if email:
                lines.append(f"  Auth:  [green]{email}[/green]")
            else:
                lines.append(f"  Auth:  [yellow]Not logged in[/yellow] → {ENV.HCLI_BINARY_NAME} login")

        # -- IDA --
        instances: dict[str, str] = config_store.get_object("ida.instances", {}) or {}
        default_name = config_store.get_string("ida.default", "")

        if instances and default_name and default_name in instances:
            path = Path(instances[default_name])
            if path.exists() and is_ida_dir(path):
                version = parse_instance_version(default_name, path)
                ver = f" {version}" if version else ""
                lines.append(f"  IDA:   [green]IDA{ver}[/green] at {path}")
            else:
                lines.append(f"  IDA:   [red]Not found[/red] at {path}")
                lines.append(f"         → {ENV.HCLI_BINARY_NAME} ida install -d ida-pro:latest -y")
        elif not instances:
            lines.append("  IDA:   [yellow]Not installed[/yellow]")
            lines.append(f"         → {ENV.HCLI_BINARY_NAME} ida install -d ida-pro:latest -l LICENSE_ID -y")
            lines.append(f"         [dim]Find your license ID with: {ENV.HCLI_BINARY_NAME} license list[/dim]")
            lines.append("         [dim]Installing also activates idalib for Python (import idapro).[/dim]")
        else:
            valid = sum(1 for p in instances.values() if Path(p).exists() and is_ida_dir(Path(p)))
            lines.append(f"  IDA:   [yellow]{len(instances)} instance(s), no default set[/yellow] ({valid} valid)")
            lines.append(f"         → {ENV.HCLI_BINARY_NAME} ida switch")

        # -- idalib (from ida-config.json, independent of hcli default) --
        try:
            ida_config = get_ida_config()
            idalib_dir = ida_config.paths.installation_directory
            if idalib_dir:
                idalib_path = _normalize_install_dir(idalib_dir)
                if idalib_path.exists() and is_idalib_capable_installation(idalib_path):
                    # Only show the path when it differs from the hcli default
                    if default_name and default_name in instances:
                        hcli_default_path = Path(instances[default_name])
                        try:
                            same = hcli_default_path.resolve() == idalib_path.resolve()
                        except OSError:
                            same = False
                    else:
                        same = False
                    if same:
                        lines.append("  idalib: [green]active[/green]")
                    else:
                        lines.append(f"  idalib: [green]active[/green] [dim]({idalib_path})[/dim]")
                else:
                    lines.append(f"  idalib: [red]not found[/red] at {idalib_path}")
            elif instances:
                lines.append(
                    "  idalib: [yellow]not configured[/yellow] [dim](ida-config.json has no ida-install-dir)[/dim]"
                )
        except Exception:
            pass

        return "\n\n\b\n[bold]Status:[/bold]\n" + "\n".join(lines)
    except Exception:
        return ""


def get_help_text():
    """Generate help text with extensions information."""
    base_help = f"[bold blue]{ENV.HCLI_BINARY_NAME.upper()}[/bold blue] [dim](v{ENV.HCLI_VERSION}{ENV.HCLI_VERSION_EXTRA})[/dim]\n\n[yellow]Hex-Rays Command-line interface for managing IDA installation, licenses and more.[/yellow]"

    base_help += _get_status_section()

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
                    f"[red]Authentication failed. Please check your credentials or use '{ENV.HCLI_BINARY_NAME} login'.[/red]"
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

    # fix #190: stale stdout/err handles due to click pytest integration
    hcli.lib.console._sync_console_streams()

    if is_binary() and not (disable_updates or ENV.HCLI_DISABLE_UPDATES):
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
