from pathlib import Path

from fixtures import temp_env_var

from hcli.lib.ida.plugin.repo.github import (
    PLUGINS_CACHE_KEY,
    get_release_asset_cache_directory,
    get_releases_metadata_cache_path,
    get_source_archive_cache,
    set_candidate_github_repos_cache,
    set_source_archive_cache,
)


def test_plugin_repository_cache_stays_under_plugins_directory(tmp_path: Path):
    with temp_env_var("HCLI_CACHE_DIR", str(tmp_path)):
        set_source_archive_cache("owner", "repo", "0" * 40, b"archive")
        get_release_asset_cache_directory("owner", "repo", "1")
        get_releases_metadata_cache_path("owner", "repo").write_text("{}", encoding="utf-8")
        set_candidate_github_repos_cache(["owner/repo"])

        assert get_source_archive_cache("owner", "repo", "0" * 40) == b"archive"

    assert [p.name for p in tmp_path.iterdir()] == [PLUGINS_CACHE_KEY]
    plugins = tmp_path / PLUGINS_CACHE_KEY
    assert sorted(p.name for p in plugins.iterdir()) == ["candidate_repos.json", "owner"]
    assert sorted(p.name for p in (plugins / "owner" / "repo").iterdir()) == [
        "release-assets",
        "releases.json",
        "source-archives",
    ]
