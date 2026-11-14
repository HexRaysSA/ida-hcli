"""``hcli packs flirt install`` — install FLIRT packs into an IDA signature folder.

Also the install machinery ``flirt sync`` shares. Packs are acquired as
``lumina push`` does (a verified cached copy, else a download to a temp
folder; ``--keep``: into the cache) and installed one at a time, each
atomically (see ``hcli.lib.packs.install``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Final, TypeVar, cast

import rich_click as click
from pydantic import BaseModel
from rich.text import Text

from hcli.commands.packs.acquire import SOURCE_TEXT, PackSource, obtain_pack, temp_dir
from hcli.commands.packs.common import format_mib, json_option, packs_errors, with_repository
from hcli.lib.commands import async_command, auth_command
from hcli.lib.console import console, print_json
from hcli.lib.packs.errors import PacksError
from hcli.lib.packs.install import (
    InstallAction,
    InstallItem,
    install_pack,
    is_supported,
    plan_install,
    proc_dir,
    resolve_sig_dir,
    sig_reference,
)
from hcli.lib.packs.models import PackFormat, Release, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.resolve import is_glob, resolve_packs
from hcli.lib.packs.store import cache_dir, cached_pack_path, is_cached

F = TypeVar("F", bound=Callable[..., Any])

FLIRT = PackFormat.FLIRT


def install_options(f: F) -> F:
    """The options ``install`` and ``sync`` share (``--json`` is added separately, as everywhere)."""
    options = [
        click.option(
            "--sig-dir",
            type=click.Path(file_okay=False, path_type=Path),
            default=None,
            metavar="DIR",
            help="Signature folder to install into, holding <proc>/ folders (default: IDA's user folder, $IDAUSR/sig).",
        ),
        click.option(
            "--keep",
            is_flag=True,
            default=False,
            help="Keep downloaded packs in the local cache (default: download to a temp folder and delete).",
        ),
        click.option(
            "--dry-run", is_flag=True, default=False, help="Show what would be installed; download and write nothing."
        ),
    ]
    for option in reversed(options):
        f = option(f)
    return f


def requires_sig_dir(f: F) -> F:
    """Resolve ``sig_dir`` (``--sig-dir``, else ``$IDAUSR/sig``) so the command gets it as a Path."""

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        kwargs["sig_dir"] = resolve_sig_dir(kwargs.get("sig_dir"))
        return f(*args, **kwargs)

    return cast(F, wrapper)


def say(as_json: bool, *parts: str | tuple[str, str]) -> None:
    """One human output line (nothing with ``--json``)."""
    if not as_json:
        console.print(Text.assemble(*parts), soft_wrap=True)


def say_notes(plan: Sequence[InstallItem], as_json: bool) -> None:
    """A dim note per skipped pack (an unsupported arch) and per folder without a usable install (installed again)."""
    for item in plan:
        if item.action is InstallAction.UNSUPPORTED:
            note = f"note: {item.pack.address}: hcli cannot install arch {item.pack.arch!r}; skipped"
            say(as_json, (note, "dim"))
        elif item.installed.problem:
            say(as_json, (f"note: {item.pack.address}: {item.installed.problem}; installing it again", "dim"))


def reject_named_unsupported(selected: Sequence[ReleasePack], patterns: Sequence[str]) -> None:
    """UnsupportedArchError for a pack of an arch hcli cannot install that was named exactly (not by a glob)."""
    named = {pattern for pattern in patterns if not is_glob(pattern)}
    for pack in selected:
        if pack.address in named and not is_supported(pack):
            proc_dir(pack)  # raises


class InstallStatus(str, Enum):
    """What happened to a pack; the values are the ``--json`` slugs."""

    INSTALLED = "installed"
    UPGRADED = "upgraded"
    DOWNGRADED = "downgraded"  # the previous build had a higher revision
    REINSTALLED = "reinstalled"  # --force over the same build
    ALREADY_INSTALLED = "already_installed"


# How human output names each status.
STATUS_TEXT: Final = {
    InstallStatus.INSTALLED: "installed",
    InstallStatus.UPGRADED: "upgraded",
    InstallStatus.DOWNGRADED: "downgraded",
    InstallStatus.REINSTALLED: "reinstalled",
    InstallStatus.ALREADY_INSTALLED: "already installed",
}


class InstallResult(BaseModel):
    """One pack, installed or found already installed (its ``--json`` entry)."""

    address: str
    revision: int
    sha256: str
    path: str
    status: InstallStatus
    # The build the folder held before (None: nothing usable).
    previous_revision: int | None = None
    # None when nothing was installed.
    source: PackSource | None = None
    sigs: int
    warnings: list[str] = []


def _already_installed(item: InstallItem, target: Path, as_json: bool) -> InstallResult:
    manifest = item.installed.manifest
    say(
        as_json,
        ("✓ ", "green"),
        (item.pack.address, "magenta"),
        f" r{item.pack.revision}: {STATUS_TEXT[InstallStatus.ALREADY_INSTALLED]} in ",
        (str(target), "cyan"),
    )
    return InstallResult(
        address=item.pack.address,
        revision=item.pack.revision,
        sha256=item.pack.sha256,
        path=str(target),
        status=InstallStatus.ALREADY_INSTALLED,
        previous_revision=manifest.revision if manifest else None,
        sigs=manifest.sigs if manifest else item.pack.members,
    )


async def _install_one(
    repo: PackRepository, root: Path, snapshot: str, item: InstallItem, target: Path, keep: bool, as_json: bool
) -> InstallResult:
    pack = item.pack
    previous = item.installed.manifest
    with temp_dir("hcli-install-") as staging:
        staged = await obtain_pack(repo, FLIRT, pack, staging, keep=keep, as_json=as_json)
        say(
            as_json,
            ("• ", "blue"),
            f"{FLIRT.value} ",
            (pack.address, "magenta"),
            f" r{pack.revision}: {SOURCE_TEXT[staged.source]}, sha256 ✓",
        )
        manifest = install_pack(staged.path, pack, snapshot, root)
    if previous is None:
        status = InstallStatus.INSTALLED
    elif previous.sha256 == pack.sha256:
        status = InstallStatus.REINSTALLED
    elif previous.revision > pack.revision:
        status = InstallStatus.DOWNGRADED
    else:
        status = InstallStatus.UPGRADED
    changed = status in (InstallStatus.UPGRADED, InstallStatus.DOWNGRADED)
    was = f" (was r{previous.revision})" if changed and previous else ""
    say(
        as_json,
        ("✓ ", "green"),
        (pack.address, "magenta"),
        f" r{pack.revision}: {manifest.sigs:,} sigs {STATUS_TEXT[status]}{was} in ",
        (str(target), "cyan"),
    )
    return InstallResult(
        address=pack.address,
        revision=pack.revision,
        sha256=pack.sha256,
        path=str(target),
        status=status,
        previous_revision=previous.revision if previous else None,
        source=staged.source,
        sigs=manifest.sigs,
        warnings=[staged.warning] if staged.warning else [],
    )


async def install_packs(
    repo: PackRepository,
    release: Release,
    root: Path,
    plan: Sequence[InstallItem],
    *,
    force: bool,
    keep: bool,
    as_json: bool,
) -> list[InstallResult]:
    """Install each planned pack in turn (up-to-date ones only with ``force``); stops at the first failure.

    Unsupported packs are skipped (``say_notes`` names them). A failure
    after the first pack carries the results so far in ``completed``
    (printed with ``--json``).
    """
    results: list[InstallResult] = []
    first: ReleasePack | None = None
    try:
        for item in plan:
            target = item.target
            if target is None:
                continue
            if item.action is InstallAction.UP_TO_DATE and not force:
                results.append(_already_installed(item, target, as_json))
            else:
                results.append(await _install_one(repo, root, release.snapshot, item, target, keep, as_json))
                if first is None:
                    first = item.pack
    except PacksError as e:
        e.completed = {"packs": [result.model_dump(mode="json") for result in results]}
        raise
    if first is not None:
        reference = sig_reference(first, "<sig>")
        note = f"In IDA, name a signature by its relative path, {reference}, <sig> being its file name without .sig"
        say(as_json, (note, "dim"))
    return results


def dry_run_installs(plan: Sequence[InstallItem], *, force: bool, keep: bool, as_json: bool) -> list[dict[str, Any]]:
    """Describe what an install would do: each pack's source, size and folder. Nothing is downloaded or written.

    Unsupported packs get no entry (``say_notes`` names them).
    """
    root = cache_dir(create=False)
    entries: list[dict[str, Any]] = []
    for item in plan:
        pack = item.pack
        target = item.target
        if target is None:
            continue
        if item.action is InstallAction.UP_TO_DATE and not force:
            say(
                as_json,
                ("✓ ", "green"),
                (pack.address, "magenta"),
                f" r{pack.revision}: {STATUS_TEXT[InstallStatus.ALREADY_INSTALLED]} in ",
                (str(target), "cyan"),
            )
            entries.append(
                {
                    "address": pack.address,
                    "revision": pack.revision,
                    "sha256": pack.sha256,
                    "path": str(target),
                    "action": item.action.value,
                    "source": None,
                }
            )
            continue
        cached = is_cached(cached_pack_path(pack, root), pack.size)
        where = "to the cache" if keep else "to temp"
        state = "cached" if cached else f"would download {format_mib(pack.size)} {where}"
        say(
            as_json,
            ("↓ ", "cyan"),
            "would install ",
            (pack.address, "magenta"),
            f" r{pack.revision} ({pack.members:,} sigs; {state}) into ",
            (str(target), "cyan"),
        )
        entries.append(
            {
                "address": pack.address,
                "revision": pack.revision,
                "sha256": pack.sha256,
                "size": pack.size,
                "path": str(target),
                "action": item.action.value,
                "source": (PackSource.CACHED if cached else PackSource.WOULD_DOWNLOAD).value,
            }
        )
    say(as_json, ("dry run: nothing downloaded or written", "dim"))
    return entries


@auth_command()
@click.argument("packs", nargs=-1, required=True)
@install_options
@click.option("--force", "-f", is_flag=True, default=False, help="Reinstall packs that are already installed.")
@json_option
@packs_errors
@async_command
@requires_sig_dir
@with_repository
async def install(
    repo: PackRepository,
    packs: tuple[str, ...],
    sig_dir: Path,
    keep: bool,
    dry_run: bool,
    force: bool,
    as_json: bool,
) -> None:
    """Install FLIRT packs into an IDA signature folder.

    PACKS are ``component/platform-group-arch`` addresses or globs (e.g.
    ``msvc/windows-all-x86_64``, ``'msvc/*'``) from the stable FLIRT
    Release. Each pack is taken from the local cache when a verified copy is
    there, else downloaded to a temp folder (``--keep``: into the cache),
    then its signatures are installed into
    ``<sig-dir>/<proc>/hcli-packs/<component>/<platform-group-arch>/``
    (``<proc>``: ``pc`` for the x86 arches, ``arm`` for the arm ones) with a
    ``.hcli-pack.json`` manifest naming the installed build. The folder is
    replaced as a whole, so anything else put in it is deleted.

    The signature folder is IDA's user one, ``$IDAUSR/sig``, unless
    --sig-dir names another; IDA then finds each signature by its relative name,
    ``hcli-packs/<component>/<platform-group-arch>/<sig>``. A pack whose
    installed build is the published one is left alone unless --force. A
    pack of an arch hcli cannot install is skipped when a glob selects it,
    and an ``unsupported_arch`` error when named exactly.
    """
    root = sig_dir
    release = await repo.read_release(FLIRT)
    selected = resolve_packs(release.packs, packs, FLIRT)
    reject_named_unsupported(selected, packs)
    plan = plan_install(selected, root)
    unsupported = [item.pack.address for item in plan if item.action is InstallAction.UNSUPPORTED]
    base = {"sig_dir": str(root), "snapshot": release.snapshot, "dry_run": dry_run, "unsupported": unsupported}
    say_notes(plan, as_json)

    if dry_run:
        entries = dry_run_installs(plan, force=force, keep=keep, as_json=as_json)
        if as_json:
            print_json({**base, "packs": entries})
        return

    results = await install_packs(repo, release, root, plan, force=force, keep=keep, as_json=as_json)
    if as_json:
        print_json({**base, "packs": [result.model_dump(mode="json") for result in results]})
