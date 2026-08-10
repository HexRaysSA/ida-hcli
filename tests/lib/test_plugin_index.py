import os
import shutil
import tempfile

import pytest

from hcli.lib.ida.plugin.repo.github import (
    GitHubGraphQLClient,
    get_release_asset,
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


@pytest.mark.parametrize(
    "key",
    [
        "",
        ".",
        "..",
        "tëst",
        "test\ttab",
        "test\nnewline",
        "test\rcarriage",
        "test/slash",
        "test\\backslash",
    ],
)
def test_get_cache_directory_rejects_invalid_path_keys(temp_hcli_cache_dir, key: str):
    with pytest.raises(ValueError):
        get_cache_directory(key)


def test_get_cache_directory_validates_every_component(temp_hcli_cache_dir):
    with pytest.raises(ValueError):
        get_cache_directory("valid", "", "alsovalid")

    cache_dir = get_cache_directory("valid", "path", "components")
    assert cache_dir.is_dir()


@pytest.mark.skipif(not os.getenv("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_github_release_metadata_and_asset(temp_hcli_cache_dir):
    """Smoke test of the live GitHub GraphQL API and the release-asset download path.

    airbus-cert/ttddbg v1.2.0 is an old, stable release used as a fixture.
    """
    client = GitHubGraphQLClient(os.environ["GITHUB_TOKEN"])

    owner, repo = parse_repository("airbus-cert/ttddbg")
    releases = client.get_releases(owner, repo).releases

    release = next(release for release in releases if release.tag_name == "v1.2.0")
    assert release.name == "SSTIC 2023 Release"

    asset = release.assets[0]
    buf = get_release_asset(owner, repo, "v1.2.0", asset)
    assert len(buf) == asset.size
