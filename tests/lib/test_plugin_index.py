import json
import os
import shutil
import tempfile

import pytest

from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.repo.github import (
    GitHubGraphQLClient,
    fetch_github_release_zip_asset,
    get_release_asset,
    get_release_metadata,
    get_source_archive,
    parse_repository,
)
from hcli.lib.util.cache import get_cache_directory


@pytest.fixture
def temp_hcli_cache_dir():
    temp_dir = tempfile.mkdtemp()
    old_history = os.environ.get("HCLI_CACHE_DIR", "")
    os.environ["HCLI_CACHE_DIR"] = temp_dir
    try:
        yield
    finally:
        shutil.rmtree(temp_dir)
        if old_history:
            os.environ["HCLI_CACHE_DIR"] = old_history
        else:
            os.environ.pop("HCLI_CACHE_DIR", None)


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_github_client_get_releases():
    token = os.getenv("GITHUB_TOKEN")
    assert token is not None, "GITHUB_TOKEN is not set"
    client = GitHubGraphQLClient(token)

    owner, repo = parse_repository("airbus-cert/ttddbg")
    releases = client.get_releases(owner, repo).releases

    assert len(releases) > 0, "Expected at least one release for airbus-cert/ttddbg"

    tags = [release.tag_name for release in releases]
    assert "v1.2.0" in tags


def test_get_cache_directory_invalid_path_keys(temp_hcli_cache_dir):
    with pytest.raises(ValueError):
        get_cache_directory("")

    with pytest.raises(ValueError):
        get_cache_directory(".")

    with pytest.raises(ValueError):
        get_cache_directory("..")

    with pytest.raises(ValueError):
        get_cache_directory("tëst")

    with pytest.raises(ValueError):
        get_cache_directory("test\ttab")

    with pytest.raises(ValueError):
        get_cache_directory("test\nnewline")

    with pytest.raises(ValueError):
        get_cache_directory("test\rcarriage")

    with pytest.raises(ValueError):
        get_cache_directory("test/slash")

    with pytest.raises(ValueError):
        get_cache_directory("test\\backslash")

    with pytest.raises(ValueError):
        get_cache_directory("valid", "", "alsovalid")

    cache_dir = get_cache_directory("valid", "path", "components")
    assert cache_dir.exists()
    assert cache_dir.is_dir()


def test_json_file_plugin_repo_from_url_follows_https_redirects(httpx_mock):
    url = "https://example.com/repo.json"
    redirected_url = "https://cdn.example.com/repo.json"
    doc = {
        "version": 1,
        "plugins": [
            {
                "name": "plugin1",
                "host": "https://github.com/HexRaysSA/ida-hcli",
                "versions": {},
            }
        ],
    }

    httpx_mock.add_response(url=url, status_code=302, headers={"Location": redirected_url})
    httpx_mock.add_response(url=redirected_url, status_code=200, text=json.dumps(doc))

    repo = JSONFilePluginRepo.from_url(url)

    assert [plugin.name for plugin in repo.get_plugins()] == ["plugin1"]


def test_fetch_github_release_zip_asset_follows_asset_redirects(httpx_mock):
    release_url = "https://api.github.com/repos/owner/repo/releases/latest"
    asset_url = "https://github.com/owner/repo/releases/download/v1.0.0/plugin.zip"
    redirected_asset_url = "https://objects.githubusercontent.com/plugin.zip"
    asset_content = b"plugin archive"
    release_doc = {
        "assets": [
            {
                "name": "plugin.zip",
                "size": len(asset_content),
                "browser_download_url": asset_url,
            }
        ]
    }

    httpx_mock.add_response(url=release_url, status_code=200, json=release_doc)
    httpx_mock.add_response(url=asset_url, status_code=302, headers={"Location": redirected_asset_url})
    httpx_mock.add_response(url=redirected_asset_url, status_code=200, content=asset_content)

    assert fetch_github_release_zip_asset("owner", "repo") == asset_content


def test_fetch_github_release_zip_asset_rejects_http_downgrade(httpx_mock):
    release_url = "https://api.github.com/repos/owner/repo/releases/latest"
    asset_url = "https://github.com/owner/repo/releases/download/v1.0.0/plugin.zip"
    redirected_asset_url = "http://objects.githubusercontent.com/plugin.zip"
    asset_content = b"plugin archive"
    release_doc = {
        "assets": [
            {
                "name": "plugin.zip",
                "size": len(asset_content),
                "browser_download_url": asset_url,
            }
        ]
    }

    httpx_mock.add_response(url=release_url, status_code=200, json=release_doc)
    httpx_mock.add_response(url=asset_url, status_code=302, headers={"Location": redirected_asset_url})
    httpx_mock.add_response(url=redirected_asset_url, status_code=200, content=asset_content)

    with pytest.raises(ValueError, match="HTTPS request was redirected to insecure HTTP URL"):
        fetch_github_release_zip_asset("owner", "repo")


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_get_releases(temp_hcli_cache_dir):
    token = os.getenv("GITHUB_TOKEN")
    assert token is not None, "GITHUB_TOKEN is not set"
    client = GitHubGraphQLClient(token)

    owner, repo = parse_repository("airbus-cert/ttddbg")
    releases = client.get_releases(owner, repo).releases

    assert len(releases) > 0, "Expected at least one release for airbus-cert/ttddbg"

    tags = [release.tag_name for release in releases]
    assert "v1.2.0" in tags


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_get_release(temp_hcli_cache_dir):
    token = os.getenv("GITHUB_TOKEN")
    assert token is not None, "GITHUB_TOKEN is not set"
    client = GitHubGraphQLClient(token)

    owner, repo = parse_repository("airbus-cert/ttddbg")
    release = get_release_metadata(client, owner, repo, "v1.2.0")

    assert release.name == "SSTIC 2023 Release"


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_get_source_archive(temp_hcli_cache_dir):
    token = os.getenv("GITHUB_TOKEN")
    assert token is not None, "GITHUB_TOKEN is not set"
    client = GitHubGraphQLClient(token)

    owner, repo = parse_repository("airbus-cert/ttddbg")
    release = get_release_metadata(client, owner, repo, "v1.2.0")

    buf = get_source_archive(owner, repo, release.commit_hash, release.zipball_url)
    assert len(buf) == 43759079


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_get_release_asset(temp_hcli_cache_dir):
    token = os.getenv("GITHUB_TOKEN")
    assert token is not None, "GITHUB_TOKEN is not set"
    client = GitHubGraphQLClient(token)

    owner, repo = parse_repository("airbus-cert/ttddbg")
    release = get_release_metadata(client, owner, repo, "v1.2.0")

    asset = release.assets[0]
    buf = get_release_asset(owner, repo, "v1.2.0", asset)
    assert len(buf) == 696320
