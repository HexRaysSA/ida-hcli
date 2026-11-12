"""The in-process import: verify then push."""

from __future__ import annotations

import hashlib
import json
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import (
    FIXTURE_PACK,
    FIXTURE_PRODUCER,
    FIXTURE_RETAINED_ROWS,
    lumina_arrow,
    lumina_pack,
    lumina_rows,
    producer,
    write_lumina_pack,
)

from hcli.lib.packs.errors import (
    IntegrityError,
    PacksError,
    PushConfigError,
    ServerRefusedError,
    TLSError,
    TransientError,
)
from hcli.lib.packs.lumina import importer as importer_module
from hcli.lib.packs.lumina import pack as pack_module
from hcli.lib.packs.lumina.importer import (
    ImportMember,
    ImportReport,
    ImportTotals,
    escape_controls,
    import_pack,
)
from hcli.lib.packs.lumina.pack import Digest
from hcli.lib.packs.lumina.push import NOT_LUMINA_HINT
from hcli.lib.packs.server import API_KEY_ENV, ApiKey, parse_server, tls_hint

PRODUCER = FIXTURE_PRODUCER
RETAINED_ROWS = FIXTURE_RETAINED_ROWS
sha = producer
KEY = "hrlu_test_key_0123456789"
SERVER = parse_server("https://lumina.test")


@dataclass
class FakeServer:
    """A Lumina /api/metadata: answers each POST with ``answer(funcs, call)``; records every call."""

    answer: Callable[[list[dict[str, Any]], int], httpx.Response] | None = None
    error: Exception | None = None
    calls: list[tuple[httpx.Request, dict[str, Any]]] = field(default_factory=list)
    stored: set[str] = field(default_factory=set)
    ca_certs: list[Path | None] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        body = json.loads(request.content)
        self.calls.append((request, body))
        if self.answer is not None:
            return self.answer(body["funcs"], len(self.calls) - 1)
        codes = []
        for func in body["funcs"]:
            codes.append({"code": "OK" if func["pattern"] in self.stored else "ADDED"})
            self.stored.add(func["pattern"])
        return httpx.Response(200, json={"results": codes})

    @property
    def funcs(self) -> list[dict[str, Any]]:
        return [func for _, body in self.calls for func in body["funcs"]]


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    fake = FakeServer()

    def client(_transport: object, ca_cert: Path | None = None, timeout: object = None) -> httpx.Client:
        fake.ca_certs.append(ca_cert)
        return httpx.Client(transport=httpx.MockTransport(fake.handle), follow_redirects=False)

    monkeypatch.setattr(importer_module, "lumina_client", client)
    return fake


def digest_of(path: Path) -> Digest:
    data = path.read_bytes()
    return Digest(hashlib.sha256(data).hexdigest(), len(data))


def run(
    path: Path = FIXTURE_PACK,
    *,
    expected: Digest | None = None,
    key: ApiKey | None = None,
    server_url: str = "https://lumina.test",
    **kwargs: Any,
) -> ImportReport:
    return import_pack(
        path,
        expected=expected or digest_of(path),
        server=parse_server(server_url),
        api_key=key or ApiKey(value=KEY),
        retry_base=0.0,
        sleep=lambda _seconds: None,
        **kwargs,
    )


# -- the real fixture -------------------------------------------------------------------


def test_the_fixture_pack_adds_every_retained_row_then_is_idempotent(server: FakeServer) -> None:
    first = run()
    assert first.total == ImportTotals(
        packs=1, members=1, rows=RETAINED_ROWS, pushed=RETAINED_ROWS, added=RETAINED_ROWS
    )
    (member,) = first.members
    assert (member.sha256, member.batches, member.pack) == (PRODUCER, 1, str(FIXTURE_PACK))
    request, body = server.calls[0]
    assert str(request.url) == "https://lumina.test/api/metadata"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert body["hostname"] == "hcli" and body["idb"] == body["input"] + ".i64"
    assert "provenance" not in body
    assert len({f["pattern"] for f in server.funcs}) == RETAINED_ROWS
    again = run()
    assert again.total == ImportTotals(packs=1, members=1, rows=RETAINED_ROWS, pushed=RETAINED_ROWS, ok=RETAINED_ROWS)


def test_members_larger_than_the_in_memory_limit_push_the_same(
    server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    run()
    in_memory = server.funcs
    monkeypatch.setattr(pack_module, "IN_MEMORY_MAX", 0)
    server.calls.clear()
    run()
    assert server.funcs == in_memory


# -- verification before the first request --------------------------------------------


@pytest.mark.parametrize(
    ("expected", "words"),
    [
        (lambda d: Digest("0" * 64, d.size), "does not match the Release: sha256"),
        (lambda d: Digest(d.sha256, d.size + 1), "does not match the Release: size"),
    ],
)
def test_a_pack_that_does_not_match_its_release_pushes_nothing(
    server: FakeServer, expected: Callable[[Digest], Digest], words: str
) -> None:
    with pytest.raises(IntegrityError, match=words):
        run(expected=expected(digest_of(FIXTURE_PACK)))
    assert server.calls == []


def test_a_bad_later_member_fails_verification_before_any_request(server: FakeServer, tmp_path: Path) -> None:
    good = lumina_arrow([lumina_rows(3)])
    bad = good.replace(b"metadata", b"metadatb")  # a schema with the wrong column name
    pack = write_lumina_pack(tmp_path, [(sha(1), good), (sha(2), bad)])
    with pytest.raises(IntegrityError, match=f"pack member {sha(2)} is not a valid Lumina Arrow file"):
        run(pack)
    assert server.calls == []


@pytest.mark.parametrize("change", ["member", "trailing"])
def test_a_pack_changed_after_verification_is_refused_before_pushing_it(
    server: FakeServer, tmp_path: Path, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = write_lumina_pack(
        tmp_path, [(sha(1), lumina_arrow([lumina_rows(3)])), (sha(2), lumina_arrow([lumina_rows(2)]))]
    )
    original = pack.read_bytes()
    verify = importer_module._Import.verify

    def verify_then_tamper(self: Any) -> None:
        verify(self)
        if change == "member":
            pack.write_bytes(
                lumina_pack([(sha(1), lumina_arrow([lumina_rows(4)])), (sha(2), lumina_arrow([lumina_rows(2)]))])
            )
        else:
            pack.write_bytes(original + b"\0" * 64)

    monkeypatch.setattr(importer_module._Import, "verify", verify_then_tamper)
    with pytest.raises(IntegrityError, match="changed since it was verified") as info:
        run(pack, expected=digest_of(pack))
    if change == "member":
        assert server.calls == []
        assert f"member {sha(1)} differs" in info.value.message
    else:
        # Bytes after the last member are only seen at the end, after every member was pushed.
        assert len(server.calls) == 2 and "verified as" in info.value.message


def test_a_member_whose_input_sha256_differs_from_its_name_warns(server: FakeServer, tmp_path: Path) -> None:
    pack = write_lumina_pack(tmp_path, [(sha(7), lumina_arrow([lumina_rows(2)]))])
    report = run(pack)
    assert any(f'pack member {sha(7)} records input_sha256 "{"ab" * 32}"' in w for w in report.warnings)


# -- what is pushed --------------------------------------------------------------------


def test_members_are_pushed_in_archive_order_with_their_filtered_rows(
    server: FakeServer, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rows = lumina_rows(6)
    rows["size"][0] = 31  # too small: not eligible
    rows["name"][1] = ""  # unnamed: not eligible
    rows["md5"][3] = rows["md5"][2]  # same MD5, another name: a conflicting group, dropped whole
    second = lumina_rows(2, start=100)
    pack = write_lumina_pack(tmp_path, [(sha(9), lumina_arrow([rows], metadata={})), (sha(3), lumina_arrow([second]))])
    with caplog.at_level("DEBUG", logger="hcli.lib.packs.lumina.importer"):
        report = run(pack)
    assert [m.sha256 for m in report.members] == [sha(9), sha(3)]
    assert [(m.rows, m.pushed) for m in report.members] == [(6, 2), (2, 2)]
    assert report.members[0].input == sha(9)  # no input_name: the member's sha256
    assert f"{sha(9)}: 6 rows, 4 push-eligible, 2 left after dropping conflicting MD5 groups" in caplog.text
    assert report.total == ImportTotals(packs=1, members=2, rows=8, pushed=4, added=4)


def test_codes_other_than_added_and_ok_count_as_other(server: FakeServer) -> None:
    server.answer = lambda funcs, _call: httpx.Response(
        200, json={"results": [{"code": c} for c in (["ADDED", "OK", "REJECTED"] * 40)[: len(funcs)]]}
    )
    report = run()  # one batch: codes cycle ADDED, OK, REJECTED
    expected = [i % 3 for i in range(RETAINED_ROWS)]
    assert (report.total.added, report.total.ok, report.total.other) == (
        expected.count(0),
        expected.count(1),
        expected.count(2),
    )


def test_fewer_results_than_functions_warns(server: FakeServer) -> None:
    server.answer = lambda _funcs, _call: httpx.Response(200, json={"results": [{"code": "ADDED"}]})
    record = run()
    assert any(f"server returned 1 results for {RETAINED_ROWS} functions" in w for w in record.warnings)


# -- configuration ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("   ", "API key is empty"),
        ("hrlu key", "API key has characters outside printable ASCII"),
        ("hrlué", "API key has characters outside printable ASCII"),
    ],
)
def test_the_api_key_is_trimmed_and_header_safe(server: FakeServer, value: str, words: str) -> None:
    with pytest.raises(PushConfigError, match=f"{API_KEY_ENV}: {words}"):
        run(key=ApiKey(value=value))
    assert server.calls == []


def test_a_trimmed_key_file_is_used(server: FakeServer, tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text(f"\n  {KEY}\n")
    key_file.chmod(0o600)
    record = run(key=ApiKey(path=key_file))
    assert server.calls[0][0].headers["Authorization"] == f"Bearer {KEY}"
    assert not any("other users can read it" in w for w in record.warnings)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_a_group_or_world_readable_key_file_warns(server: FakeServer, tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text(KEY)
    key_file.chmod(0o644)
    record = run(key=ApiKey(path=key_file))
    assert any(
        f"API key file {key_file} has mode 644; other users can read it (chmod 600 it)" in w for w in record.warnings
    )


def test_plain_http_warns_and_https_does_not(server: FakeServer) -> None:
    plain = run(server_url="http://lumina.test:8080/base/")
    assert str(server.calls[-1][0].url) == "http://lumina.test:8080/base/api/metadata"
    assert any("is plain http; the API key and functions travel unencrypted" in w for w in plain.warnings)
    secure = run()
    assert not any("plain http" in w for w in secure.warnings)


def test_the_ca_bundle_reaches_the_client(server: FakeServer, tmp_path: Path) -> None:
    run(ca_cert=tmp_path / "ca.pem")
    assert server.ca_certs == [tmp_path / "ca.pem"]


# -- failures ---------------------------------------------------------------------------


def test_a_refused_key_names_the_member_and_hints_the_key(server: FakeServer) -> None:
    server.answer = lambda *_: httpx.Response(401, json={"error": "unauthorized"})
    with pytest.raises(ServerRefusedError) as info:
        run()
    assert info.value.message.startswith(f"push {PRODUCER} ")
    assert info.value.message.endswith('batch rejected with HTTP 401 Unauthorized; body: {"error":"unauthorized"}')
    assert info.value.hint is not None and "is a valid importer key" in info.value.hint


def test_a_server_body_cannot_forge_output_lines(server: FakeServer) -> None:
    server.answer = lambda *_: httpx.Response(400, text="bad\nFAKE LINE\x1b[31m")
    with pytest.raises(ServerRefusedError) as info:
        run()
    assert "\n" not in info.value.message and "\x1b" not in info.value.message
    assert info.value.message.endswith("body: bad\\nFAKE LINE\\u{1b}[31m")


def test_one_function_over_the_body_cap_is_a_refusal(server: FakeServer) -> None:
    server.answer = lambda *_: httpx.Response(413)
    with pytest.raises(ServerRefusedError) as info:
        run()
    assert "single-function batch rejected with HTTP 413" in info.value.message
    assert info.value.hint is not None and "body cap" in info.value.hint


def test_server_errors_that_persist_are_transient(server: FakeServer) -> None:
    server.answer = lambda *_: httpx.Response(503)
    with pytest.raises(TransientError) as info:
        run()
    assert len(server.calls) == 6
    assert info.value.hint is not None and "safe to re-run" in info.value.hint


def test_an_untrusted_certificate_is_a_tls_error_with_the_ca_bundle_hint(server: FakeServer) -> None:
    error = httpx.ConnectError("certificate verify failed")
    error.__context__ = ssl.SSLCertVerificationError(1, "certificate verify failed")
    server.error = error
    with pytest.raises(TLSError) as info:
        run()
    assert info.value.message.startswith(f"push {PRODUCER} ")
    assert info.value.hint == tls_hint()


def test_a_server_that_is_not_lumina_is_a_configuration_error(server: FakeServer) -> None:
    server.answer = lambda *_: httpx.Response(200, text="<html>")
    with pytest.raises(PushConfigError) as info:
        run()
    assert info.value.hint == NOT_LUMINA_HINT


@pytest.mark.parametrize(
    ("status", "body", "hint_part"),
    [
        (403, "", "is a valid importer key"),
        (301, "", "http->https redirects are not followed"),
        (308, "", "/api/metadata is appended"),
        (404, "", "is the Lumina REST base URL"),
        (400, "bad", "HTTP 400"),
        # The body is the server's and can say anything: only the importer's own wording counts.
        (400, "HTTP 401 Location: x", "HTTP 400"),
    ],
)
def test_refusal_hints_follow_the_status(server: FakeServer, status: int, body: str, hint_part: str) -> None:
    headers = {"Location": "https://lumina.test/x"} if 300 <= status < 400 else {}
    server.answer = lambda *_: httpx.Response(status, text=body, headers=headers)
    with pytest.raises(ServerRefusedError) as info:
        run()
    assert info.value.hint is not None and hint_part in info.value.hint
    if status != 403:
        assert API_KEY_ENV not in info.value.hint


# -- the report --------------------------------------------------------------------------


def test_member_lines_have_the_documented_shape_and_escape_controls() -> None:
    member = ImportMember(
        pack="p", sha256=sha(1), input="lib\nFAKE", rows=5, pushed=4, batches=1, added=3, ok=1, other=0
    )
    assert member.line() == f"{sha(1)} lib\\nFAKE rows=5 ADDED=3 OK=1 other=0"


@pytest.mark.parametrize(
    ("text", "escaped"),
    [
        ("tab\there", "tab\\there"),
        ("cr\r", "cr\\r"),
        ("\x00", "\\u{0}"),
        ("\x7f\x85", "\\u{7f}\\u{85}"),
        ("λ ok", "λ ok"),
    ],
)
def test_control_characters_are_escaped_as_rust_does(text: str, escaped: str) -> None:
    assert escape_controls(text) == escaped


def test_totals_add_up() -> None:
    a = ImportTotals(packs=1, members=2, rows=10, pushed=8, added=5, ok=2, other=1)
    assert a + a == ImportTotals(packs=2, members=4, rows=20, pushed=16, added=10, ok=4, other=2)


def test_any_packs_error_is_raised_never_a_bare_exception(server: FakeServer, tmp_path: Path) -> None:
    missing = tmp_path / "missing-r1.tar.zst"
    with pytest.raises(PacksError) as info:
        run(missing, expected=Digest("0" * 64, 0))
    assert info.value.kind == "local_io"
