"""Tests for the ignored-URLs list and the rejected-archive log entries of the GitHub indexer."""

import io
import json
import logging
import re
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import PLUGINS_DIR

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin.repo import github
from hcli.lib.ida.plugin.repo.github import GitHubCommit, GithubPluginRepo, GitHubRelease, GitHubReleases, GitHubTag

# plugin1 declares https://github.com/HexRaysSA/ida-hcli as its repository
VALID_ZIP = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
OWNER, REPO = "hexrayssa", "ida-hcli"


def edit_metadata(zip_data: bytes, repository: str | None = None, entry_point: str | None = None) -> bytes:
    """Rewrite fields of every ida-plugin.json in an archive."""
    src = zipfile.ZipFile(io.BytesIO(zip_data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("ida-plugin.json"):
                metadata = json.loads(data)
                if repository is not None:
                    metadata["plugin"]["urls"]["repository"] = repository
                if entry_point is not None:
                    metadata["plugin"]["entryPoint"] = entry_point
                data = json.dumps(metadata).encode("utf-8")
            dst.writestr(name, data)
    return buf.getvalue()


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def commit(oid: str) -> dict:
    return {
        "oid": oid,
        "committedDate": "2026-10-01T00:00:00Z",
        "zipballUrl": f"https://codeload.github.com/{OWNER}/{REPO}/legacy.zip/{oid}",
    }


def release(tag: str, oid: str, assets: list[str]) -> GitHubRelease:
    return GitHubRelease.from_dict(
        {
            "name": tag,
            "tagName": tag,
            "createdAt": "2026-10-01T00:00:00Z",
            "publishedAt": "2026-10-01T00:00:00Z",
            "isPrerelease": False,
            "isDraft": False,
            "url": f"https://github.com/{OWNER}/{REPO}/releases/tag/{tag}",
            "releaseAssets": {
                "nodes": [
                    {
                        "name": name,
                        "contentType": "application/zip",
                        "size": 1,
                        "downloadUrl": f"https://github.com/{OWNER}/{REPO}/releases/download/{tag}/{name}",
                    }
                    for name in assets
                ]
            },
            "tag": {"target": commit(oid)},
        },
        OWNER,
        REPO,
    )


SRC_VALID = commit("a" * 40)["zipballUrl"]
SRC_NO_METADATA = commit("b" * 40)["zipballUrl"]
SRC_HOST_MISMATCH = commit("c" * 40)["zipballUrl"]
ASSET_VALID = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/valid.zip"
ASSET_INVALID_METADATA = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/broken.zip"
ASSET_IGNORED = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/ignored.zip"

CONTENT = {
    SRC_VALID: VALID_ZIP,
    SRC_NO_METADATA: make_zip({"README.md": b"not a plugin"}),
    SRC_HOST_MISMATCH: edit_metadata(VALID_ZIP, repository="https://github.com/someone-else/ida-hcli"),
    ASSET_VALID: VALID_ZIP,
    ASSET_INVALID_METADATA: edit_metadata(VALID_ZIP, entry_point="missing.py"),
    ASSET_IGNORED: VALID_ZIP,
}
REJECTED = {SRC_NO_METADATA, SRC_HOST_MISMATCH, ASSET_INVALID_METADATA}


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Serve one repository with a fixed set of releases and tags; return the list of downloaded URLs."""
    monkeypatch.setenv("HCLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    releases = GitHubReleases(
        default_branch=GitHubCommit.from_dict(commit("d" * 40)),
        releases=[
            release("v1.0.0", "a" * 40, ["valid.zip", "broken.zip", "ignored.zip"]),
            release("v0.9.0", "b" * 40, []),
        ],
        tags=[
            GitHubTag.from_dict({"name": "v0.8.0", "target": commit("c" * 40)}),
        ],
    )

    downloads: list[str] = []

    def download_source_archive(zip_url: str) -> bytes:
        downloads.append(zip_url)
        return CONTENT[zip_url]

    def download_release_asset(owner, repo, release_id, asset) -> bytes:
        downloads.append(asset.download_url)
        return CONTENT[asset.download_url]

    monkeypatch.setattr(GithubPluginRepo, "_get_repos", lambda self: [(OWNER, REPO)])
    monkeypatch.setattr(github, "warm_releases_metadata_cache", lambda client, repos: None)
    monkeypatch.setattr(github, "get_releases_metadata", lambda client, owner, repo: releases)
    monkeypatch.setattr(github, "download_source_archive", download_source_archive)
    monkeypatch.setattr(github, "download_release_asset", download_release_asset)
    return downloads


def logged_rejected_urls(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Read the rejected archive URLs from the log, as the plugin-repository sync does."""
    urls = []
    for record in caplog.records:
        message = record.getMessage()
        if not message.startswith("no valid plugin in archive:"):
            continue
        match = re.search(r"<structured: (\{.*\})>", message)
        assert match is not None
        urls.append(json.loads(match.group(1))["url"])
    return urls


def test_snapshot_skips_ignored_urls_and_logs_rejected_archives(
    fake_github: list[str], caplog: pytest.LogCaptureFixture, tmp_path: Path
):
    caplog.set_level(logging.INFO, logger="hcli")
    ignored = tmp_path / "ignored-urls.txt"
    ignored.write_text(f"# archives that hold no valid plugin\n{ASSET_IGNORED}\n", encoding="utf-8")

    result = CliRunner().invoke(
        plugin_group, ["--repo", "github", "--with-ignored-urls-list", str(ignored), "repo", "snapshot"]
    )

    assert result.exit_code == 0, result.output
    assert set(fake_github) == set(CONTENT) - {ASSET_IGNORED}
    assert sorted(logged_rejected_urls(caplog)) == sorted(REJECTED)


def test_github_repo_logs_url_rejected_only_under_old_repository_name(
    fake_github: list[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # A renamed repository listed under its old and new name: GitHub follows the rename,
    # so both entries yield the same archive URLs, but only the new name matches the host.
    monkeypatch.setattr(GithubPluginRepo, "_get_repos", lambda self: [(OWNER, "old-name"), (OWNER, REPO)])
    caplog.set_level(logging.INFO, logger="hcli")

    plugins = GithubPluginRepo("token").get_plugins()

    urls = {loc.url for p in plugins for locs in p.versions.values() for loc in locs}
    assert urls == {SRC_VALID, ASSET_VALID, ASSET_IGNORED}
    # each URL that both names reject is logged once
    assert sorted(logged_rejected_urls(caplog)) == sorted(REJECTED)
