import asyncio
import sys
from functools import wraps
from typing import Callable

import rich_click as click

from hcli.lib.auth import get_auth_service
from hcli.lib.console import console
from hcli.lib.constants.auth import CredentialType


def require_auth(f: Callable) -> Callable:
    """Decorator to require authentication before executing a command."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_service = get_auth_service()
        auth_service.init()

        # Check if user is logged in
        if not auth_service.is_logged_in():
            if auth_service.has_expired_session():
                current_source = auth_service.get_current_credentials()
                email = current_source.email if current_source else "unknown"
                console.print(f"[red]Your session {email} has expired, use 'hcli login'.[/red]")
            else:
                console.print("[red]You are not logged in. Use 'hcli login'.[/red]")
            sys.exit(1)

        return f(*args, **kwargs)

    return wrapper


def async_command(f: Callable) -> Callable:
    """Decorator to run async functions in Click commands."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Running inside an existing loop (e.g. inside another async click command)
            return f(*args, **kwargs)  # returns coroutine; the caller must await
        else:
            return asyncio.run(f(*args, **kwargs))  # standalone CLI entrypoint

    return wrapper


def enforce_login() -> bool:
    """Check if user is logged in, exit if not."""
    auth_service = get_auth_service()

    if not auth_service.is_logged_in():
        if auth_service.has_expired_session():
            current_source = auth_service.get_current_credentials()
            email = current_source.email if current_source else "unknown"
            console.print(f"[red]Your session {email} has expired, use 'hcli login'.[/red]")
        else:
            console.print("[red]You are not logged in. Use 'hcli login'.[/red]")
        sys.exit(1)

    return True


class BaseCommand(click.RichCommand):
    """Base command class with optional authentication."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class AuthCommand(BaseCommand):
    """Command class that requires authentication."""

    def __init__(self, *args, auth_type: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth_type = auth_type

    def invoke(self, ctx: click.Context):
        """Override invoke to check authentication before execution."""
        # Get forced credentials from global option
        forced_credentials = None
        if ctx.obj and "auth_credentials" in ctx.obj:
            forced_credentials = ctx.obj["auth_credentials"]

        # Initialize auth service with forced source
        auth_service = get_auth_service()
        auth_service.init(forced_credentials=forced_credentials)

        # Determine auth type priority: decorator > global option > auto
        forced_auth_type = self.auth_type
        if not forced_auth_type and ctx.obj and "auth" in ctx.obj:
            forced_auth_type = ctx.obj["auth"]

        # Validate auth type if specified
        if forced_auth_type and forced_auth_type not in CredentialType.VALID_TYPES:
            console.print(
                f"[red]Invalid auth type '{forced_auth_type}'. Must be '{CredentialType.INTERACTIVE}' or '{CredentialType.KEY}'.[/red]"
            )
            sys.exit(1)

        # Check if user is logged in
        if not auth_service.is_logged_in():
            if auth_service.has_expired_session():
                current_source = auth_service.get_current_credentials()
                email = current_source.email if current_source else "unknown"
                console.print(f"[red]Your session {email} has expired, use 'hcli login'.[/red]")
            else:
                console.print("[red]You are not logged in. Use 'hcli login'.[/red]")
            sys.exit(1)

        # Validate forced credentials exists
        if forced_credentials:
            current_source = auth_service.get_current_credentials()
            if not current_source or current_source.name != forced_credentials:
                available_sources = [s.name for s in auth_service.list_credentials()]
                console.print(f"[red]Credentials '{forced_credentials}' not found.[/red]")
                if available_sources:
                    console.print(f"Available credentials: {', '.join(available_sources)}")
                else:
                    console.print("No credentials available. Use 'hcli login' or 'hcli auth key install'.")
                sys.exit(1)

        # If auth type is forced, validate current auth matches
        if forced_auth_type:
            current_auth = auth_service.get_auth_type()
            if current_auth["type"] != forced_auth_type:
                console.print(
                    f"[red]Authentication type mismatch. Required: '{forced_auth_type}', current: '{current_auth['type']}'.[/red]"
                )
                if forced_auth_type == CredentialType.INTERACTIVE:
                    console.print("[yellow]Please use 'hcli login' to authenticate interactively.[/yellow]")
                else:
                    console.print(
                        "[yellow]Please set an API key using 'hcli auth key install' or HCLI_API_KEY environment variable.[/yellow]"
                    )
                sys.exit(1)

        # Call parent invoke
        return super().invoke(ctx)


# Click command decorators
def base_command(*args, **kwargs):
    """Decorator for creating a base command."""
    kwargs.setdefault("cls", BaseCommand)
    return click.command(*args, **kwargs)


def auth_command(*args, auth_type: str | None = None, **kwargs):
    """Decorator for creating an authenticated command."""
    kwargs.setdefault("cls", AuthCommand)

    def decorator(f):
        # Create the command with auth_type parameter
        command_kwargs = kwargs.copy()
        if "cls" in command_kwargs:
            # Pass auth_type to the AuthCommand class
            original_cls = command_kwargs["cls"]

            class AuthCommandWithType(original_cls):  # type: ignore[misc, valid-type]
                def __init__(self, *cmd_args, **cmd_kwargs):
                    super().__init__(*cmd_args, auth_type=auth_type, **cmd_kwargs)

            command_kwargs["cls"] = AuthCommandWithType

        return click.command(*args, **command_kwargs)(f)

    return decorator
