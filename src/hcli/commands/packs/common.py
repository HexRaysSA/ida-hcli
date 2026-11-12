"""Shared options, decorators and rendering for the packs command group.

The commands are async, as hcli's API client is: ``packs_errors`` sits above
``async_command``, which runs the command (and ``with_repository``) to completion.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, Final, NoReturn, TypeVar, cast

import rich_click as click
from rich.text import Text

from hcli.lib.console import console, print_json
from hcli.lib.packs.errors import PacksError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Snapshot ids are UUIDs; the first group is plenty to tell them apart by eye.
SHORT_SNAPSHOT_LEN: Final = 8
SHORT_COMMIT_LEN: Final = 8
# How help texts and messages name each format.
FORMAT_NAMES: Final = {PackFormat.LUMINA: "Lumina", PackFormat.FLIRT: "FLIRT"}

json_option = click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output JSON (errors too: an object with error, kind and hint).",
)


def render_error(error: PacksError, as_json: bool) -> NoReturn:
    """Print a PacksError (JSON object on stdout with ``--json``) and exit 1."""
    if as_json:
        print_json(error.to_json())
    else:
        # soft_wrap: long URLs and commands stay intact for copy-paste.
        console.print(Text.assemble(("✗ ", "red"), (error.message, "red")), soft_wrap=True)
        if error.hint:
            console.print(Text.assemble("  ", ("Probably fixed by: ", "dim"), error.hint), soft_wrap=True)
    raise SystemExit(1)


def packs_errors(f: F) -> F:
    """Render any PacksError (or unexpected failure) consistently, honoring ``--json``."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        as_json = bool(kwargs.get("as_json"))
        try:
            return f(*args, **kwargs)
        except PacksError as e:
            # The transport failure behind a classified error (HCLI_DEBUG=1 shows it).
            logger.debug("packs command failed: %s", e.message, exc_info=True)
            render_error(e, as_json)
        except Exception as e:
            logger.debug("packs command failed unexpectedly", exc_info=True)
            render_error(
                PacksError(
                    f"Unexpected error: {type(e).__name__}: {e}",
                    "re-run with HCLI_DEBUG=1 for the traceback, and report it",
                ),
                as_json,
            )

    return cast(F, wrapper)


def with_repository(f: F) -> F:
    """Inject ``repo`` (a PackRepository, closed when the command returns). Stack it under ``packs_errors``.

    The commands are AuthCommands: hcli checks the login before they run.
    """

    @wraps(f)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        async with PackRepository() as repo:
            return await f(*args, repo=repo, **kwargs)

    return cast(F, wrapper)


def short_snapshot(snapshot: str | None) -> str:
    return snapshot[:SHORT_SNAPSHOT_LEN] if snapshot else "-"


def format_mib(size: int | None) -> str:
    if size is None:
        return "-"
    return f"{size / (1024 * 1024):.1f} MiB"


def format_timestamp(timestamp: str | None, *, date_only: bool = False) -> str:
    """``2026-10-29T09:29:10Z`` -> ``2026-10-29 09:29:10Z`` (or ``2026-10-29`` with ``date_only``)."""
    if not timestamp:
        return "-"
    if date_only:
        return timestamp.split("T", 1)[0]
    return timestamp.replace("T", " ")


def format_content(fmt: PackFormat, pack: ReleasePack) -> str:
    """Lumina packs count functions (rows); FLIRT packs count signatures (members)."""
    if fmt is PackFormat.LUMINA and pack.rows is not None:
        return f"{pack.rows:,} funcs"
    if fmt is PackFormat.FLIRT:
        return f"{pack.members:,} sigs"
    return f"{pack.members:,} members"


def pack_json(fmt: PackFormat, pack: ReleasePack, **extra: Any) -> dict[str, Any]:
    """A pack entry for ``--json``: its Release fields plus format and address."""
    return {"format": fmt.value, "address": pack.address, **pack.model_dump(mode="json"), **extra}
