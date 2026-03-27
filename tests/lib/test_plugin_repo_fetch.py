import json

import pytest

from hcli.lib.ida.plugin.repo import fetch_plugin_archive
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.plugin.repo.github import fetch_github_release_zip_asset


def test_fetch_plugin_archive_follows_https_redirects(httpx_mock):
    """Plugin archive fetches should succeed through HTTPS redirects."""
    url = "https://example.com/archive.zip"
    redirected_url = "https://cdn.example.com/archive.zip"

    httpx_mock.add_response(url=url, status_code=302, headers={"Location": redirected_url})
    httpx_mock.add_response(url=redirected_url, status_code=200, content=b"plugin archive")

    assert fetch_plugin_archive(url) == b"plugin archive"


def test_fetch_plugin_archive_rejects_https_redirect_to_http(httpx_mock):
    """Plugin archive fetches should reject HTTPS redirects that downgrade to HTTP."""
    url = "https://example.com/archive.zip"
    redirected_url = "http://cdn.example.com/archive.zip"

    httpx_mock.add_response(url=url, status_code=302, headers={"Location": redirected_url})
    httpx_mock.add_response(url=redirected_url, status_code=200, content=b"plugin archive")

    with pytest.raises(ValueError, match="HTTPS request was redirected to insecure HTTP URL"):
        fetch_plugin_archive(url)


def test_json_file_plugin_repo_from_url_follows_https_redirects(httpx_mock):
    """Plugin repository JSON fetches should succeed through HTTPS redirects."""
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
    """GitHub release asset downloads should succeed through HTTPS redirects."""
    release_url = "https://api.github.com/repos/owner/repo/releases/latest"
    asset_url = "https://github.com/owner/repo/releases/download/v1.0.0/plugin.zip"
    redirected_asset_url = "https://objects.githubusercontent.com/plugin.zip"
    release_doc = {
        "assets": [
            {
                "name": "plugin.zip",
                "size": 13,
                "browser_download_url": asset_url,
            }
        ]
    }

    httpx_mock.add_response(url=release_url, status_code=200, json=release_doc)
    httpx_mock.add_response(url=asset_url, status_code=302, headers={"Location": redirected_asset_url})
    httpx_mock.add_response(url=redirected_asset_url, status_code=200, content=b"plugin archive")

    assert fetch_github_release_zip_asset("owner", "repo") == b"plugin archive"
