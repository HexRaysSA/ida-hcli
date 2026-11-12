"""``hcli packs lumina sync`` — push every Lumina pack the server does not have in its stable revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import rich_click as click
from rich import box
from rich.table import Table
from rich.text import Text

from hcli.commands.packs.common import json_option, packs_errors, short_snapshot, with_repository
from hcli.commands.packs.push import (
    LUMINA,
    PushSettings,
    dry_run_packs,
    push_options,
    push_packs,
    push_settings,
    say,
)
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.resolve import filter_packs
from hcli.lib.packs.state import SYNC_ACTION_TEXT, SyncAction, SyncItem, load_state, plan_sync, state_path

_ACTION_STYLES = {SyncAction.PUSH: "cyan", SyncAction.UPGRADE: "yellow", SyncAction.UP_TO_DATE: "green"}


def _render_plan(plan: list[SyncItem], settings: PushSettings) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1, 0, 0))
    table.add_column("PACK", style="magenta", no_wrap=True)
    table.add_column("SERVER HAS", justify="right", no_wrap=True)
    table.add_column("STABLE", justify="right", no_wrap=True)
    table.add_column("ACTION", no_wrap=True)
    for item in plan:
        has = f"r{item.recorded.revision}" if item.recorded else "-"
        table.add_row(
            item.pack.address,
            has,
            f"r{item.pack.revision}",
            Text(SYNC_ACTION_TEXT[item.action], style=_ACTION_STYLES[item.action]),
        )
    console.print(table)
    note = (
        f"SERVER HAS is what this machine recorded pushing to {settings.server.host}; "
        "Lumina servers cannot report pack revisions."
    )
    say(settings, (note, "dim"))


def _plan_json(item: SyncItem) -> dict[str, Any]:
    return {
        "address": item.pack.address,
        "stable_revision": item.pack.revision,
        "stable_sha256": item.pack.sha256,
        "server_has": item.recorded.revision if item.recorded else None,
        "server_has_sha256": item.recorded.sha256 if item.recorded else None,
        "pushed_at": item.recorded.pushed_at if item.recorded else None,
        "action": item.action.value,
    }


@auth_command()
@click.option("--component", default=None, help="Only packs of this component (e.g. msvc).")
@click.option("--arch", default=None, help="Only packs for this architecture (e.g. arm64).")
@push_options
@json_option
@packs_errors
@async_command
@with_repository
async def sync(
    repo: PackRepository,
    component: str | None,
    arch: str | None,
    server_url: str | None,
    keep: bool,
    dry_run: bool,
    api_key_file: Path | None,
    ca_cert: Path | None,
    as_json: bool,
) -> None:
    """Push the stable Lumina packs a server is missing, or has in an older revision.

    For each Lumina pack in the stable Release (filtered by --component and
    --arch) the plan shows SERVER HAS, STABLE and ACTION (push, push
    (upgrade) or up to date), then pushes everything not up to date, as
    ``push`` does.

    SERVER HAS is what this machine recorded pushing to that server (a
    Lumina server cannot report which pack revisions it holds); a pack
    pushed from another machine shows as ``-`` and is pushed again, which
    is harmless: pushes are idempotent.
    """
    settings = push_settings(server_url, api_key_file, ca_cert, keep, dry_run, as_json)
    release = await repo.read_release(LUMINA)
    packs = filter_packs(release.packs, LUMINA, component=component, arch=arch)
    plan = plan_sync(packs, load_state(settings.server))
    todo = [item.pack for item in plan if item.action is not SyncAction.UP_TO_DATE]
    result: dict[str, Any] = {
        "server": settings.server.url,
        "snapshot": release.snapshot,
        "dry_run": dry_run,
        "state_path": str(state_path(settings.server)),
        "plan": [_plan_json(item) for item in plan],
    }

    if not as_json:
        if not plan:
            console.print("No packs match.", style="dim")
        else:
            _render_plan(plan, settings)

    if not todo:
        if plan:
            say(
                settings,
                ("✓ ", "green"),
                f"{settings.server.host} is up to date with snapshot {short_snapshot(release.snapshot)}",
            )
        if as_json:
            print_json({**result, "packs": [], "total": None})
        return

    if not as_json:
        console.print()
    if dry_run:
        described = dry_run_packs(todo, settings)
        if as_json:
            print_json({**result, **described})
        return

    outcome = await push_packs(repo, release, todo, settings)
    if as_json:
        print_json({**result, **outcome.to_json()})
