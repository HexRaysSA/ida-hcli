from __future__ import annotations

import asyncio
import importlib
import json
import os
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from click.testing import CliRunner
from conftest import (
    LUMINA_KV,
    FakeBackend,
    lumina_arrow,
    lumina_pack,
    lumina_rows,
    make_meta,
    make_pack,
    make_release,
    pack_key,
    use_backend,
)

from hcli.commands.packs import packs
from hcli.commands.packs.acquire import link_or_copy, obtain_pack
from hcli.commands.packs.push import PushSettings
from hcli.lib.packs.errors import (
    InvalidServerError,
    InvalidStateError,
    LocalIOError,
    MissingApiKeyError,
    PacksError,
)
from hcli.lib.packs.lumina.arrow import KV_PREFIX
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository
from hcli.lib.packs.server import (
    API_KEY_ENV,
    CA_CERT_ENV,
    ApiKey,
    _request_error,
    _verify,
    parse_server,
    resolve_api_key,
)
from hcli.lib.packs.state import (
    PushRecord,
    ServerState,
    SyncAction,
    load_state,
    plan_sync,
    record_push,
    state_path,
)
from hcli.lib.packs.store import cached_pack_path

KEY = "lumina-test-key-0123456789abcdef"
SERVER = "http://127.0.0.1:18080"

# the API key


def test_api_key_resolution_and_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_KEY_ENV, "  ")
    with pytest.raises(MissingApiKeyError) as info:
        resolve_api_key(None)
    assert info.value.kind == "missing_api_key"
    assert info.value.hint is not None and info.value.hint.startswith(f"export {API_KEY_ENV}=")
    monkeypatch.setenv(API_KEY_ENV, KEY)
    key = resolve_api_key(None)
    assert key.value == KEY
    assert KEY not in repr(key)
    # The file wins.
    assert resolve_api_key(tmp_path / "k").path == tmp_path / "k"


# server URL and state


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:18080", "http://127.0.0.1:18080"),
        ("HTTP://LOCALHOST:18080/", "http://localhost:18080"),
        ("https://lumina.example.com:443/base/", "https://lumina.example.com/base"),
        ("http://example.com:80", "http://example.com"),
        ("https://[::1]:8443", "https://[::1]:8443"),
    ],
)
def test_server_url_normalization(value: str, expected: str) -> None:
    assert parse_server(value).url == expected


@pytest.mark.parametrize(
    "value", ["ftp://x", "http://", "https://user:pw@host", "https://host/?q=1", "https://host:notaport"]
)
def test_server_url_rejects_unusable_values(value: str) -> None:
    with pytest.raises(InvalidServerError):
        parse_server(value)


def test_state_path_is_keyed_by_the_normalized_url(isolated_push_env: Path) -> None:
    path = state_path(parse_server("HTTP://127.0.0.1:18080/"))
    assert path == state_path(parse_server("http://127.0.0.1:18080"))
    assert path.parent == isolated_push_env / "lumina-servers"
    assert path.name.startswith("127-0-0-1-18080-")
    assert path != state_path(parse_server("http://127.0.0.1:18081"))


def _record(address: str = "msvc/windows-all-x86", sha256: str = "a" * 64, revision: int = 2) -> PushRecord:
    return PushRecord(address=address, revision=revision, sha256=sha256, snapshot="s", pushed_at="2026-11-02T10:00:00Z")


def test_state_round_trip_and_replace(isolated_push_env: Path) -> None:
    server = parse_server(SERVER)
    assert load_state(server).packs == {}
    record_push(server, _record())
    record_push(server, _record("msvc/windows-all-arm64"))
    path = record_push(server, _record(revision=3, sha256="b" * 64))
    state = load_state(server)
    assert state.server == SERVER
    assert state.packs["msvc/windows-all-x86"].revision == 3
    assert set(state.packs) == {"msvc/windows-all-x86", "msvc/windows-all-arm64"}
    assert [entry.name for entry in path.parent.iterdir()] == [path.name]  # no temp files left


def test_state_write_is_atomic(isolated_push_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = parse_server(SERVER)
    path = record_push(server, _record())
    before = path.read_bytes()

    def fail(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("hcli.lib.packs.store.os.replace", fail)
    with pytest.raises(PacksError) as info:
        record_push(server, _record(revision=9))
    assert info.value.kind == "local_io"
    assert path.read_bytes() == before
    assert [entry.name for entry in path.parent.iterdir()] == [path.name]


def test_corrupt_state_is_reported(isolated_push_env: Path) -> None:
    server = parse_server(SERVER)
    path = state_path(server)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InvalidStateError) as info:
        load_state(server)
    assert info.value.hint is not None and str(path) in info.value.hint


def test_plan_sync() -> None:
    fresh, same, rebuilt = (
        ReleasePack.model_validate(make_pack(arch, arch.encode(), b"m")) for arch in ("arm64", "x86", "x86_64")
    )
    state = ServerState(
        server=SERVER,
        packs={
            same.address: _record(same.address, same.sha256),
            rebuilt.address: _record(rebuilt.address, "c" * 64, revision=1),
        },
    )
    plan = plan_sync([fresh, same, rebuilt], state)
    assert [(item.pack.address, item.action) for item in plan] == [
        (fresh.address, SyncAction.PUSH),
        (same.address, SyncAction.UP_TO_DATE),
        (rebuilt.address, SyncAction.UPGRADE),
    ]
    assert plan[2].recorded is not None and plan[2].recorded.revision == 1


# staging a pack under its published filename

ARCHES = ("arm64", "x86", "x86_64")
ROWS_PER_PACK = 5


def _member(index: int, arch: str) -> bytes:
    """One Lumina member per pack, its rows distinct across packs, named after its pack."""
    metadata = {**LUMINA_KV, f"{KV_PREFIX}input_name": f"lib-{arch}"}
    return lumina_arrow([lumina_rows(ROWS_PER_PACK, start=100 * index)], metadata=metadata)


PAYLOADS = {arch: lumina_pack([(f"{index + 1:064x}", _member(index, arch))]) for index, arch in enumerate(ARCHES)}
META = make_meta([])


def _release_objects() -> dict[str, bytes]:
    entries = [
        make_pack(arch, payload, META, rows=1000 + index) for index, (arch, payload) in enumerate(PAYLOADS.items())
    ]
    objects = {pack_key(entry): PAYLOADS[entry["arch"]] for entry in entries}
    objects["lumina/release"] = json.dumps(make_release(entries)).encode()
    return objects


def _settings(keep: bool = False) -> PushSettings:
    return PushSettings(parse_server(SERVER), ApiKey(value=KEY), None, keep, as_json=True)


def _pack(arch: str) -> ReleasePack:
    return ReleasePack.model_validate(make_pack(arch, PAYLOADS[arch], META))


@pytest.mark.parametrize(("keep", "source"), [(False, "temp"), (True, "downloaded")])
def test_obtain_pack_downloads_under_the_published_name(
    cache_root: Path, tmp_path: Path, keep: bool, source: str
) -> None:
    repo = PackRepository(FakeBackend(_release_objects()))
    staging = tmp_path / "staging"
    staging.mkdir()
    pack = _pack("x86")
    staged = asyncio.run(obtain_pack(repo, PackFormat.LUMINA, pack, staging, keep=keep, as_json=True))
    assert (staged.source, staged.warning) == (source, None)
    assert staged.path == staging / "windows-all-x86-r2.tar.zst"
    assert staged.path.read_bytes() == PAYLOADS["x86"]
    assert cached_pack_path(pack).is_file() is keep


def test_obtain_pack_stages_a_cached_pack(cache_root: Path, tmp_path: Path) -> None:
    backend = FakeBackend(_release_objects())
    repo = PackRepository(backend)
    pack = _pack("arm64")
    asyncio.run(repo.fetch_pack(PackFormat.LUMINA, pack))
    backend.reads.clear()
    staged = asyncio.run(obtain_pack(repo, PackFormat.LUMINA, pack, tmp_path, keep=False, as_json=True))
    assert (staged.source, staged.path.name, staged.path.read_bytes()) == ("cached", pack.filename, PAYLOADS["arm64"])
    assert backend.reads == []


def test_obtain_pack_replaces_a_damaged_cache_entry(cache_root: Path, tmp_path: Path) -> None:
    repo = PackRepository(FakeBackend(_release_objects()))
    pack = _pack("arm64")
    cached = cached_pack_path(pack)
    cached.write_bytes(b"X" * pack.size)
    staged = asyncio.run(obtain_pack(repo, PackFormat.LUMINA, pack, tmp_path, keep=False, as_json=True))
    # Never staged; replaced in the cache, so later runs and `list` see a good entry.
    assert staged.source == "downloaded"
    assert staged.warning == "cached copy failed verification; downloading it again"
    assert staged.path.read_bytes() == PAYLOADS["arm64"]
    assert cached.read_bytes() == PAYLOADS["arm64"]


def test_obtain_pack_bypasses_an_unreadable_cache_entry(
    cache_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = PackRepository(FakeBackend(_release_objects()))
    pack = _pack("arm64")
    cached = cached_pack_path(pack)
    cached.write_bytes(PAYLOADS["arm64"])

    def unreadable(path: Path, **_kwargs: object) -> None:
        raise LocalIOError("read", path, PermissionError(13, "Permission denied"))

    monkeypatch.setattr("hcli.lib.packs.repository.verify_file", unreadable)
    staged = asyncio.run(obtain_pack(repo, PackFormat.LUMINA, pack, tmp_path, keep=False, as_json=True))
    assert staged.source == "temp"
    assert staged.warning is not None and "could not be read" in staged.warning
    assert staged.path.read_bytes() == PAYLOADS["arm64"]


def test_link_or_copy_falls_back_to_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows across volumes (or FAT): no hardlinks.
    def no_link(*_args: object) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_link)
    source = tmp_path / "source"
    source.write_bytes(b"pack")
    link_or_copy(source, tmp_path / "dest")
    assert (tmp_path / "dest").read_bytes() == b"pack"
    assert (tmp_path / "dest").stat().st_ino != source.stat().st_ino


# CLI, against a fake Lumina server


@dataclass
class FakeLumina:
    """The Lumina server: the read-only key lookup push makes first, then ``/api/metadata`` pushes."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    error: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    ca_certs: list[Path | None] = field(default_factory=list)
    # Pushes: the status to answer for a member's input name (default 200), what was stored.
    push_status: dict[str, int] = field(default_factory=dict)
    interrupt_on: str | None = None
    stored: set[str] = field(default_factory=set)

    @property
    def pushes(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/api/metadata")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        if request.url.path.endswith("/api/metadata"):
            body = json.loads(request.content)
            if body["input"] == self.interrupt_on:
                raise KeyboardInterrupt
            status = self.push_status.get(body["input"], 200)
            if status != 200:
                return httpx.Response(status, json={"error": "scripted"})
            codes = [{"code": "OK" if f["pattern"] in self.stored else "ADDED"} for f in body["funcs"]]
            self.stored.update(f["pattern"] for f in body["funcs"])
            return httpx.Response(200, json={"results": codes})
        assert request.url.path.endswith("/api/metadata/lookup")
        if self.status != 200:
            message = "admin privilege required" if self.status == 403 else "nope"
            return httpx.Response(self.status, headers=self.headers, json={"error": "forbidden", "message": message})
        return httpx.Response(200, json={"results": [{"code": "NOT_FOUND"}]})


@pytest.fixture
def lumina(monkeypatch: pytest.MonkeyPatch) -> FakeLumina:
    """No test reaches a real server (one may be listening on SERVER's port); retries are instant."""
    fake = FakeLumina()

    def client(_transport: object, ca_cert: Path | None = None, timeout: object = None) -> httpx.Client:
        fake.ca_certs.append(ca_cert)
        return httpx.Client(transport=httpx.MockTransport(fake.handle), follow_redirects=False)

    monkeypatch.setattr("hcli.lib.packs.server.lumina_client", client)
    monkeypatch.setattr("hcli.lib.packs.lumina.importer.lumina_client", client)
    # Only the importer's clock: patching time.sleep itself would reach every thread.
    monkeypatch.setattr("hcli.lib.packs.lumina.importer.time", SimpleNamespace(sleep=lambda _seconds: None))
    return fake


@pytest.fixture
def push_repo(cache_root: Path, monkeypatch: pytest.MonkeyPatch, lumina: FakeLumina) -> FakeBackend:
    backend = FakeBackend(_release_objects())
    use_backend(monkeypatch, backend)
    return backend


def _invoke(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(packs, list(args), env=env, catch_exceptions=False)


def test_push_pushes_each_pack_and_records_state(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    result = _invoke(
        "lumina", "push", "msvc/windows-all-x8*", "--server", SERVER + "/", "--json", env={API_KEY_ENV: KEY}
    )
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["server"] == SERVER
    assert "uploader" not in output
    assert [(p["address"], p["source"]) for p in output["packs"]] == [
        ("msvc/windows-all-x86", "temp"),
        ("msvc/windows-all-x86_64", "temp"),
    ]
    assert any("is plain http" in w for w in output["packs"][0]["warnings"])
    assert output["total"]["pushed"] == 2 * ROWS_PER_PACK and output["total"]["packs"] == 2
    assert output["total"]["added"] == 2 * ROWS_PER_PACK

    # One member, one batch per pack, in order, and the key only ever in the header.
    assert [push["input"] for push in lumina.pushes] == ["lib-x86", "lib-x86_64"]
    assert all(len(push["funcs"]) == ROWS_PER_PACK for push in lumina.pushes)
    assert KEY not in result.output

    state = load_state(parse_server(SERVER))
    assert set(state.packs) == {"msvc/windows-all-x86", "msvc/windows-all-x86_64"}
    assert state.packs["msvc/windows-all-x86"].totals.added == ROWS_PER_PACK
    assert not cached_pack_path(_pack("x86")).exists()  # temp download, not kept


def test_sync_pushes_what_is_missing_then_is_up_to_date(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    env = {API_KEY_ENV: KEY, "HCLI_LUMINA_SERVER": SERVER}
    assert _invoke("lumina", "push", "msvc/windows-all-x86", "--keep", env=env).exit_code == 0

    dry = json.loads(_invoke("lumina", "sync", "--dry-run", "--json", env=env).output)
    assert [(item["address"], item["action"]) for item in dry["plan"]] == [
        ("msvc/windows-all-arm64", "push"),
        ("msvc/windows-all-x86", "up_to_date"),
        ("msvc/windows-all-x86_64", "push"),
    ]
    assert [entry["address"] for entry in dry["packs"]] == ["msvc/windows-all-arm64", "msvc/windows-all-x86_64"]
    assert len(lumina.pushes) == 1  # the dry run pushed nothing

    human = _invoke("lumina", "sync", env=env)
    assert human.exit_code == 0, human.output
    assert "SERVER HAS is what this machine recorded pushing to 127.0.0.1:18080" in human.output
    # The table shows labels; --json the slugs.
    assert "up to date" in human.output and "up_to_date" not in human.output
    assert (
        f"✓ msvc/windows-all-arm64 r2: {ROWS_PER_PACK} pushed · {ROWS_PER_PACK} added · 0 already present"
        in human.output
    )
    assert len(lumina.pushes) == 3

    done = json.loads(_invoke("lumina", "sync", "--json", env=env).output)
    assert {item["action"] for item in done["plan"]} == {"up_to_date"}
    assert done["packs"] == []
    assert len(lumina.pushes) == 3


def test_sync_upgrades_a_pack_recorded_with_other_bytes(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    server = parse_server(SERVER)
    pack = _pack("arm64")
    record_push(server, _record(pack.address, "d" * 64, revision=1))
    result = json.loads(
        _invoke("lumina", "sync", "--arch", "arm64", "--json", "--server", SERVER, env={API_KEY_ENV: KEY}).output
    )
    assert [(item["server_has"], item["action"]) for item in result["plan"]] == [(1, "upgrade")]
    assert load_state(server).packs[pack.address].sha256 == pack.sha256


def test_push_maps_a_refusal_and_keeps_earlier_packs(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    lumina.push_status["lib-x86"] = 401
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "server_refused"
    assert error["error"].startswith("Could not push msvc/windows-all-x86 r2: push ")
    assert 'batch rejected with HTTP 401 Unauthorized; body: {"error":"scripted"}' in error["error"]
    assert error["hint"] == f"check that {API_KEY_ENV} (or --api-key-file) is a valid importer key for this server"
    assert error["completed"]["packs"] == []
    assert load_state(parse_server(SERVER)).packs == {}


def test_push_human_output(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, env={API_KEY_ENV: KEY})
    assert result.exit_code == 0, result.output
    assert "• lumina msvc/windows-all-x86 r2: downloaded to temp, sha256 ✓" in result.output
    assert f"↑ pushing 1,001 functions to {SERVER} …" in result.output
    assert f"✓ msvc/windows-all-x86 r2: {ROWS_PER_PACK} pushed · {ROWS_PER_PACK} added" in result.output
    assert KEY not in result.output


def test_push_without_a_key_fails_before_anything_runs(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json")
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "missing_api_key"
    assert error["hint"].startswith("export LUMINA_API_KEY=")
    assert push_repo.reads == []
    assert lumina.requests == []


def test_push_without_a_server(push_repo: FakeBackend) -> None:
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    assert json.loads(result.output)["kind"] == "invalid_server"


def test_dry_run_downloads_and_pushes_nothing(
    push_repo: FakeBackend, lumina: FakeLumina, isolated_push_env: Path
) -> None:
    # No key: a dry run reports it instead of failing.
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER, "--dry-run", "--json")
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["dry_run"] is True
    assert output["api_key"] is None
    assert "uploader" not in output
    assert [entry["source"] for entry in output["packs"]] == ["would_download"] * 3
    assert push_repo.reads == ["lumina/release"]
    assert lumina.requests == []
    assert not isolated_push_env.exists()


def test_a_pack_that_does_not_match_the_release_pushes_nothing(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    # The repository serves other bytes than the Release lists: refused before any push.
    key = next(k for k in push_repo.objects if k.endswith("windows-all-x86/pack"))
    push_repo.objects[key] = PAYLOADS["arm64"]
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    assert json.loads(result.output)["kind"] == "integrity"
    assert lumina.pushes == []


def test_ctrl_c_during_a_push_stops_with_the_packs_already_pushed(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    lumina.interrupt_on = "lib-x86"
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "interrupted"
    assert error["error"] == "Interrupted while pushing msvc/windows-all-x86 r2."
    assert [entry["address"] for entry in error["completed"]["packs"]] == ["msvc/windows-all-arm64"]


# list shows what was pushed


def test_list_shows_pushed_revisions(push_repo: FakeBackend) -> None:
    server = parse_server(SERVER)
    record_push(server, _record("msvc/windows-all-x86", _pack("x86").sha256))
    record_push(server, _record("msvc/windows-all-arm64", "e" * 64, revision=1))
    output = json.loads(_invoke("lumina", "list", "--json", env={"HCLI_LUMINA_SERVER": SERVER}).output)
    assert output["lumina_server"] == SERVER
    pushed = {entry["address"]: entry["pushed"] for entry in output["packs"]}
    assert pushed["msvc/windows-all-x86"]["up_to_date"] is True
    assert pushed["msvc/windows-all-arm64"] == {
        "revision": 1,
        "sha256": "e" * 64,
        "pushed_at": "2026-11-02T10:00:00Z",
        "up_to_date": False,
    }
    assert pushed["msvc/windows-all-x86_64"] is None

    human = _invoke("lumina", "list", env={"HCLI_LUMINA_SERVER": SERVER}).output
    assert "cached ↑r2" not in human  # nothing is cached in this test
    assert "- ↑r2" in human and "- ↑r1" in human and "pushed to 127.0.0.1:18080" in human


def test_list_without_a_server_has_no_pushed_column(push_repo: FakeBackend) -> None:
    output = json.loads(_invoke("lumina", "list", "--json").output)
    assert "lumina_server" not in output
    assert all("pushed" not in entry for entry in output["packs"])
    assert os.environ.get("HCLI_LUMINA_SERVER") is None


# failing early: state, key, URL (before any pack download)


@pytest.mark.parametrize(
    ("content", "message"),
    [("{not json", "is unreadable"), (json.dumps({"schema_version": 99, "server": SERVER}), "schema_version 99")],
)
def test_push_with_bad_state_fails_before_downloading(
    push_repo: FakeBackend, lumina: FakeLumina, monkeypatch: pytest.MonkeyPatch, content: str, message: str
) -> None:
    path = state_path(parse_server(SERVER))
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "invalid_state" and message in error["error"]
    assert "completed" not in error
    assert push_repo.reads == ["lumina/release"]
    assert lumina.requests == []


@pytest.mark.parametrize(
    ("status", "headers", "kind", "hint_part"),
    [
        (401, {}, "access_denied", "(or --api-key-file) is a valid importer key"),
        (403, {}, "access_denied", "valid importer key"),
        (404, {}, "invalid_server", "/api/metadata is appended"),
        (301, {"Location": "https://127.0.0.1:18080/"}, "invalid_server", "redirects are not followed"),
        (503, {}, "transient", "server is up"),
        (400, {}, "server_refused", "next-gen Lumina server"),
    ],
)
def test_push_checks_the_key_before_downloading(
    push_repo: FakeBackend,
    lumina: FakeLumina,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    kind: str,
    hint_part: str,
) -> None:
    lumina.status, lumina.headers = status, headers
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert (error["kind"], f"HTTP {status}" in error["error"]) == (kind, True)
    assert hint_part in error["hint"]
    assert KEY not in result.output
    assert push_repo.reads == ["lumina/release"]
    assert [request.url.path for request in lumina.requests] == ["/api/metadata/lookup"]
    if status == 301:
        assert "redirect to https://127.0.0.1:18080/" in error["error"]


def test_push_key_check_network_failure(
    push_repo: FakeBackend, lumina: FakeLumina, monkeypatch: pytest.MonkeyPatch
) -> None:
    lumina.error = httpx.ConnectError("connection refused")
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    error = json.loads(result.output)
    assert error["kind"] == "network"
    assert "cannot reach 127.0.0.1:18080" in error["error"]
    assert push_repo.reads == ["lumina/release"]


def test_push_with_only_an_api_key_file(push_repo: FakeBackend, lumina: FakeLumina, tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    result = _invoke(
        "lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--api-key-file", str(key_file), "--json"
    )
    assert result.exit_code == 0, result.output
    # FakeLumina checks the Bearer key on the probe and on the push.
    assert [request.url.path for request in lumina.requests] == ["/api/metadata/lookup", "/api/metadata"]
    assert json.loads(result.output)["packs"][0]["address"] == "msvc/windows-all-x86"


def test_ca_cert_env_reaches_the_probe_and_the_push(push_repo: FakeBackend, lumina: FakeLumina, tmp_path: Path) -> None:
    ca = tmp_path / "ca.pem"
    ca.write_text("pem", encoding="utf-8")
    env = {API_KEY_ENV: KEY, "HCLI_LUMINA_CA_CERT": str(ca)}
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json", env=env)
    assert result.exit_code == 0, result.output
    assert lumina.ca_certs == [ca, ca]


def test_dry_run_sends_no_key_check(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    lumina.status = 401
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER, "--dry-run", "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 0, result.output
    assert lumina.requests == []


# failing (or not) after a successful push


def test_state_write_failure_after_a_push_warns_and_continues(
    push_repo: FakeBackend, lumina: FakeLumina, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unwritable(_server: object, record: PushRecord, _root: object = None) -> Path:
        raise LocalIOError("write", tmp_path / "state.json", PermissionError(13, "Permission denied"))

    monkeypatch.setattr(importlib.import_module("hcli.commands.packs.push"), "record_push", unwritable)
    result = _invoke("lumina", "push", "msvc/windows-all-x8*", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert [entry["recorded"] for entry in output["packs"]] == [False, False]
    assert len(lumina.pushes) == 2  # the second pack was still pushed
    warning = output["packs"][0]["warnings"][-1]
    assert warning.startswith("pushed, but not recorded for sync") and "Permission denied" in warning

    human = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, env={API_KEY_ENV: KEY})
    assert human.exit_code == 0, human.output
    assert f"✓ msvc/windows-all-x86 r2: {ROWS_PER_PACK} pushed" in human.output
    assert "! msvc/windows-all-x86 r2: pushed, but not recorded for sync" in human.output


@pytest.mark.parametrize("command", ["push", "sync"])
def test_failure_mid_run_reports_the_packs_already_pushed(
    push_repo: FakeBackend, lumina: FakeLumina, command: str
) -> None:
    lumina.push_status["lib-x86_64"] = 503  # persists through the retries: transient
    args = ["lumina", "push", "msvc/*"] if command == "push" else ["lumina", "sync"]
    result = _invoke(*args, "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "transient"
    assert "batch exhausted retries after HTTP 503 Service Unavailable" in error["error"]
    completed = error["completed"]
    assert [entry["address"] for entry in completed["packs"]] == ["msvc/windows-all-arm64", "msvc/windows-all-x86"]
    assert completed["total"]["pushed"] == 2 * ROWS_PER_PACK
    assert set(load_state(parse_server(SERVER)).packs) == {"msvc/windows-all-arm64", "msvc/windows-all-x86"}

    human = _invoke(*args, "--server", SERVER, env={API_KEY_ENV: KEY})
    assert human.exit_code == 1
    if command == "push":
        assert f"✓ msvc/windows-all-arm64 r2: {ROWS_PER_PACK} pushed" in human.output
    assert "✗ Could not push msvc/windows-all-x86_64 r2: push " in human.output


# dry run: no side effects


def test_dry_run_creates_no_cache_folders(
    push_repo: FakeBackend, monkeypatch: pytest.MonkeyPatch, cache_root: Path, isolated_push_env: Path
) -> None:
    for args in (["lumina", "push", "msvc/*"], ["lumina", "sync"]):
        result = _invoke(*args, "--server", SERVER, "--dry-run", "--json")
        assert result.exit_code == 0, result.output
    assert not cache_root.parent.exists()  # no <cache>/packs
    assert not isolated_push_env.exists()


def test_dry_run_cached_uses_the_size_check(push_repo: FakeBackend, cache_root: Path) -> None:
    right, truncated = _pack("x86"), _pack("arm64")
    cache_root.mkdir(parents=True)
    cached_pack_path(right).write_bytes(b"?" * right.size)  # not hashed: the size is right
    cached_pack_path(truncated).write_bytes(b"short")
    output = json.loads(_invoke("lumina", "push", "msvc/*", "--server", SERVER, "--dry-run", "--json").output)
    sources = {entry["address"]: entry["source"] for entry in output["packs"]}
    assert sources == {
        "msvc/windows-all-arm64": "would_download",
        "msvc/windows-all-x86": "cached",
        "msvc/windows-all-x86_64": "would_download",
    }


# sync filters


@pytest.mark.parametrize(
    ("args", "message", "hint"),
    [
        (
            ["--arch", "armm64"],
            "No lumina pack has arch 'armm64' (available: arm64, x86, x86_64)",
            "did you mean arm64?",
        ),
        (["--component", "msvcc"], "No lumina pack has component 'msvcc' (available: msvc)", "did you mean msvc?"),
        (["--component", "zzz"], "has component 'zzz'", "hcli packs lumina list"),
    ],
)
def test_sync_filter_typo_is_an_unknown_pack(push_repo: FakeBackend, args: list[str], message: str, hint: str) -> None:
    result = _invoke("lumina", "sync", *args, "--server", SERVER, "--dry-run", "--json")
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "unknown_pack"
    assert message in error["error"]
    assert hint in error["hint"]


# the server probes' TLS trust


def test_ca_bundle_loads_into_the_probe_context(tmp_path: Path) -> None:
    import certifi

    assert _verify(None) is True
    assert isinstance(_verify(Path(certifi.where())), ssl.SSLContext)
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a certificate", encoding="utf-8")
    with pytest.raises(PacksError) as info:
        _verify(garbage)
    assert info.value.kind == "tls"
    with pytest.raises(PacksError) as info:
        _verify(tmp_path / "missing.pem")
    assert info.value.kind == "local_io"


def test_tls_failure_hints_the_ca_bundle() -> None:
    error = httpx.ConnectError("handshake failed")
    error.__context__ = ssl.SSLCertVerificationError("self-signed certificate")
    mapped = _request_error(error, parse_server("https://lumina.example.com"), "check it")
    assert mapped.kind == "tls"
    assert mapped.hint is not None and "--ca-cert" in mapped.hint and CA_CERT_ENV in mapped.hint


def test_refused_key_on_an_api_path_hints_the_base_url(push_repo: FakeBackend, lumina: FakeLumina) -> None:
    # The server answers unknown /api/metadata/... paths with 403 "admin privilege required".
    lumina.status = 403
    result = _invoke("lumina", "push", "msvc/*", "--server", SERVER + "/api/metadata", "--json", env={API_KEY_ENV: KEY})
    error = json.loads(result.output)
    assert error["error"] == "The Lumina server refused the API key (HTTP 403: admin privilege required)."
    assert error["hint"].startswith("check that --server / HCLI_LUMINA_SERVER (http://127.0.0.1:18080/api/metadata)")
    assert error["hint"].endswith(
        "otherwise check that LUMINA_API_KEY (or --api-key-file) is a valid importer key for this server"
    )
    assert lumina.requests[0].url.path == "/api/metadata/api/metadata/lookup"


def test_a_local_failure_during_the_import_keeps_its_kind(
    push_repo: FakeBackend, lumina: FakeLumina, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Errors are not rebuilt from their class (LocalIOError takes other arguments): context is added in place.
    def full_disk(*_args: object, **_kwargs: object) -> None:
        raise LocalIOError("write", tmp_path / "spill", OSError(28, "No space left on device"))

    monkeypatch.setattr(importlib.import_module("hcli.commands.packs.push"), "import_pack", full_disk)
    result = _invoke("lumina", "push", "msvc/windows-all-x86", "--server", SERVER, "--json", env={API_KEY_ENV: KEY})
    assert result.exit_code == 1
    error = json.loads(result.output)
    assert error["kind"] == "local_io"
    assert error["error"].startswith("Could not push msvc/windows-all-x86 r2: Could not write ")
    assert "No space left on device" in error["error"] and "close any program" in error["hint"]
