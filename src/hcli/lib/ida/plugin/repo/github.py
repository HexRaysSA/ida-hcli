import functools
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import rich.progress
from pydantic import BaseModel, ConfigDict, Field

from hcli.lib.console import stderr_console
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex
from hcli.lib.util.cache import get_cache_directory

logger = logging.getLogger(__name__)

# Maximum file size to download (100MB)
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024


class GitHubReleaseAsset(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    content_type: str = Field(alias="contentType")
    size: int
    download_url: str = Field(alias="downloadUrl")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubReleaseAsset":
        return cls.model_validate(data)


class GitHubRelease(BaseModel):
    name: str
    tag_name: str
    commit_hash: str
    created_at: str
    published_at: str
    is_prerelease: bool
    is_draft: bool
    url: str
    zipball_url: str
    assets: list[GitHubReleaseAsset]

    @classmethod
    def from_dict(cls, data: dict[str, Any], owner: str, repo: str) -> "GitHubRelease":
        assets_data = data.get("releaseAssets", {}).get("nodes", [])
        assets = [GitHubReleaseAsset.from_dict(asset) for asset in assets_data]

        # Extract tarball and zipball URLs and commit hash from tag target
        tag_name = data.get("tagName", "")
        zipball_url = ""
        commit_hash = ""

        target = data["tag"]["target"]

        # release is against a tag
        # otherwise release is against a commit
        if "target" in target:
            target = target["target"]

        zipball_url = target["zipballUrl"]
        commit_hash = target["oid"]

        return cls(
            name=data.get("name", "") or data.get("tagName", ""),
            tag_name=tag_name,
            created_at=data["createdAt"],
            published_at=data["publishedAt"],
            is_prerelease=data["isPrerelease"],
            is_draft=data["isDraft"],
            url=data["url"],
            assets=assets,
            zipball_url=zipball_url,
            commit_hash=commit_hash,
        )


class GitHubTag(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tag_name: str
    commit_hash: str
    zipball_url: str
    committed_date: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubTag":
        target = data["target"]
        if "target" in target:
            target = target["target"]

        return cls(
            tag_name=data["name"],
            commit_hash=target["oid"],
            zipball_url=target["zipballUrl"],
            committed_date=target["committedDate"],
        )


class GitHubCommit(BaseModel):
    commit_hash: str
    committed_date: str
    zipball_url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubCommit":
        return cls(
            commit_hash=data["oid"],
            committed_date=data["committedDate"],
            zipball_url=data["zipballUrl"],
        )


class GitHubReleases(BaseModel):
    default_branch: GitHubCommit
    releases: list[GitHubRelease]
    tags: list[GitHubTag]


class GitHubGraphQLClient:
    """GitHub GraphQL API client"""

    def __init__(self, token: str):
        self.token = token
        self.api_url = "https://api.github.com/graphql"
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL query"""
        data = {"query": query, "variables": variables or {}}

        req = urllib.request.Request(self.api_url, data=json.dumps(data).encode("utf-8"), headers=self.headers)

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))

                if "errors" in result:
                    raise Exception(f"GraphQL errors: {result['errors']}")

                return result["data"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise Exception(f"HTTP {e.code}: {error_body}")

    def get_many_releases(self, repos: list[tuple[str, str]], count: int = 10) -> dict[tuple[str, str], GitHubReleases]:
        """Fetch releases for multiple repositories in a single query

        Returns: mapping from (owner, repo) -> GitHubReleases
        """
        if not repos:
            return {}

        logging.info(f"Fetching releases from GitHub API for {len(repos)} repositories")

        # Build query with aliases
        query_parts = []
        variables = {"first": count}

        for i, (owner, repo) in enumerate(repos):
            alias = f"repo{i}"
            query_parts.append(f"""
                {alias}: repository(owner: "{owner}", name: "{repo}") {{
                    defaultBranchRef {{
                        target {{
                            ... on Commit {{
                                oid
                                zipballUrl
                                committedDate
                            }}
                        }}
                    }}
                    releases(first: $first, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
                        nodes {{
                            name
                            tagName
                            createdAt
                            publishedAt
                            isPrerelease
                            isDraft
                            url
                            releaseAssets(first: 50) {{
                                nodes {{
                                    name
                                    downloadUrl
                                    size
                                    contentType
                                }}
                            }}
                            tag {{
                                target {{
                                    ... on Commit {{
                                        zipballUrl
                                        oid
                                        committedDate
                                    }}
                                    ... on Tag {{
                                        target {{
                                            ... on Commit {{
                                                zipballUrl
                                                oid
                                                committedDate
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                    refs(refPrefix: "refs/tags/", first: 25, orderBy: {{field: TAG_COMMIT_DATE, direction: DESC}}) {{
                        nodes {{
                            name
                            target {{
                                ... on Commit {{
                                    zipballUrl
                                    oid
                                    committedDate
                                }}
                                ... on Tag {{
                                    target {{
                                        ... on Commit {{
                                            zipballUrl
                                            oid
                                            committedDate
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            """)

        query = f"""
        query($first: Int!) {{
            {"".join(query_parts)}
        }}
        """

        data = self.query(query, variables)

        result = {}
        for i, (owner, repo) in enumerate(repos):
            repo_data = data.get(f"repo{i}")

            if not repo_data:
                logging.warning(f"Repository {owner}/{repo} not found")
                continue

            releases_data = repo_data["releases"]["nodes"]
            tags_data = repo_data["refs"]["nodes"]
            result[(owner, repo)] = GitHubReleases(
                default_branch=GitHubCommit.from_dict(repo_data["defaultBranchRef"]["target"]),
                releases=[GitHubRelease.from_dict(release_data, owner, repo) for release_data in releases_data],
                tags=[GitHubTag.from_dict(tag_data) for tag_data in tags_data],
            )

        return result

    def get_releases(self, owner: str, repo: str, count: int = 10) -> GitHubReleases:
        key = (owner, repo)
        return self.get_many_releases([key])[key]


def parse_repository(repo_string: str) -> tuple[str, str]:
    """Parse repository string into owner and repo name"""
    if "/" not in repo_string:
        raise ValueError(f"Invalid repository format: {repo_string}. Expected format: owner/repo")

    parts = repo_string.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid repository format: {repo_string}. Expected format: owner/repo")

    return parts[0], parts[1]


def get_source_archive_cache_directory(owner: str, repo: str, commit_hash: str) -> Path:
    return get_cache_directory(owner, repo, "source-archives", commit_hash)


def get_release_asset_cache_directory(owner: str, repo: str, release_id: str) -> Path:
    return get_cache_directory(owner, repo, "release-assets", release_id)


def get_releases_metadata_cache_path(owner: str, repo: str) -> Path:
    return get_cache_directory(owner, repo) / "releases.json"


def set_releases_metadata_cache(owner: str, repo: str, releases: GitHubReleases) -> None:
    cache_path = get_releases_metadata_cache_path(owner, repo)
    releases_data = releases.model_dump()
    cache_path.write_text(json.dumps(releases_data, indent=2, sort_keys=True))
    logging.debug(f"Saved releases cache to: {cache_path}")


def get_releases_metadata_cache(owner: str, repo: str) -> GitHubReleases:
    cache_path = get_releases_metadata_cache_path(owner, repo)
    if not cache_path.exists():
        raise KeyError(f"No releases cache found for {owner}/{repo}")

    file_age = time.time() - cache_path.stat().st_mtime

    # release metadata cache expires after 24 hours
    # based on file modification time
    if file_age > 24 * 60 * 60:  # 24 hours
        logging.info(f"Cache expired for {owner}/{repo} releases metadata, removing file")
        cache_path.unlink()
        raise KeyError(f"Expired releases cache removed for {owner}/{repo}")

    releases_data = json.loads(cache_path.read_text())
    return GitHubReleases.model_validate(releases_data)


def warm_releases_metadata_cache(client: GitHubGraphQLClient, repos: list[tuple[str, str]]) -> None:
    """Warm the releases metadata cache for multiple repositories"""

    repos_to_fetch = []

    for owner, repo in repos:
        try:
            get_releases_metadata_cache(owner, repo)
        except KeyError:
            repos_to_fetch.append((owner, repo))

    if not repos_to_fetch:
        logging.debug("All repositories already cached")
        return

    logging.debug(f"Warming cache for {len(repos_to_fetch)} repositories")

    BATCH_SIZE = 10
    for i in rich.progress.track(
        range(0, len(repos_to_fetch), BATCH_SIZE), description="Warming cache", transient=True, console=stderr_console
    ):
        batch = repos_to_fetch[i : i + BATCH_SIZE]
        releases_batch = client.get_many_releases(batch)

        for (owner, repo), releases in releases_batch.items():
            set_releases_metadata_cache(owner, repo, releases)


def get_releases_metadata(client: GitHubGraphQLClient, owner: str, repo: str) -> GitHubReleases:
    try:
        return get_releases_metadata_cache(owner, repo)
    except KeyError:
        releases = client.get_releases(owner, repo)
        set_releases_metadata_cache(owner, repo, releases)
        return releases


def set_release_asset_cache(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset, buf: bytes):
    cache_path = get_release_asset_cache_directory(owner, repo, release_id)
    (cache_path / asset.name).write_bytes(buf)
    logging.debug(f"Asset {asset.name} cached for {owner}/{repo} release {release_id}")


def get_release_asset_cache(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset) -> bytes:
    cache_path = get_release_asset_cache_directory(owner, repo, release_id)
    asset_path = cache_path / asset.name
    if not asset_path.exists():
        raise KeyError(f"Asset {asset.name} not found in cache for {owner}/{repo} release {release_id}")

    logging.debug(f"Asset {asset.name} found in cache for {owner}/{repo} release {release_id}")
    return asset_path.read_bytes()


def download_release_asset(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset) -> bytes:
    if asset.size > MAX_DOWNLOAD_SIZE:
        raise ValueError(f"Asset {asset.name} exceeds {MAX_DOWNLOAD_SIZE} limit")

    logging.info(f"Downloading asset: {asset.name} ({asset.size}) from {asset.download_url}")
    req = urllib.request.Request(asset.download_url)
    # TODO: there are network-related exceptions possible here.
    with urllib.request.urlopen(req) as response:
        asset_data = response.read()

    logging.debug(f"Downloaded {len(asset_data)} bytes for asset {asset.name}")
    return asset_data


def get_release_asset(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset) -> bytes:
    try:
        return get_release_asset_cache(owner, repo, release_id, asset)
    except KeyError:
        buf = download_release_asset(owner, repo, release_id, asset)
        set_release_asset_cache(owner, repo, release_id, asset, buf)
        return buf


SOURCE_ARCHIVE_FILENAME = "source.zip"


def set_source_archive_cache(owner: str, repo: str, commit_hash: str, buf: bytes):
    cache_path = get_source_archive_cache_directory(owner, repo, commit_hash)
    (cache_path / SOURCE_ARCHIVE_FILENAME).write_bytes(buf)
    logging.debug(f"Source archive cached for {owner}/{repo}@{commit_hash[:8]}")


def get_source_archive_cache(owner: str, repo: str, commit_hash: str) -> bytes:
    cache_path = get_source_archive_cache_directory(owner, repo, commit_hash)
    archive_path = cache_path / SOURCE_ARCHIVE_FILENAME
    if not archive_path.exists():
        raise KeyError(f"Source archive not found in cache for {owner}/{repo}@{commit_hash[:8]}")

    logging.debug(f"Source archive found in cache for {owner}/{repo}@{commit_hash[:8]}")
    return archive_path.read_bytes()


def download_source_archive(zip_url: str) -> bytes:
    logging.info(f"Downloading source archive from {zip_url}")
    req = urllib.request.Request(zip_url)
    with urllib.request.urlopen(req) as response:
        buf = response.read()

    logging.debug(f"Downloaded {len(buf)} bytes from {zip_url}")
    return buf


def get_source_archive(owner: str, repo: str, commit_hash: str, zip_url: str) -> bytes:
    try:
        return get_source_archive_cache(owner, repo, commit_hash)
    except KeyError:
        buf = download_source_archive(zip_url)
        set_source_archive_cache(owner, repo, commit_hash, buf)
        return buf


def get_release_metadata(client: GitHubGraphQLClient, owner: str, repo: str, release_id: str) -> GitHubRelease:
    """Extract release metadata from a release"""
    for release in get_releases_metadata(client, owner, repo).releases:
        if release.tag_name == release_id:
            return release

    raise KeyError(f"Release {release_id} not found for {owner}/{repo}")


def get_candidate_github_repos_cache_path() -> Path:
    return get_cache_directory() / "candidate_repos.json"


def set_candidate_github_repos_cache(repos: list[str]) -> None:
    cache_path = get_candidate_github_repos_cache_path()
    cache_path.write_text(json.dumps(repos, indent=2, sort_keys=True))
    logging.debug(f"Saved candidate repos cache to: {cache_path}")


def get_candidate_github_repos_cache() -> list[str]:
    cache_path = get_candidate_github_repos_cache_path()
    if not cache_path.exists():
        raise KeyError("No candidate repos cache found")

    file_age = time.time() - cache_path.stat().st_mtime

    # release metadata cache expires after 24 hours
    # based on file modification time
    if file_age > 24 * 60 * 60:  # 24 hours
        logging.info("Cache expired for candidate repos, removing file")
        cache_path.unlink()
        raise KeyError("Expired candidate repos cache")

    return json.loads(cache_path.read_text())


def find_github_repos_with_plugins(token: str) -> list[str]:
    """Find GitHub repositories that contain ida-plugin.json files using GitHub's search API.

    Returns:
        List of repositories in "owner/repo" format
    """

    # Note: Forks with fewer stars than the parent repository or no commits are not indexed for code search.
    # via: https://docs.github.com/en/search-github/searching-on-github/searching-code
    queries = [
        "filename:ida-plugin.json",
        "filename:ida-plugin.json fork:true",
    ]

    repos = set()
    for query in queries:
        search_url = "https://api.github.com/search/code"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ida-hcli",
        }

        page = 1
        while True:
            params = f"q={urllib.parse.quote(query)}&per_page=100&page={page}"
            url = f"{search_url}?{params}"

            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req) as response:
                    result = json.loads(response.read().decode("utf-8"))

                    items = result.get("items", [])
                    if not items:
                        break

                    for item in items:
                        repo_full_name = item["repository"]["full_name"]
                        repos.add(repo_full_name)

                    if len(items) < 100:
                        break

                    page += 1
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8")
                logger.warning(f"GitHub search API error on page {page}: HTTP {e.code}: {error_body}")
                break
            except Exception as e:
                logger.warning(f"Failed to search GitHub repositories on page {page}: {e}")
                break

    return sorted(list(repos))


class GithubPluginRepo(BasePluginRepo):
    def __init__(self, token: str, extra_repos: list[str] | None = None):
        super().__init__()
        self.token = token
        self.extra_repos = extra_repos or []
        self.client = GitHubGraphQLClient(token)

        warm_releases_metadata_cache(self.client, self._get_repos())
        _ = self._get_repos()

    def _get_repos(self):
        try:
            repos = get_candidate_github_repos_cache()
        except KeyError:
            repos = find_github_repos_with_plugins(self.token)
            set_candidate_github_repos_cache(repos)

        return [parse_repository(repo) for repo in sorted(set(repos + self.extra_repos))]

    @functools.cache
    def get_plugins(self) -> list[Plugin]:
        repos = self._get_repos()

        assets = []
        source_archives = []

        # first collect all the URLs
        # then fetch them in a second loop
        # so that we can have a meaningful progress bar.

        for owner, repo in sorted(repos):
            md = get_releases_metadata(self.client, owner, repo)
            seen_zipball_urls = set()
            for release in md.releases:
                if release.published_at < "2025-09-01":
                    logger.debug(
                        "skipping old release: %s/%s %s on %s", owner, repo, release.tag_name, release.published_at
                    )
                    continue

                # source archives
                source_archives.append((owner, repo, release.commit_hash, release.zipball_url))
                seen_zipball_urls.add(release.zipball_url)

                # assets (distribution/binary archives)
                for asset in release.assets:
                    if asset.content_type != "application/zip":
                        continue

                    assets.append((owner, repo, release.tag_name, asset))

            for tag in md.tags:
                if not tag.tag_name.startswith("v"):
                    continue

                if tag.committed_date < "2025-09-01":
                    logger.debug("skipping old tag: %s/%s %s on %s", owner, repo, tag.tag_name, tag.committed_date)
                    continue

                logger.debug("found tag: %s/%s %s", owner, repo, tag.tag_name)

                if tag.zipball_url in seen_zipball_urls:
                    logger.debug("already found URL for tag: %s/%s %s: %s", owner, repo, tag.tag_name, tag.zipball_url)
                else:
                    source_archives.append((owner, repo, tag.commit_hash, tag.zipball_url))
                    seen_zipball_urls.add(tag.zipball_url)

        index = PluginArchiveIndex()

        for owner, repo, tag_name, asset in rich.progress.track(
            assets, description="Fetching plugin assests", transient=True, console=stderr_console
        ):
            try:
                buf = get_release_asset(owner, repo, tag_name, asset)
            except ValueError:
                continue

            host_url = f"https://github.com/{owner}/{repo}"
            index.index_plugin_archive(buf, asset.download_url, expected_host=host_url)

        for owner, repo, commit_hash, url in rich.progress.track(
            source_archives, description="Fetching plugin source archives", transient=True, console=stderr_console
        ):
            try:
                buf = get_source_archive(owner, repo, commit_hash, url)
            except ValueError:
                continue

            host_url = f"https://github.com/{owner}/{repo}"
            index.index_plugin_archive(buf, url, expected_host=host_url)

        return index.get_plugins()
