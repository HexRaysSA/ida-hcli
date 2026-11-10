"""Tests for the ignored-URLs list used by the GitHub plugin repository indexer."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import PLUGINS_DIR

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin.repo import PluginArchiveIndex, github
from hcli.lib.ida.plugin.repo.github import (
    GitHubCommit,
    GithubPluginRepo,
    GitHubRelease,
    GitHubReleases,
    GitHubTag,
    append_ignored_urls,
    get_release_asset_cache_directory,
    get_source_archive_cache_directory,
)

# plugin1 declares https://github.com/HexRaysSA/ida-hcli as its repository
VALID_ZIP = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
HOST = "https://github.com/HexRaysSA/ida-hcli"
OWNER, REPO = "hexrayssa", "ida-hcli"


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def with_repository(zip_data: bytes, repository: str) -> bytes:
    """Rewrite the repository URL in every ida-plugin.json of an archive."""
    src = zipfile.ZipFile(io.BytesIO(zip_data))
    files = {}
    for name in src.namelist():
        data = src.read(name)
        if name.endswith("ida-plugin.json"):
            metadata = json.loads(data)
            metadata["plugin"]["urls"]["repository"] = repository
            data = json.dumps(metadata).encode("utf-8")
        files[name] = data
    return make_zip(files)


NO_METADATA_ZIP = make_zip({"README.md": b"not a plugin"})
INVALID_METADATA_ZIP = make_zip({"ida-plugin.json": b'{"plugin": {}}'})
HOST_MISMATCH_ZIP = with_repository(VALID_ZIP, "https://github.com/someone-else/ida-hcli")


@pytest.mark.parametrize(
    "zip_data, expected",
    [
        (VALID_ZIP, 1),
        (NO_METADATA_ZIP, 0),
        (INVALID_METADATA_ZIP, 0),
        (HOST_MISMATCH_ZIP, 0),
    ],
    ids=["valid", "no-metadata", "invalid-metadata", "host-mismatch"],
)
def test_index_plugin_archive_returns_number_of_indexed_plugins(zip_data: bytes, expected: int):
    index = PluginArchiveIndex()
    assert index.index_plugin_archive(zip_data, "https://example.com/a.zip", expected_host=HOST) == expected


def test_append_ignored_urls_adds_timestamped_block(tmp_path: Path):
    path = tmp_path / "ignored-urls.txt"
    path.write_text("# header\nhttps://example.com/old.zip", encoding="utf-8")  # no trailing newline

    append_ignored_urls(path, ["https://example.com/a.zip", "https://example.com/b.zip"])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["# header", "https://example.com/old.zip", ""]
    assert lines[3].startswith("# Discovered on ")
    assert lines[4:] == ["https://example.com/a.zip", "https://example.com/b.zip"]


def test_append_ignored_urls_does_nothing_without_urls(tmp_path: Path):
    path = tmp_path / "ignored-urls.txt"
    path.write_text("# header\n", encoding="utf-8")

    append_ignored_urls(path, [])

    assert path.read_text(encoding="utf-8") == "# header\n"


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
SRC_REJECTED = commit("b" * 40)["zipballUrl"]
SRC_TAG_REJECTED = commit("c" * 40)["zipballUrl"]
ASSET_VALID = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/valid.zip"
ASSET_REJECTED = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/docs.zip"
ASSET_IGNORED = f"https://github.com/{OWNER}/{REPO}/releases/download/v1.0.0/ignored.zip"

CONTENT = {
    SRC_VALID: VALID_ZIP,
    SRC_REJECTED: NO_METADATA_ZIP,
    SRC_TAG_REJECTED: HOST_MISMATCH_ZIP,
    ASSET_VALID: VALID_ZIP,
    ASSET_REJECTED: INVALID_METADATA_ZIP,
    ASSET_IGNORED: VALID_ZIP,
}


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Serve one repository with a fixed set of releases and tags; return the list of downloaded URLs."""
    monkeypatch.setenv("HCLI_CACHE_DIR", str(tmp_path / "cache"))

    releases = GitHubReleases(
        default_branch=GitHubCommit.from_dict(commit("d" * 40)),
        releases=[
            release("v1.0.0", "a" * 40, ["valid.zip", "docs.zip", "ignored.zip"]),
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


def test_github_repo_skips_ignored_urls_and_records_rejected_ones(fake_github: list[str]):
    repo = GithubPluginRepo("token", ignored_urls=[ASSET_IGNORED])

    plugins = repo.get_plugins()

    assert ASSET_IGNORED not in fake_github
    assert set(fake_github) == set(CONTENT) - {ASSET_IGNORED}
    assert sorted(repo.newly_ignored_urls) == sorted([SRC_REJECTED, SRC_TAG_REJECTED, ASSET_REJECTED])

    urls = {loc.url for p in plugins for locs in p.versions.values() for loc in locs}
    assert urls == {SRC_VALID, ASSET_VALID}


def test_github_repo_keeps_only_valid_archives_in_cache(fake_github: list[str]):
    GithubPluginRepo("token").get_plugins()

    assert (get_source_archive_cache_directory(OWNER, REPO, "a" * 40) / "source.zip").exists()
    assert (get_release_asset_cache_directory(OWNER, REPO, "v1.0.0") / "valid.zip").exists()
    assert not (get_source_archive_cache_directory(OWNER, REPO, "b" * 40) / "source.zip").exists()
    assert not (get_source_archive_cache_directory(OWNER, REPO, "c" * 40) / "source.zip").exists()
    assert not (get_release_asset_cache_directory(OWNER, REPO, "v1.0.0") / "docs.zip").exists()


def test_snapshot_appends_rejected_urls_to_ignored_urls_list(
    fake_github: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    ignored = tmp_path / "ignored-urls.txt"
    ignored.write_text(f"# archives that hold no valid plugin\n{ASSET_IGNORED}\n", encoding="utf-8")

    result = CliRunner().invoke(
        plugin_group, ["--repo", "github", "--with-ignored-urls-list", str(ignored), "repo", "snapshot"]
    )

    assert result.exit_code == 0, result.output
    assert ASSET_IGNORED not in fake_github
    snapshot = json.loads(result.stdout)
    assert ASSET_IGNORED not in json.dumps(snapshot)

    lines = ignored.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == ["# archives that hold no valid plugin", ASSET_IGNORED, ""]
    assert lines[3].startswith("# Discovered on ")
    assert sorted(lines[4:]) == sorted([SRC_REJECTED, SRC_TAG_REJECTED, ASSET_REJECTED])

    # a second run reads the valid archives from the cache, skips the rejected ones,
    # and so downloads nothing and adds nothing
    fake_github.clear()
    result = CliRunner().invoke(
        plugin_group, ["--repo", "github", "--with-ignored-urls-list", str(ignored), "repo", "snapshot"]
    )
    assert result.exit_code == 0, result.output
    assert fake_github == []
    assert json.loads(result.stdout) == snapshot
    assert ignored.read_text(encoding="utf-8").splitlines() == lines


def test_github_repo_does_not_ignore_url_accepted_under_another_repo_name(
    fake_github: list[str], monkeypatch: pytest.MonkeyPatch
):
    # A renamed repository listed under its old and new name: GitHub follows the rename,
    # so both entries yield the same archive URLs, but only the new name matches the host.
    monkeypatch.setattr(GithubPluginRepo, "_get_repos", lambda self: [(OWNER, "old-name"), (OWNER, REPO)])
    repo = GithubPluginRepo("token")

    plugins = repo.get_plugins()

    urls = {loc.url for p in plugins for locs in p.versions.values() for loc in locs}
    assert urls == {SRC_VALID, ASSET_VALID, ASSET_IGNORED}
    assert sorted(repo.newly_ignored_urls) == sorted([SRC_REJECTED, SRC_TAG_REJECTED, ASSET_REJECTED])
