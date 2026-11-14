"""PortalBackend over hcli's API client, against a mock portal and storage.

Credentials go to the portal only, and every refusal is classified.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import LoggedOutAuth, make_meta, make_pack, make_release

from hcli.env import ENV
from hcli.lib.api.common import APIClient
from hcli.lib.packs.errors import IntegrityError, NetworkError, PacksError
from hcli.lib.packs.models import PackFormat, ReleasePack
from hcli.lib.packs.repository import PackRepository, PortalBackend

API = "https://portal.test"
RECORDS = f"{API}/api/assets/packs"
STORAGE = "https://storage.test"
PAYLOAD = b"pack archive bytes" * 4
PACK_ENTRY = make_pack("x86", PAYLOAD, b"meta")
PACK = ReleasePack.model_validate(PACK_ENTRY)
RELEASE = json.dumps(make_release([PACK_ENTRY])).encode()

Handler = Callable[[httpx.Request], httpx.Response]


def _record(key: str, url: str | None) -> dict[str, object]:
    """The asset record the portal answers (the fields hcli relies on)."""
    return {"key": key, "filename": key.rsplit("/", 1)[-1], "size": 1, "version": 1, "url": url}


Runner = Callable[[Handler, Callable[[PackRepository], Awaitable[Any]]], Any]


@pytest.fixture(autouse=True)
def portal_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ENV, "HCLI_API_URL", API)


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> Runner:
    """Answer hcli's API client with ``handler``, open a repository, run ``action`` on it, close it."""

    def go(handler: Handler, action: Callable[[PackRepository], Awaitable[Any]]) -> Any:
        # As in hcli's API client tests: the client's transport answers through the handler.
        client = APIClient()
        client.client = httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler))
        monkeypatch.setattr("hcli.lib.api.common._api_client", client)

        async def scenario() -> Any:
            try:
                async with PackRepository(PortalBackend()) as repo:
                    return await action(repo)
            finally:
                await client.client.aclose()

        return asyncio.run(scenario())

    return go


def portal(files: dict[str, bytes], *, storage: Handler | None = None) -> tuple[Handler, list[httpx.Request]]:
    """A portal answering a record (with a signed link) per key, and a storage serving the bytes."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url.startswith(f"{RECORDS}/"):
            key = url.removeprefix(f"{RECORDS}/")
            if key not in files:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, json=_record(key, f"{STORAGE}/signed/{key}?X-Amz-Signature=abc"))
        if storage is not None:
            return storage(request)
        key = request.url.path.removeprefix("/signed/")
        return httpx.Response(200, content=files[key]) if key in files else httpx.Response(404)

    return handler, seen


def test_reads_the_record_with_credentials_then_the_link_without(run: Runner) -> None:
    handler, seen = portal({"lumina/release": RELEASE})
    release = run(handler, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert release.packs[0].address == "msvc/windows-all-x86"
    record, download = seen
    assert str(record.url) == f"{RECORDS}/lumina/release"
    assert record.headers["x-api-key"] == ENV.HCLI_API_KEY
    # The storage gets the signed link only: never the portal credentials.
    assert download.url.host == "storage.test"
    assert "x-api-key" not in download.headers and "authorization" not in download.headers


def test_downloads_a_pack_by_its_portal_key(tmp_path: Path, run: Runner) -> None:
    handler, seen = portal({"lumina/msvc/windows-all-x86/pack": PAYLOAD})
    run(
        handler,
        lambda repo: repo.download_verified(
            "lumina/msvc/windows-all-x86/pack", tmp_path / "p", size=len(PAYLOAD), sha256=PACK.sha256
        ),
    )
    assert (tmp_path / "p").read_bytes() == PAYLOAD
    assert [request.url.host for request in seen] == ["portal.test", "storage.test"]


def test_a_pack_newer_than_the_release_is_never_exposed(tmp_path: Path, run: Runner) -> None:
    # The portal registered a new revision before the Release that lists it.
    handler, _ = portal({"lumina/msvc/windows-all-x86/pack": b"the next revision"})
    with pytest.raises(IntegrityError) as info:
        run(
            handler,
            lambda repo: repo.download_verified(
                "lumina/msvc/windows-all-x86/pack", tmp_path / "p", size=len(PAYLOAD), sha256=PACK.sha256
            ),
        )
    assert info.value.hint is not None and "portal may have been updating" in info.value.hint
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("status", "kind", "hint"),
    [
        (401, "credentials", "hcli login"),
        (403, "access_denied", "assets:read"),
        (404, "release_unavailable", "may not have access"),
        (500, "repository_error", None),
    ],
)
def test_portal_refusals_on_the_release(status: int, kind: str, hint: str | None, run: Runner) -> None:
    with pytest.raises(PacksError) as info:
        run(lambda _request: httpx.Response(status), lambda repo: repo.read_release(PackFormat.LUMINA))
    assert info.value.kind == kind
    assert "lumina" in info.value.message
    if hint is not None:
        assert info.value.hint is not None and hint in info.value.hint
    else:
        assert info.value.message == "Could not read lumina/release: HTTP 500 from the portal."


def test_pack_missing_on_the_portal_suggests_a_retry(run: Runner) -> None:
    handler, _ = portal({})
    with pytest.raises(PacksError) as info:
        run(handler, lambda repo: repo.fetch_pack(PackFormat.LUMINA, PACK))
    assert info.value.kind == "not_found"
    assert "although the stable Release lists it" in info.value.message
    assert info.value.hint is not None and "try again in a minute" in info.value.hint
    assert "sync" not in info.value.hint


@pytest.mark.parametrize(
    ("status", "kind", "words"),
    [(403, "access_denied", "link may have expired"), (404, "not_found", "report it to Hex-Rays")],
)
def test_storage_refusals_are_not_blamed_on_the_portal(
    status: int, kind: str, words: str, tmp_path: Path, run: Runner
) -> None:
    handler, _ = portal({"lumina/release": RELEASE}, storage=lambda _request: httpx.Response(status))
    with pytest.raises(PacksError) as info:
        run(handler, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert info.value.kind == kind
    assert "storage" in info.value.message
    assert info.value.hint is not None and words in info.value.hint


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json=_record("lumina/release", None)),
        httpx.Response(200, json=_record("lumina/release", "")),
        httpx.Response(200, json={k: v for k, v in _record("lumina/release", None).items() if k != "url"}),
    ],
)
def test_a_record_without_a_link_is_not_found(answer: httpx.Response, run: Runner) -> None:
    with pytest.raises(PacksError) as info:
        run(lambda _request: answer, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert info.value.kind == "not_found"
    assert "no file to download" in info.value.message


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, content=b"<html>login</html>"),
        httpx.Response(200, json=["not", "a", "record"]),
        # Not a record, but it carries a signed link: the link must not leak into the message.
        httpx.Response(200, json={"url": f"{STORAGE}/signed/x?X-Amz-Signature=secret"}),
    ],
)
def test_an_unreadable_portal_answer_is_reported(answer: httpx.Response, run: Runner) -> None:
    with pytest.raises(PacksError) as info:
        run(lambda _request: answer, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert info.value.kind == "repository_error"
    assert "unreadable answer" in info.value.message
    assert "secret" not in json.dumps(info.value.to_json())


def test_a_portal_redirect_is_not_followed_with_the_credentials(run: Runner) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.test/steal"})

    with pytest.raises(PacksError):
        run(handler, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert [request.url.host for request in seen] == ["portal.test"]


class _FailingStream(httpx.AsyncByteStream):
    """A body that breaks after the first chunk; records aclose()."""

    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"abcd"
        raise httpx.ReadError("connection reset")

    async def aclose(self) -> None:
        self.closed = True


def test_a_broken_download_is_a_network_error_and_leaves_nothing(tmp_path: Path, run: Runner) -> None:
    stream = _FailingStream()
    handler, _ = portal({"k": b""}, storage=lambda _request: httpx.Response(200, stream=stream))
    with pytest.raises(NetworkError, match=r"cannot reach storage\.test \(ReadError\)"):
        run(handler, lambda repo: repo.download_verified("k", tmp_path / "x", size=8, sha256="0" * 64))
    assert stream.closed
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("api", "endpoint", "url"),
    [
        ("https://api.eu.hex-rays.com", ("api.eu.hex-rays.com", 443), "https://api.eu.hex-rays.com/api/assets/packs"),
        ("http://localhost:3000/", ("localhost", 3000), "http://localhost:3000/api/assets/packs"),
    ],
)
def test_endpoint_and_url_follow_the_api_url(
    monkeypatch: pytest.MonkeyPatch, api: str, endpoint: tuple[str, int], url: str
) -> None:
    monkeypatch.setattr(ENV, "HCLI_API_URL", api)
    backend = PortalBackend()
    assert backend.endpoint() == endpoint
    assert backend.url == url


def test_a_login_lost_before_the_request_is_a_credentials_error(monkeypatch: pytest.MonkeyPatch, run: Runner) -> None:
    # The commands are AuthCommands, but the API client checks again: nothing is sent without a login.
    monkeypatch.setattr(ENV, "HCLI_API_KEY", None)
    monkeypatch.setattr("hcli.lib.api.common.get_auth_service", LoggedOutAuth)
    handler, seen = portal({"lumina/release": RELEASE})
    with pytest.raises(PacksError) as info:
        run(handler, lambda repo: repo.read_release(PackFormat.LUMINA))
    assert info.value.kind == "credentials"
    assert seen == []


def test_keys_match_the_portal_layout(cache_root: Path, run: Runner) -> None:
    # Spelled out on purpose: the portal's key layout is the contract.
    meta = make_meta([])
    entry = make_pack("x86_64", PAYLOAD, meta)
    handler, seen = portal(
        {"lumina/msvc/windows-all-x86_64/pack": PAYLOAD, "lumina/msvc/windows-all-x86_64/contents": meta}
    )
    pack = ReleasePack.model_validate(entry)

    async def both(repo: PackRepository) -> None:
        await repo.fetch_pack(PackFormat.LUMINA, pack)
        await repo.read_meta(PackFormat.LUMINA, pack)

    run(handler, both)
    portal_paths = [request.url.path for request in seen if request.url.host == "portal.test"]
    assert portal_paths == [
        "/api/assets/packs/lumina/msvc/windows-all-x86_64/pack",
        "/api/assets/packs/lumina/msvc/windows-all-x86_64/contents",
    ]
