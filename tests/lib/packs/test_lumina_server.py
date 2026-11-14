"""The in-process import against a real Lumina server.

Skipped unless LUMINA_SERVER_BIN names a Lumina server binary. Some checks read
the server's SQLite database and need ``sqlite3`` on PATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pytest
from conftest import FIXTURE_PACK, FIXTURE_PRODUCER, FIXTURE_RETAINED_ROWS, lumina_pack
from pyarrow import ipc

from hcli.lib.packs.errors import IntegrityError, ServerRefusedError
from hcli.lib.packs.lumina.arrow import KV_PREFIX
from hcli.lib.packs.lumina.importer import ImportReport, import_pack
from hcli.lib.packs.lumina.pack import Digest
from hcli.lib.packs.server import ApiKey, parse_server

PACK = FIXTURE_PACK
PRODUCER = FIXTURE_PRODUCER
R2_PRODUCER = "1" * 64
RENAMING_PRODUCER = "2" * 64
RETAINED_ROWS = FIXTURE_RETAINED_ROWS
UNKNOWN_MD5 = "00112233445566778899aabbccddeeff"
ADMIN = ("ops", "ops-password-123")
IMPORTER = ("importer", "importer-password-123")

SERVER_BIN = os.environ.get("LUMINA_SERVER_BIN")
pytestmark = pytest.mark.skipif(not SERVER_BIN, reason="LUMINA_SERVER_BIN is not set (a pinned Lumina server build)")
needs_sqlite3 = pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 not on PATH")


class LuminaServer:
    """A throwaway Lumina server with a SQLite database, as the pack builder's tests start it."""

    def __init__(self, extra_env: dict[str, str] | None = None) -> None:
        assert SERVER_BIN is not None
        self.bin = SERVER_BIN
        self.work = Path(tempfile.mkdtemp(prefix="lumina-"))
        (self.work / "config").mkdir()
        (self.work / "db").mkdir()
        (self.work / "config/config.yaml").write_text(f"sqlite3:\n  path: {self.work / 'db/lumina.db'}\n")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.env = {
            **os.environ,
            "LS_WORK_DIR": str(self.work),
            "LS_HOST": "127.0.0.1",
            "LS_PORT": str(port),
            "LS_RPC_ENABLED": "false",
            "LS_RPC_TLS": "false",  # required even with RPC disabled
            **(extra_env or {}),
        }
        self.base = f"http://127.0.0.1:{port}"
        self._log = (self.work / "server.log").open("wb")
        self.process = subprocess.Popen(
            [self.bin, "start"],
            env=self.env,
            cwd=self.work,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=self._log,
        )
        deadline = time.monotonic() + 60
        while True:
            assert self.process.poll() is None, (self.work / "server.log").read_text()[-2000:]
            try:
                ready = httpx.get(f"{self.base}/api/health/ready", timeout=1)
                if ready.is_success and ready.json().get("ready") is True:
                    break
            except (httpx.HTTPError, ValueError):
                pass
            assert time.monotonic() < deadline, "Lumina server not ready"
            time.sleep(0.05)
        self.key = self._mint_key()

    def _account(self, user: tuple[str, str], admin: bool) -> None:
        command = [self.bin, "admin", "create", "--username", user[0], "--password", user[1]]
        subprocess.run(
            [*command, *([] if admin else ["--no-admin"])], env=self.env, cwd=self.work, check=True, capture_output=True
        )

    def _mint_key(self) -> str:
        """An importer key owned by a non-admin account, minted through the admin console."""
        self._account(ADMIN, True)
        self._account(IMPORTER, False)
        with httpx.Client(base_url=self.base, follow_redirects=False) as http:
            login = http.post("/admin/login", data={"user_name": ADMIN[0], "password": ADMIN[1], "next": "/admin"})
            assert login.status_code == 302
            session = next(
                v.split(";")[0].removeprefix("hr_admin_session=")
                for v in login.headers.get_list("set-cookie")
                if v.startswith("hr_admin_session=")
            )
            cookie = {"Cookie": f"hr_admin_session={session}"}
            users = http.get("/admin/users", headers=cookie).raise_for_status().text
            row = users.index(f">{IMPORTER[0]}</span>")
            match = re.match(r"\d+", users[row:].split("api-keys-btn-", 1)[1])
            assert match is not None
            page = http.post(f"/admin/api-keys/modal?user={match.group(0)}", headers=cookie, data={"name": "hcli-test"})
            key = max(re.findall(r"hrlu_[A-Za-z0-9_]+", page.raise_for_status().text), key=len)
        assert self.lookup([UNKNOWN_MD5], key)[0]["code"] == "NOT_FOUND"
        return key

    def lookup(self, md5s: list[str], key: str | None = None) -> list[dict[str, Any]]:
        response = httpx.post(
            f"{self.base}/api/metadata/lookup",
            headers={"Authorization": f"Bearer {key or self.key}"},
            json={"patterns": md5s},
            timeout=30,
        )
        return response.raise_for_status().json()["results"]

    def pushes(self) -> int:
        db = self.work / "db/lumina.db"
        out = subprocess.run(
            ["sqlite3", "-readonly", str(db), "SELECT count(*) FROM pushes;"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(out.stdout)

    def run(self, pack: Path = PACK, expected: Digest | None = None, **kwargs: Any) -> ImportReport:
        """Import ``pack``, checked against ``expected`` (default: its own bytes, as a matching Release lists them)."""
        data = pack.read_bytes()
        expected = expected or Digest(hashlib.sha256(data).hexdigest(), len(data))
        return import_pack(
            pack, expected=expected, server=parse_server(self.base), api_key=ApiKey(value=self.key), **kwargs
        )

    def stop(self) -> None:
        self.process.terminate()
        self.process.wait(10)
        self._log.close()
        shutil.rmtree(self.work, ignore_errors=True)


@pytest.fixture
def start() -> Generator[Callable[..., LuminaServer], None, None]:
    servers: list[LuminaServer] = []

    def make(extra_env: dict[str, str] | None = None) -> LuminaServer:
        servers.append(LuminaServer(extra_env))
        return servers[-1]

    yield make
    for server in servers:
        server.stop()


# -- fixture rows --------------------------------------------------------------------------


def fixture_member() -> bytes:
    """The fixture pack's one member, read with pyarrow's reference reader."""
    import io
    import tarfile

    import zstandard

    with tarfile.open(
        fileobj=io.BytesIO(zstandard.ZstdDecompressor().stream_reader(PACK.open("rb")).read()), mode="r:"
    ) as tar:
        member = tar.extractfile(f"arrows/{PRODUCER}.arrow")
        assert member is not None
        return member.read()


def rows(member: bytes) -> list[dict[str, Any]]:
    table = ipc.open_file(pa.BufferReader(member)).read_all()
    return [{**row, "md5": row["md5"].hex()} for row in table.to_pylist()]


def md5s(member_rows: list[dict[str, Any]]) -> list[str]:
    return [row["md5"] for row in member_rows]


def assert_present(server: LuminaServer, member_rows: list[dict[str, Any]], present: bool = True) -> None:
    codes = {result["code"] for result in server.lookup(md5s(member_rows))}
    assert codes == ({"OK"} if present else {"NOT_FOUND"}), codes


def counts(report: ImportReport, producer: str | None = None) -> tuple[int, int, int, int]:
    members = [m for m in report.members if producer is None or m.sha256 == producer]
    return (
        sum(m.pushed for m in members),
        sum(m.added for m in members),
        sum(m.ok for m in members),
        sum(m.other for m in members),
    )


def derive_member(member: bytes, producer: str, edit: Callable[[bytearray, list[str], int], bool]) -> bytes:
    """``member`` as if exported from ``producer``: its input key-values repointed, rows edited or dropped."""
    reader = ipc.open_file(pa.BufferReader(member))
    table = reader.read_all()
    metadata = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    assert metadata[f"{KV_PREFIX}input_sha256"] == PRODUCER
    metadata.update(
        {
            f"{KV_PREFIX}input_sha256": producer,
            f"{KV_PREFIX}input_md5": producer[:32],
            f"{KV_PREFIX}input_name": f"{metadata[KV_PREFIX + 'input_name']}.{producer[:8]}",
        }
    )
    columns = table.to_pydict()
    keep = []
    for i in range(table.num_rows):
        md5 = bytearray(columns["md5"][i])
        names = [columns["name"][i]]
        keep.append(edit(md5, names, i))
        columns["md5"][i], columns["name"][i] = bytes(md5), names[0]
    schema = table.schema.with_metadata(metadata)
    filtered = pa.table(columns, schema=schema).filter(pa.array(keep))
    sink = pa.BufferOutputStream()
    with ipc.new_file(sink, schema) as writer:
        writer.write_table(filtered)
    return sink.getvalue().to_pybytes()


def write_r2(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    """Revision 2 of the fixture coordinate: these members, in this archive order."""
    pack = tmp_path / "windows-msvc-x86_64-r2.tar.zst"
    pack.write_bytes(lumina_pack(members))
    return pack


# -- the tests ------------------------------------------------------------------------------


def test_import_adds_every_row_and_is_idempotent(start: Callable[..., LuminaServer]) -> None:
    server = start()
    member_rows = rows(fixture_member())
    assert_present(server, member_rows, present=False)
    first = server.run()
    assert counts(first) == (RETAINED_ROWS, RETAINED_ROWS, 0, 0)
    assert_present(server, member_rows)
    assert server.lookup([UNKNOWN_MD5])[0]["code"] == "NOT_FOUND"
    again = server.run()
    assert counts(again) == (RETAINED_ROWS, 0, RETAINED_ROWS, 0)
    assert_present(server, member_rows)


@needs_sqlite3
@pytest.mark.parametrize(
    "expected",
    [lambda d: Digest("0" * 64, d.size), lambda d: Digest(d.sha256, d.size + 1)],
    ids=["checksum", "size"],
)
def test_a_pack_that_does_not_match_its_release_pushes_nothing(
    start: Callable[..., LuminaServer], expected: Callable[[Digest], Digest]
) -> None:
    server = start()
    data = PACK.read_bytes()
    with pytest.raises(IntegrityError, match="does not match the Release"):
        server.run(expected=expected(Digest(hashlib.sha256(data).hexdigest(), len(data))))
    assert_present(server, rows(fixture_member()), present=False)
    assert server.pushes() == 0


@needs_sqlite3
def test_a_small_server_body_cap_is_split_on_413(start: Callable[..., LuminaServer]) -> None:
    server = start({"LS_MAX_PUSH_BODY_BYTES": "16384"})
    report = server.run(chunk_bytes=1_000_000)
    assert counts(report) == (RETAINED_ROWS, RETAINED_ROWS, 0, 0)
    assert_present(server, rows(fixture_member()))
    assert server.pushes() >= 2


def test_a_server_cap_below_one_function_is_a_refusal(start: Callable[..., LuminaServer]) -> None:
    cap = 512
    member_rows = rows(fixture_member())

    def func_json(row: dict[str, Any]) -> int:
        import base64

        func = {
            "pattern": row["md5"],
            "name": row["name"],
            "size": row["size"],
            "metadata": base64.b64encode(row["metadata"] or b"").decode(),
            "ea": "0",
        }
        return len(json.dumps(func, separators=(",", ":")))

    largest = max(member_rows, key=func_json)
    assert func_json(largest) > cap
    server = start({"LS_MAX_PUSH_BODY_BYTES": str(cap)})
    with pytest.raises(ServerRefusedError, match="single-function batch rejected with HTTP 413"):
        server.run()
    assert server.lookup([largest["md5"]])[0]["code"] == "NOT_FOUND"


def test_a_newer_revision_over_an_older_adds_only_new_rows(start: Callable[..., LuminaServer], tmp_path: Path) -> None:
    r1_member = fixture_member()
    r1_rows = rows(r1_member)

    def invert(md5: bytearray, _names: list[str], _i: int) -> bool:
        md5[:] = bytes(b ^ 0xFF for b in md5)
        return True

    new_member = derive_member(r1_member, R2_PRODUCER, invert)
    new_rows = rows(new_member)
    assert len(new_rows) == RETAINED_ROWS and not set(md5s(new_rows)) & set(md5s(r1_rows))
    server = start()
    assert counts(server.run()) == (RETAINED_ROWS, RETAINED_ROWS, 0, 0)
    assert_present(server, new_rows, present=False)

    pack = write_r2(tmp_path, [(PRODUCER, r1_member), (R2_PRODUCER, new_member)])
    r2 = server.run(pack)
    assert counts(r2, PRODUCER) == (RETAINED_ROWS, 0, RETAINED_ROWS, 0)
    assert counts(r2, R2_PRODUCER) == (RETAINED_ROWS, RETAINED_ROWS, 0, 0)
    assert (r2.total.members, r2.total.rows, r2.total.added, r2.total.ok) == (
        2,
        2 * RETAINED_ROWS,
        RETAINED_ROWS,
        RETAINED_ROWS,
    )
    assert_present(server, r1_rows)
    assert_present(server, new_rows)
    again = server.run(pack)
    assert counts(again, R2_PRODUCER) == (RETAINED_ROWS, 0, RETAINED_ROWS, 0)


def test_a_newer_revision_renaming_a_stored_md5_overwrites_it_in_archive_order(
    start: Callable[..., LuminaServer], tmp_path: Path
) -> None:
    # The server keeps one function per MD5, last writer wins: the member pushed last decides the name.
    r1_member = fixture_member()
    r1_rows = rows(r1_member)
    target = r1_rows[0]
    renamed = f"{target['name']}_v2"

    def rename_target(md5: bytearray, names: list[str], _i: int) -> bool:
        if md5.hex() != target["md5"]:
            return False
        names[0] = renamed
        return True

    renaming = derive_member(r1_member, RENAMING_PRODUCER, rename_target)
    assert len(rows(renaming)) == 1
    server = start()

    def name_on_server() -> str:
        (result,) = server.lookup([target["md5"]])
        assert result["code"] == "OK" and result["size"] == target["size"]
        return result["name"]

    server.run()
    assert name_on_server() == target["name"]
    pack = write_r2(tmp_path, [(PRODUCER, r1_member), (RENAMING_PRODUCER, renaming)])
    for _ in range(2):
        r2 = server.run(pack)
        assert counts(r2, RENAMING_PRODUCER) == (1, 0, 1, 0)  # OK, not ADDED: the counts do not show the rename
        assert name_on_server() == renamed
    server.run()
    assert name_on_server() == target["name"]
    reversed_pack = write_r2(tmp_path, [(RENAMING_PRODUCER, renaming), (PRODUCER, r1_member)])
    server.run(reversed_pack)
    assert name_on_server() == target["name"]
    assert_present(server, r1_rows)
