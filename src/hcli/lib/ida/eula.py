"""Ask the user to accept the Hex-Rays EULA of an IDA installation.

Writing the acceptance to the IDA registry is `hcli.lib.ida.accept_eula`.
This module decides whether hcli may write it.
"""

from __future__ import annotations

import enum
import sys
from pathlib import Path
from typing import TextIO

from rich.console import Console
from rich.pager import Pager
from rich.prompt import Confirm

from hcli.lib.ida import get_ida_path

EULA_HEADER = (
    "Please read the following License Agreement. You must accept the terms of this agreement before you continue."
)


class EulaAction(enum.Enum):
    ACCEPT = "accept"
    PROMPT = "prompt"
    SKIP = "skip"
    NO_TERMINAL = "no-terminal"
    """hcli must ask the user, but there is no terminal to ask on."""


class EulaNeedsTerminalError(RuntimeError):
    def __init__(self, license_path: Path) -> None:
        super().__init__(f"EULA acceptance needs a terminal; read {license_path} and run with --yes")
        self.license_path = license_path


def get_eula_action(accept_eula: bool | None, interactive: bool) -> EulaAction:
    """Decide how to handle the EULA.

    Args:
        accept_eula: True when the user gives the accept flag, False when the user
          gives the do-not-accept flag, None when the user gives neither.
        interactive: whether stdin and stdout are a terminal.
    """
    if accept_eula is True:
        return EulaAction.ACCEPT
    if accept_eula is False:
        return EulaAction.SKIP
    if interactive:
        return EulaAction.PROMPT
    return EulaAction.NO_TERMINAL


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def get_license_text_path(ida_dir: Path) -> Path:
    """Get the path of the EULA text (`license.txt`) of an IDA installation."""
    return get_ida_path(ida_dir) / "license.txt"


def render_eula_record(license_path: Path) -> str:
    return f"Accepted the Hex-Rays EULA ({license_path})"


def ask_eula_acceptance(
    license_path: Path,
    console: Console,
    pager: Pager | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Show the EULA text in a pager, then ask the user to accept it.

    Args:
        pager: the pager to show the text in. The default is the system pager ($PAGER or `less`).
        stream: where to read the answer from. The default is stdin.

    Returns:
        True only when the user answers yes. The default answer is no.

    Raises:
        OSError: the EULA text cannot be read, such as FileNotFoundError when the installation has no `license.txt`.
    """
    text = license_path.read_text(encoding="utf-8")
    with console.pager(pager=pager):
        console.print(EULA_HEADER, markup=False, highlight=False)
        console.print()
        console.print(text, markup=False, highlight=False)
    return Confirm.ask("Do you accept the Hex-Rays EULA?", default=False, console=console, stream=stream)


def get_eula_consent(
    license_path: Path,
    action: EulaAction,
    console: Console,
    pager: Pager | None = None,
    stream: TextIO | None = None,
) -> bool:
    """Get whether hcli may accept the EULA for the user.

    Raises:
        EulaNeedsTerminalError: the action is NO_TERMINAL.
        OSError: the action is PROMPT and the EULA text cannot be read.
    """
    if action is EulaAction.ACCEPT:
        return True
    if action is EulaAction.SKIP:
        return False
    if action is EulaAction.NO_TERMINAL:
        raise EulaNeedsTerminalError(license_path)
    return ask_eula_acceptance(license_path, console, pager=pager, stream=stream)
