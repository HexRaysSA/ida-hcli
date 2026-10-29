import asyncio
import os
from pathlib import Path

import httpx
import pytest

from hcli.lib.api.common import APIClient, APIError

URL = "https://storage.example/bucket/sample.bin?sig=abc"
PAYLOAD = b"fresh-bytes"


@pytest.fixture
def cache_dir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "cache"
    monkeypatch.setenv("HCLI_CACHE_DIR", str(path))
    return path


def _client(handler) -> tuple[APIClient, list[httpx.Request]]:
    """APIClient whose transport records every request and answers via handler."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = APIClient()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return client, requests


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PAYLOAD)


def _download(client: APIClient, target_dir: Path, **kwargs) -> str:
    return asyncio.run(client.download_file(URL, target_dir=target_dir, **kwargs))


def _seed_cache(cache_dir: Path, key: str, content: bytes) -> Path:
    path = cache_dir / "downloads" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_no_cache_ignores_stale_cached_file_of_matching_size(cache_dir, tmp_path):
    # Same size as the remote payload: the cached path would happily reuse it.
    stale = _seed_cache(cache_dir, "deadbeef", b"stale-bytes")
    assert len(stale.read_bytes()) == len(PAYLOAD)
    client, requests = _client(_ok)

    path = _download(client, tmp_path / "out", asset_key="deadbeef", cache=False)

    assert Path(path).read_bytes() == PAYLOAD
    assert [r.method for r in requests] == ["GET"]
    assert stale.read_bytes() == b"stale-bytes"


def test_no_cache_never_writes_to_cache_directory(cache_dir, tmp_path):
    client, _ = _client(_ok)

    _download(client, tmp_path / "out", target_filename="x.bin", cache=False)

    assert not (cache_dir / "downloads").exists() or not any((cache_dir / "downloads").rglob("*"))


def test_no_cache_with_force_still_bypasses_cache(cache_dir, tmp_path):
    client, _ = _client(_ok)

    _download(client, tmp_path / "out", asset_key="k", force=True, cache=False)

    assert not (cache_dir / "downloads" / "k").exists()


def test_no_cache_creates_missing_target_dir(cache_dir, tmp_path):
    client, _ = _client(_ok)
    target = tmp_path / "a" / "b" / "c"

    path = _download(client, target, cache=False)

    assert Path(path) == target.resolve() / "sample.bin"
    assert Path(path).read_bytes() == PAYLOAD


def test_no_cache_overwrites_existing_target(cache_dir, tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    (target / "sample.bin").write_bytes(b"old content that is longer than the payload")
    client, _ = _client(_ok)

    path = _download(client, target, cache=False)

    assert Path(path).read_bytes() == PAYLOAD


def test_no_cache_http_error_raises_and_leaves_no_cache_entry(cache_dir, tmp_path):
    client, _ = _client(lambda r: httpx.Response(500, content=b"boom"))

    with pytest.raises(APIError):
        _download(client, tmp_path / "out", asset_key="k", cache=False)

    assert not (cache_dir / "downloads" / "k").exists()


def test_no_cache_sends_no_auth_headers_when_auth_false(cache_dir, tmp_path):
    client, requests = _client(_ok)

    _download(client, tmp_path / "out", cache=False)

    assert "authorization" not in requests[0].headers
    assert "content-type" not in requests[0].headers


def test_default_still_reuses_cache_when_size_matches(cache_dir, tmp_path):
    # Regression guard: the default path must keep its caching behaviour.
    _seed_cache(cache_dir, "k", b"cached-byte")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(PAYLOAD))})
        raise AssertionError("GET must not be issued on a cache hit")

    client, _ = _client(handler)

    path = _download(client, tmp_path / "out", asset_key="k")

    assert Path(path).read_bytes() == b"cached-byte"


def test_default_writes_cache_entry(cache_dir, tmp_path):
    client, _ = _client(_ok)

    _download(client, tmp_path / "out", asset_key="k")

    assert (cache_dir / "downloads" / "k").read_bytes() == PAYLOAD


def test_no_cache_does_not_follow_cache_key_path_traversal(cache_dir, tmp_path):
    # asset_key is irrelevant without a cache; a hostile key must not be used.
    client, _ = _client(_ok)
    escape = tmp_path / "escaped"

    _download(client, tmp_path / "out", asset_key=os.path.join("..", "..", "escaped"), cache=False)

    assert not escape.exists()
