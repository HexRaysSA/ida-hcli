"""Shared fixtures for the packs unit tests (pure logic, no network)."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

# Plain output whatever the environment: Rich and rich_click otherwise style
# it when FORCE_COLOR, PY_COLORS or GITHUB_ACTIONS is set, and the consoles
# read this once, when hcli is first imported (below).
os.environ["TERM"] = "dumb"

import pytest
import zstandard

from hcli.env import ENV
from hcli.lib.packs import repository
from hcli.lib.packs.errors import RequestRefused
from hcli.lib.packs.lumina.arrow import KV_PREFIX
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import RepoBackend


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_pack(arch: str, payload: bytes, meta: bytes, *, fmt: str = "lumina", **overrides: Any) -> dict[str, Any]:
    """A Release v1 pack entry for ``msvc/windows-all-<arch>`` over the given bytes."""
    meta_sha = sha256_hex(meta)
    pack = {
        "name": f"msvc-windows-all-{arch}",
        "component": "msvc",
        "platform": "windows",
        "arch": arch,
        "group": "all",
        "revision": 2,
        "path": f"packs/msvc/windows-all-{arch}-r2.tar.zst",
        "sha256": sha256_hex(payload),
        "size": len(payload),
        "size_uncompressed": 10 * len(payload),
        "member_bytes": 9 * len(payload),
        "producer_bytes": 100 * len(payload),
        "members": 3,
        "rows": None if fmt == "flirt" else 42,
        "pack_format": 1,
        "packer_commit": "76cbdaa54ef59116d44f36c3da631d12cbc68a15",
        "built_at": "2026-10-29T09:11:10Z",
        "meta": {
            "path": f"meta/msvc/{meta_sha}.jsonl.zst",
            "sha256": meta_sha,
            "size": len(meta),
            "size_uncompressed": 100,
        },
    }
    pack.update(overrides)
    return pack


def make_release(packs: list[dict[str, Any]], *, fmt: str = "lumina", **overrides: Any) -> dict[str, Any]:
    release = {
        "schema_version": 1,
        "repository": fmt,
        "channel": "stable",
        "snapshot": "7d96eefa-06f6-451e-9e69-e62412c12f4b",
        "previous_snapshot": "242d0b9d-a2f2-4d41-b11e-e488d6358615",
        "published_at": "2026-10-29T09:29:10Z",
        "packs": packs,
    }
    release.update(overrides)
    return release


def make_meta(records: list[dict[str, Any]]) -> bytes:
    text = "".join(json.dumps(record) + "\n" for record in records)
    return zstandard.ZstdCompressor().compress(text.encode())


META_RECORD: dict[str, Any] = {
    "producer": "3b86e6cf59b75ea768affe77e759d9d902c13a512a98fae82d5615804135e2ba",
    "name": "msvc/Microsoft.VC.14.29.16.11.ATL.ARM64.base/Contents/VC/Tools/MSVC/14.29.30133/atlmfc/lib/arm64/atls.lib",
    "path": "arrows/3b86e6cf59b75ea768affe77e759d9d902c13a512a98fae82d5615804135e2ba.arrow",
    "bytes": 164266,
    "producer_bytes": 2835224,
    "rows": {"input": 1125, "eligible": 1057, "duplicates": 0, "conflicts": 12, "retained": 1045},
    "toolset": "14.29",
    "ida_version": "9.5.261026",
    "license": None,
}


def pack_key(entry: dict[str, Any], fmt: str = "lumina") -> str:
    """The portal key of a ``make_pack`` entry's archive."""
    return repository.pack_key(PackFormat(fmt), ReleasePack.model_validate(entry))


def contents_key(entry: dict[str, Any], fmt: str = "lumina") -> str:
    """The portal key of a ``make_pack`` entry's contents sidecar."""
    return repository.contents_key(PackFormat(fmt), ReleasePack.model_validate(entry))


class FakeBackend(RepoBackend):
    """In-memory files by portal key; counts reads.

    ``failures`` maps a method name (``read``) to the exception it raises instead.
    """

    url = "https://portal.test/api/assets/packs"

    def __init__(self, objects: dict[str, bytes], failures: dict[str, BaseException] | None = None) -> None:
        self.objects = objects
        self.failures = failures or {}
        self.reads: list[str] = []
        self.closed = False

    def _maybe_fail(self, method: str) -> None:
        if method in self.failures:
            raise self.failures[method]

    async def aclose(self) -> None:
        self.closed = True

    def _get(self, key: str) -> bytes:
        self.reads.append(key)
        if key not in self.objects:
            raise RequestRefused(404, "portal")  # as PortalBackend reports a key the portal lacks
        return self.objects[key]

    async def read(self, key: str) -> bytes:
        self._maybe_fail("read")
        return self._get(key)

    async def iter_bytes(self, key: str) -> AsyncGenerator[bytes, None]:
        data = self._get(key)
        for start in range(0, len(data), 4):
            yield data[start : start + 4]

    def endpoint(self) -> tuple[str, int]:
        return "portal.test", 443


def use_backend(monkeypatch: pytest.MonkeyPatch, backend: RepoBackend) -> None:
    """Every PackRepository the commands open reads from ``backend``."""
    monkeypatch.setattr("hcli.lib.packs.repository.PortalBackend", lambda: backend)


class LoggedOutAuth:
    """hcli's AuthService with no usable login: none at all, or an expired session."""

    def __init__(self, *, expired: bool = False) -> None:
        self.expired = expired

    def init(self, forced_credentials: str | None = None) -> None:
        pass

    def ensure_initialized(self) -> None:
        pass

    def is_logged_in(self) -> bool:
        return False

    def has_expired_session(self) -> bool:
        return self.expired

    def get_current_credentials(self) -> None:
        return None


def log_out(monkeypatch: pytest.MonkeyPatch, *, expired: bool = False) -> None:
    """hcli has no usable login: AuthCommand refuses the command, doctor reports it."""
    auth = LoggedOutAuth(expired=expired)
    monkeypatch.setattr(ENV, "HCLI_API_KEY", None)
    monkeypatch.setattr("hcli.lib.commands.get_auth_service", lambda: auth)
    # By module: the package's ``doctor`` attribute is the command, not the module.
    monkeypatch.setattr(importlib.import_module("hcli.commands.packs.doctor"), "get_auth_service", lambda: auth)


@pytest.fixture
def cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point hcli's cache at a temp dir; returns the packs cache directory."""
    monkeypatch.setenv("HCLI_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache" / "packs"


@pytest.fixture(autouse=True)
def isolated_push_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test sees the developer's server, key, CA bundle, IDA user folder or cache; push state goes to a temp dir."""
    for name in (
        "HCLI_LUMINA_SERVER",
        "LUMINA_API_KEY",
        "HCLI_LUMINA_CA_CERT",
    ):
        monkeypatch.delenv(name, raising=False)
    # Logged in with an API key: no stored login is used and no session is checked over the network.
    # ENV reads HCLI_API_KEY once, at import, so the attribute is what counts.
    monkeypatch.setattr(ENV, "HCLI_API_KEY", "test-api-key")
    # FLIRT packs install into <IDA user folder>/sig by default: never the developer's.
    monkeypatch.setenv("HCLI_IDAUSR", str(tmp_path / "idausr"))
    # The same folder the cache_root fixture names.
    monkeypatch.setenv("HCLI_CACHE_DIR", str(tmp_path / "cache"))
    state = tmp_path / "state"
    monkeypatch.setenv("HCLI_PACKS_STATE_DIR", str(state))
    return state


# -- Lumina Arrow members (pyarrow is test-only: the reference writer) ------------------------

LUMINA_KV = {
    f"{KV_PREFIX}input_md5": "0123456789abcdef0123456789abcdef",
    f"{KV_PREFIX}input_sha256": "ab" * 32,
    f"{KV_PREFIX}input_name": "rust-std/1.95.0/liballoc.rlib",
    f"{KV_PREFIX}binary_format": "coff",
    f"{KV_PREFIX}arch": "x86_64",
    f"{KV_PREFIX}input_size": "12345",
    f"{KV_PREFIX}created_at": "2026-11-05T15:12:33Z",
}


def lumina_rows(n: int, start: int = 0) -> dict[str, list[Any]]:
    """``n`` distinct, push-eligible rows (sizes from 32, distinct MD5s and names)."""
    return {
        "ea": [0x1000 + 16 * i for i in range(start, start + n)],
        "size": [32 + i for i in range(start, start + n)],
        "name": [f"func_{i}" for i in range(start, start + n)],
        "md5": [i.to_bytes(16, "big") for i in range(start, start + n)],
        "metadata": [bytes([i % 256]) * (i % 7) for i in range(start, start + n)],
    }


def lumina_schema() -> Any:
    """The Lumina Arrow schema (pyarrow), without key-values."""
    import pyarrow as pa

    return pa.schema(
        [
            ("ea", pa.uint64()),
            ("size", pa.uint32()),
            ("name", pa.string()),
            ("md5", pa.binary(16)),
            ("metadata", pa.binary()),
        ]
    )


def lumina_arrow(
    batches: list[dict[str, Any]], metadata: dict[str, str] | None = None, schema: Any = None, options: Any = None
) -> bytes:
    """A Lumina Arrow file (IPC file format) with these batches; ``schema`` and IPC ``options`` override the defaults."""
    import pyarrow as pa
    from pyarrow import ipc

    schema = (lumina_schema() if schema is None else schema).with_metadata(LUMINA_KV if metadata is None else metadata)
    sink = pa.BufferOutputStream()
    with ipc.new_file(sink, schema, options=options) as writer:
        for batch in batches:
            writer.write_batch(pa.record_batch(batch, schema=schema))
    return sink.getvalue().to_pybytes()


def lumina_pack(members: list[tuple[str, bytes]]) -> bytes:
    """A Lumina pack: ``arrows/<producer>.arrow`` members in this order, plain ustar, zstd-compressed."""
    import io
    import tarfile

    import zstandard

    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for producer, data in members:
            info = tarfile.TarInfo(f"arrows/{producer}.arrow")
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return zstandard.ZstdCompressor().compress(out.getvalue())


# -- the Lumina fixtures ---------------------------------------------------------------------

LUMINA_FIXTURES = Path(__file__).parent / "fixtures" / "lumina"
# A real one-member pack (rust-windows-msvc-x86_64 r1), its Release, and its counts (from the pack builder).
FIXTURE_PACK = LUMINA_FIXTURES / "windows-msvc-x86_64-r1.tar.zst"
FIXTURE_RELEASE = (LUMINA_FIXTURES / "Release.json").read_bytes()
FIXTURE_PRODUCER = "8cbdd963fb07341b06bd6a58dcd6f54dafc64af5c3823fe4cdb2702f2ea97c2d"
FIXTURE_RETAINED_ROWS = 86


def producer(n: int) -> str:
    """A member name: a 64-hex producer sha256."""
    return f"{n:064x}"


def write_lumina_pack(
    folder: Path, members: list[tuple[str, bytes]], name: str = "windows-msvc-x86_64-r2.tar.zst"
) -> Path:
    path = folder / name
    path.write_bytes(lumina_pack(members))
    return path
