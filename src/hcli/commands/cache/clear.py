from __future__ import annotations

import logging
import sys

import rich_click as click
from rich.markup import escape

from hcli.lib.console import console, print_json
from hcli.lib.util.cache import get_default_cache_directory
from hcli.lib.util.cache_clear import (
    apply_clear_plan,
    get_clear_plan,
    get_clear_report,
    render_clear_listing,
    render_size,
)

logger = logging.getLogger(__name__)

NEEDS_YES = "pass --yes to delete the cache without a terminal"


@click.command()
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be deleted, and delete nothing.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Delete without asking for confirmation.")
@click.option("--json", "json_output", is_flag=True, default=False, help="Output machine-readable JSON.")
def clear_cache(dry_run: bool, yes: bool, json_output: bool) -> None:
    """Delete hcli's cache of downloaded files.

    It is always safe to clear the cache. hcli downloads the files again when it needs them.
    """
    root = get_default_cache_directory()
    logger.debug("cache root: %s", root)
    plan = get_clear_plan(root)

    if plan.is_empty or dry_run:
        if json_output:
            print_json(get_clear_report(plan, None).model_dump(mode="json"))
        elif plan.is_empty:
            console.print("The cache is empty.")
        else:
            console.print(escape(render_clear_listing(plan)))
        return

    if not yes and (json_output or not sys.stdin.isatty()):
        if json_output:
            print_json({"error": NEEDS_YES})
        else:
            console.print(f"[red]error:[/red] {NEEDS_YES}")
        raise SystemExit(1)

    if not json_output:
        console.print(escape(render_clear_listing(plan)))
    if not yes and not click.confirm(f"Delete {render_size(plan.total_bytes)} of cached files?", default=False):
        console.print("Nothing deleted.")
        return

    result = apply_clear_plan(plan)

    if json_output:
        print_json(get_clear_report(plan, result).model_dump(mode="json"))
    else:
        console.print(f"Deleted {render_size(result.deleted_bytes)} of cached files.")
        if result.failures:
            console.print(
                f"[red]error:[/red] could not delete {render_size(result.failed_bytes)}. "
                "Close other hcli processes and try again. Set HCLI_DEBUG=1 to see the paths."
            )
    if result.failures:
        raise SystemExit(1)
