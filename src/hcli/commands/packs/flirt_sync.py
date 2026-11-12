"""``hcli packs flirt sync`` — install every FLIRT pack the signature folder lacks in its stable revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import rich_click as click
from rich import box
from rich.table import Table
from rich.text import Text

from hcli.commands.packs.common import json_option, packs_errors, short_snapshot, with_repository
from hcli.commands.packs.install import (
    FLIRT,
    dry_run_installs,
    install_options,
    install_packs,
    requires_sig_dir,
    say,
    say_notes,
)
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json
from hcli.lib.packs.install import ACTION_TEXT, InstallAction, InstallItem, plan_install
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.resolve import filter_packs

_ACTION_STYLES = {
    InstallAction.INSTALL: "cyan",
    InstallAction.UPGRADE: "yellow",
    InstallAction.DOWNGRADE: "yellow",
    InstallAction.UP_TO_DATE: "green",
    InstallAction.UNSUPPORTED: "dim",
}
# Nothing to do for these.
_SETTLED = frozenset({InstallAction.UP_TO_DATE, InstallAction.UNSUPPORTED})


def _render_plan(plan: list[InstallItem]) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1, 0, 0))
    table.add_column("PACK", style="magenta", no_wrap=True)
    table.add_column("INSTALLED", justify="right", no_wrap=True)
    table.add_column("STABLE", justify="right", no_wrap=True)
    table.add_column("ACTION", no_wrap=True)
    for item in plan:
        manifest = item.installed.manifest
        table.add_row(
            item.pack.address,
            f"r{manifest.revision}" if manifest else "-",
            f"r{item.pack.revision}",
            Text(ACTION_TEXT[item.action], style=_ACTION_STYLES[item.action]),
        )
    console.print(table)


def _plan_json(item: InstallItem) -> dict[str, Any]:
    manifest = item.installed.manifest
    return {
        "address": item.pack.address,
        "stable_revision": item.pack.revision,
        "stable_sha256": item.pack.sha256,
        "installed": manifest.revision if manifest else None,
        "installed_sha256": manifest.sha256 if manifest else None,
        "installed_at": manifest.installed_at if manifest else None,
        "path": str(item.target) if item.target else None,
        "note": item.installed.problem,
        "action": item.action.value,
    }


@auth_command()
@click.option("--component", default=None, help="Only packs of this component (e.g. msvc).")
@click.option("--arch", default=None, help="Only packs for this architecture (e.g. arm64).")
@install_options
@json_option
@packs_errors
@async_command
@requires_sig_dir
@with_repository
async def sync(
    repo: PackRepository,
    component: str | None,
    arch: str | None,
    sig_dir: Path,
    keep: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Install the stable FLIRT packs a signature folder is missing, or has in an older build.

    For each FLIRT pack in the stable Release (filtered by --component and
    --arch) the plan shows INSTALLED, STABLE and ACTION (install, install
    (upgrade), install (downgrade), up to date, or unsupported for an arch
    hcli cannot install, which is skipped), then installs everything else
    not up to date, as ``install`` does.

    INSTALLED comes from each pack folder's ``.hcli-pack.json`` manifest
    under --sig-dir (default $IDAUSR/sig); a folder without a usable
    manifest, or missing signatures, counts as not installed and is
    installed again.
    """
    root = sig_dir
    release = await repo.read_release(FLIRT)
    packs = filter_packs(release.packs, FLIRT, component=component, arch=arch)
    plan = plan_install(packs, root)
    todo = [item for item in plan if item.action not in _SETTLED]
    result: dict[str, Any] = {
        "sig_dir": str(root),
        "snapshot": release.snapshot,
        "dry_run": dry_run,
        "plan": [_plan_json(item) for item in plan],
    }

    if not as_json:
        if not plan:
            console.print("No packs match.", style="dim")
        else:
            _render_plan(plan)
    say_notes(plan, as_json)

    if not todo:
        if plan:
            say(as_json, ("✓ ", "green"), f"{root} is up to date with snapshot {short_snapshot(release.snapshot)}")
        if as_json:
            print_json({**result, "packs": []})
        return

    if not as_json:
        console.print()
    if dry_run:
        entries = dry_run_installs(todo, force=False, keep=keep, as_json=as_json)
        if as_json:
            print_json({**result, "packs": entries})
        return

    results = await install_packs(repo, release, root, todo, force=False, keep=keep, as_json=as_json)
    if as_json:
        print_json({**result, "packs": [r.model_dump(mode="json") for r in results]})
