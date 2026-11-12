"""What this machine has pushed to each Lumina server, and the ``sync`` plan derived from it.

A Lumina server stores functions, not packs: it cannot say which pack
revision it holds. So after every successful push hcli records the pack
(address, revision, sha256, snapshot, totals) in a per-server state file,
and ``sync`` compares those records with the stable Release. The records
describe this machine's pushes only; another machine pushing the same server
keeps its own.

State, not cache (deleting it makes ``sync`` re-push, which is harmless but
slow), so it lives under hcli's config directory::

    <config>/packs/lumina-servers/<host-slug>-<sha256(url)[:16]>.json

``HCLI_PACKS_STATE_DIR`` replaces ``<config>/packs``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final

from platformdirs import user_config_dir
from pydantic import BaseModel, ConfigDict, ValidationError

from hcli.env import ENV
from hcli.lib.packs.errors import InvalidStateError, LocalIOError
from hcli.lib.packs.lumina.importer import ImportTotals
from hcli.lib.packs.models import ReleasePack
from hcli.lib.packs.server import LuminaServer
from hcli.lib.packs.store import write_atomic

STATE_ENV: Final = "HCLI_PACKS_STATE_DIR"
STATE_SCHEMA_VERSION: Final = 1
SERVERS_DIR: Final = "lumina-servers"
_SLUG_MAX: Final = 48


class _StateModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PushRecord(_StateModel):
    """One pack this machine pushed to the server, successfully and in full."""

    address: str
    revision: int
    sha256: str
    snapshot: str
    pushed_at: str
    # The import's totals for this one push.
    totals: ImportTotals = ImportTotals()


class ServerState(_StateModel):
    schema_version: int = STATE_SCHEMA_VERSION
    server: str
    # Keyed by pack address: the latest successful push of each.
    packs: dict[str, PushRecord] = {}


def state_root() -> Path:
    """``HCLI_PACKS_STATE_DIR``, else ``<hcli config dir>/packs`` (hcli's own ``user_config_dir``)."""
    override = ENV.HCLI_PACKS_STATE_DIR
    if override:
        return Path(override).expanduser()
    return Path(user_config_dir("hcli", "hex-rays")) / "packs"


def server_slug(server: LuminaServer) -> str:
    """A readable, filename-safe rendering of host, port and path."""
    readable = server.url.split("://", 1)[1]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", readable).strip("-").lower()
    return slug[:_SLUG_MAX].rstrip("-") or "server"


def state_path(server: LuminaServer, root: Path | None = None) -> Path:
    """The state file of ``server``: a readable slug plus a hash of the normalized URL (the identity)."""
    digest = hashlib.sha256(server.url.encode()).hexdigest()[:16]
    return (root or state_root()) / SERVERS_DIR / f"{server_slug(server)}-{digest}.json"


def load_state(server: LuminaServer, root: Path | None = None) -> ServerState:
    """The recorded pushes to ``server`` (empty when nothing was recorded yet)."""
    path = state_path(server, root)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ServerState(server=server.url)
    except OSError as e:
        raise LocalIOError("read", path, e) from e
    try:
        state = ServerState.model_validate_json(data)
    except ValidationError as e:
        raise InvalidStateError(
            f"The push state {path} is unreadable: {e.error_count()} problem(s).",
            f"delete {path}; `hcli packs lumina sync` then pushes every pack again (harmless, only slower)",
        ) from e
    if state.schema_version != STATE_SCHEMA_VERSION:
        raise InvalidStateError(
            f"The push state {path} has schema_version {state.schema_version}; "
            f"this hcli reads version {STATE_SCHEMA_VERSION}.",
            "upgrade hcli (hcli update)",
        )
    return state


def record_push(server: LuminaServer, record: PushRecord, root: Path | None = None) -> Path:
    """Add ``record`` to the server's state (replacing the pack's previous record), atomically.

    Read-modify-write: two pushes to one server running at the same time
    can lose one record, which only makes a later ``sync`` re-push it.
    """
    state = load_state(server, root)
    state.packs[record.address] = record
    path = state_path(server, root)
    write_atomic(path, (json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode())
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SyncAction(str, Enum):
    """What ``lumina sync`` plans for a pack; the values are the ``--json`` slugs."""

    PUSH = "push"
    UPGRADE = "upgrade"
    UP_TO_DATE = "up_to_date"


# How the plan table names each action.
SYNC_ACTION_TEXT: Final = {
    SyncAction.PUSH: "push",
    SyncAction.UPGRADE: "push (upgrade)",
    SyncAction.UP_TO_DATE: "up to date",
}


@dataclass(frozen=True)
class SyncItem:
    pack: ReleasePack
    recorded: PushRecord | None
    action: SyncAction


def plan_sync(packs: Sequence[ReleasePack], state: ServerState) -> list[SyncItem]:
    """Per pack: never pushed -> push; pushed with other bytes -> upgrade; same sha256 -> up to date.

    The sha256 decides, not the revision: a rebuilt pack keeps its address
    but not its bytes.
    """
    items: list[SyncItem] = []
    for pack in packs:
        recorded = state.packs.get(pack.address)
        if recorded is None:
            action = SyncAction.PUSH
        elif recorded.sha256 == pack.sha256:
            action = SyncAction.UP_TO_DATE
        else:
            action = SyncAction.UPGRADE
        items.append(SyncItem(pack, recorded, action))
    return items
