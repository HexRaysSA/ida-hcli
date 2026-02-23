import functools
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import requests
import rich.progress
from pydantic import BaseModel, ConfigDict, Field
from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base

from hcli.lib.console import stderr_console
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginArchiveIndex
from hcli.lib.util.cache import get_cache_directory
from hcli.lib.util.logging import m

logger = logging.getLogger(__name__)

# Maximum file size to download (100MB)
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024

GITHUB_API_URL = "https://api.github.com"


def is_github_url(url: str) -> bool:
    """Check if URL is a GitHub repository URL."""
    return "github.com/" in url or url.startswith("git@github.com:")


def parse_github_url(url: str) -> tuple[str, str, str | None]:
    """Parse GitHub URL into (owner, repo, tag).

    Supports:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo@tag
    - git@github.com:owner/repo.git
    - git@github.com:owner/repo.git@tag
    """
    # Extract optional @tag suffix
    tag: str | None = None
    if "@" in url:
        # Handle git@ prefix separately
        if url.startswith("git@"):
            # git@github.com:owner/repo.git@tag
            parts = url.split(
                "@", 2
            )  # ['git', 'github.com:owner/repo.git', 'tag'] or ['git', 'github.com:owner/repo.git']
            if len(parts) == 3:
                tag = parts[2]
                url = f"git@{parts[1]}"
        else:
            # https://github.com/owner/repo@tag
            url, tag = url.rsplit("@", 1)

    # Parse the URL to get owner/repo
    if url.startswith("git@"):
        # git@github.com:owner/repo.git
        match = re.match(r"git@github\.com:([^/]+)/(.+?)(?:\.git)?$", url)
        if not match:
            raise ValueError(f"Invalid GitHub SSH URL: {url}")
        owner, repo = match.groups()
    else:
        # https://github.com/owner/repo(.git)
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lstrip("/")
        path = path.removesuffix(".git")
        parts = path.split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {url}")
        owner, repo = parts[0], parts[1]

    return owner, repo, tag


def fetch_github_release_zip_asset(owner: str, repo: str, tag: str | None = None) -> bytes:
    """Fetch .zip asset from GitHub release using REST API.

    Args:
        owner: Repository owner
        repo: Repository name
        tag: Release tag (e.g., "v1.0.0"). If None, fetches latest release.

    Returns:
        The .zip asset bytes.

    Raises:
        ValueError: If no .zip asset or multiple .zip assets found.
        requests.RequestException: If API request fails.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}

    # Fetch release metadata
    if tag:
        release_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/releases/tags/{tag}"
    else:
        release_url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/releases/latest"

    logger.info(f"fetching release from {release_url}")
    response = requests.get(release_url, headers=headers, timeout=30.0)
    response.raise_for_status()
    release_data = response.json()

    # Find .zip assets
    assets = release_data.get("assets", [])
    zip_assets = [a for a in assets if a.get("name", "").lower().endswith(".zip")]

    if not zip_assets:
        tag_info = f" ({tag})" if tag else " (latest)"
        raise ValueError(f"No .zip asset found in release{tag_info} for {owner}/{repo}")

    if len(zip_assets) > 1:
        asset_names = [a["name"] for a in zip_assets]
        raise ValueError(
            f"Multiple .zip assets found in release: {', '.join(asset_names)}. Cannot determine which to install."
        )

    asset = zip_assets[0]
    asset_name = asset["name"]
    asset_size = asset.get("size", 0)
    download_url = asset["browser_download_url"]

    if asset_size > MAX_DOWNLOAD_SIZE:
        raise ValueError(
            f"Asset {asset_name} ({asset_size} bytes) exceeds maximum size limit ({MAX_DOWNLOAD_SIZE} bytes)"
        )

    logger.info(f"downloading asset: {asset_name} ({asset_size} bytes) from {download_url}")
    response = requests.get(download_url, timeout=60.0)
    response.raise_for_status()

    return response.content


class WaitGitHubRateLimit(wait_base):
    """Custom wait strategy that respects GitHub's rate limit headers.

    GitHub provides these headers:
    - retry-after: seconds to wait before retrying
    - x-ratelimit-reset: UTC epoch timestamp when limit resets
    - x-ratelimit-remaining: requests remaining in current window

    If retry-after is present, use that.
    Otherwise, if x-ratelimit-remaining is 0, wait until x-ratelimit-reset.
    Otherwise, use exponential backoff with a minimum of 60 seconds.
    """

    def __init__(self, min_wait: int = 60, max_wait: int = 3600):
        self.min_wait = min_wait
        self.max_wait = max_wait

    def __call__(self, retry_state: RetryCallState) -> float:
        if retry_state.outcome and retry_state.outcome.failed:
            exception = retry_state.outcome.exception()
            if isinstance(exception, urllib.error.HTTPError):
                logger.debug(f"Rate limit headers received: {dict(exception.headers)}")

                retry_after = exception.headers.get("retry-after") or exception.headers.get("Retry-After")
                if retry_after:
                    retry_after_seconds = max(int(retry_after), self.min_wait)
                    logger.info(f"GitHub rate limit hit, respecting retry-after: {retry_after_seconds}s")
                    return min(retry_after_seconds, self.max_wait)

                remaining_str = exception.headers.get("x-ratelimit-remaining") or exception.headers.get(
                    "X-RateLimit-Remaining"
                )
                reset_time_str = exception.headers.get("x-ratelimit-reset") or exception.headers.get(
                    "X-RateLimit-Reset"
                )

                if reset_time_str:
                    reset_time = int(reset_time_str)
                    current_time = time.time()
                    raw_wait_time = reset_time - current_time

                    logger.debug(
                        f"Rate limit calculation: reset={reset_time}, current={current_time:.0f}, "
                        f"raw_wait={raw_wait_time:.0f}s, remaining={remaining_str}"
                    )

                    if raw_wait_time < 0:
                        logger.warning(
                            f"Clock skew detected: reset time {reset_time} is in the past "
                            f"(current time: {current_time:.0f}), using minimum wait"
                        )
                        wait_time = float(self.min_wait)
                    elif raw_wait_time > self.max_wait:
                        logger.warning(
                            f"Calculated wait time {raw_wait_time:.0f}s exceeds maximum {self.max_wait}s, "
                            f"capping to maximum"
                        )
                        wait_time = float(self.max_wait)
                    else:
                        wait_time = max(raw_wait_time, float(self.min_wait))

                    logger.info(f"GitHub rate limit exhausted, waiting until reset: {wait_time:.0f}s")
                    return wait_time

        attempt = retry_state.attempt_number
        exponential_wait = min(self.min_wait * (2 ** (attempt - 1)), self.max_wait)
        logger.info(f"GitHub rate limit hit, using exponential backoff: {exponential_wait:.0f}s (attempt {attempt})")
        return exponential_wait


def _is_rate_limit_error(exception: BaseException) -> bool:
    """Check if exception is a GitHub rate limit error (403 or 429)."""
    return isinstance(exception, urllib.error.HTTPError) and exception.code in (403, 429)


def _check_and_handle_proactive_rate_limit(response) -> None:
    """Check response headers and proactively wait if rate limit is nearly exhausted."""
    remaining_str = response.headers.get("x-ratelimit-remaining") or response.headers.get("X-RateLimit-Remaining")
    reset_time_str = response.headers.get("x-ratelimit-reset") or response.headers.get("X-RateLimit-Reset")

    if remaining_str and reset_time_str:
        remaining = int(remaining_str)
        if remaining <= 2:
            reset_time = int(reset_time_str)
            current_time = time.time()
            wait_time = max(reset_time - current_time, 30)

            if 0 < wait_time < 3600:
                logger.warning(
                    f"Proactive rate limit: only {remaining} requests remaining, "
                    f"waiting {wait_time:.0f}s until reset to avoid exhaustion"
                )
                time.sleep(wait_time)


@retry(
    retry=retry_if_exception(_is_rate_limit_error),
    wait=WaitGitHubRateLimit(min_wait=60, max_wait=3600),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _urlopen_with_retry(req: urllib.request.Request):
    """Wrapper around urllib.request.urlopen with GitHub rate limit retry logic."""
    response = urllib.request.urlopen(req)
    _check_and_handle_proactive_rate_limit(response)
    return response


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
            with _urlopen_with_retry(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                errors = result.get("errors", [])
                if any(e.get("type") != "NOT_FOUND" for e in errors):
                    raise RuntimeError(f"GraphQL errors: {[e for e in errors if e.get('type') != 'NOT_FOUND']}")
                # GitHub returns partial data alongside NOT_FOUND errors for deleted/renamed repos, skip them.
                for e in errors:
                    logger.warning("GitHub GraphQL NOT_FOUND (repo deleted/renamed): %s", e.get("message"))
                return result["data"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8")
            raise RuntimeError(f"HTTP {e.code}: {error_body}")

    def get_many_releases(self, repos: list[tuple[str, str]], count: int = 10) -> dict[tuple[str, str], GitHubReleases]:
        """Fetch releases for multiple repositories in a single query

        Returns: mapping from (owner, repo) -> GitHubReleases
        """
        if not repos:
            return {}

        logger.info(f"fetching releases from GitHub API for {len(repos)} repositories")

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

            if not repo_data or not repo_data.get("defaultBranchRef"):
                logger.warning(f"Repository {owner}/{repo} not found")
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
        data = self.get_many_releases([key], count=count)
        if key not in data:
            raise KeyError(f"Repository {owner}/{repo} not found")
        return data[key]


def parse_repository(repo_string: str) -> tuple[str, str]:
    """Parse repository string into owner and repo name"""
    if "/" not in repo_string:
        raise ValueError(f"invalid repository format: {repo_string}. Expected format: owner/repo")

    parts = repo_string.split("/")
    if len(parts) != 2:
        raise ValueError(f"invalid repository format: {repo_string}. Expected format: owner/repo")

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
    cache_path.write_text(json.dumps(releases_data, indent=2, sort_keys=True), encoding="utf-8")
    logger.debug(f"saved releases cache to: {cache_path}")


def get_releases_metadata_cache(owner: str, repo: str) -> GitHubReleases:
    cache_path = get_releases_metadata_cache_path(owner, repo)
    if not cache_path.exists():
        raise KeyError(f"no releases cache found for {owner}/{repo}")

    file_age = time.time() - cache_path.stat().st_mtime

    # release metadata cache expires after 24 hours
    # based on file modification time
    if file_age > 24 * 60 * 60:  # 24 hours
        logger.info(f"cache expired for {owner}/{repo} releases metadata, removing file")
        cache_path.unlink()
        raise KeyError(f"expired releases cache removed for {owner}/{repo}")

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
        logger.debug("all repositories already cached")
        return

    logger.debug(f"warming cache for {len(repos_to_fetch)} repositories")

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
    logger.debug(f"asset {asset.name} cached for {owner}/{repo} release {release_id}")


def get_release_asset_cache(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset) -> bytes:
    cache_path = get_release_asset_cache_directory(owner, repo, release_id)
    asset_path = cache_path / asset.name
    if not asset_path.exists():
        raise KeyError(f"asset {asset.name} not found in cache for {owner}/{repo} release {release_id}")

    logger.debug(f"asset {asset.name} found in cache for {owner}/{repo} release {release_id}")
    return asset_path.read_bytes()


def download_release_asset(owner: str, repo: str, release_id: str, asset: GitHubReleaseAsset) -> bytes:
    if asset.size > MAX_DOWNLOAD_SIZE:
        raise ValueError(f"asset {asset.name} exceeds {MAX_DOWNLOAD_SIZE} limit")

    logger.info(f"downloading asset: {asset.name} ({asset.size}) from {asset.download_url}")
    req = urllib.request.Request(asset.download_url)
    with _urlopen_with_retry(req) as response:
        asset_data = response.read()

    logger.debug(f"downloaded {len(asset_data)} bytes for asset {asset.name}")
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
    logger.debug(f"Source archive cached for {owner}/{repo}@{commit_hash[:8]}")


def get_source_archive_cache(owner: str, repo: str, commit_hash: str) -> bytes:
    cache_path = get_source_archive_cache_directory(owner, repo, commit_hash)
    archive_path = cache_path / SOURCE_ARCHIVE_FILENAME
    if not archive_path.exists():
        raise KeyError(f"source archive not found in cache for {owner}/{repo}@{commit_hash[:8]}")

    logger.debug(f"source archive found in cache for {owner}/{repo}@{commit_hash[:8]}")
    return archive_path.read_bytes()


def download_source_archive(zip_url: str) -> bytes:
    logger.info(f"downloading source archive from {zip_url}")
    req = urllib.request.Request(zip_url)
    with _urlopen_with_retry(req) as response:
        buf = response.read()

    logger.debug(f"downloaded {len(buf)} bytes from {zip_url}")
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

    raise KeyError(f"release {release_id} not found for {owner}/{repo}")


def get_candidate_github_repos_cache_path() -> Path:
    return get_cache_directory() / "candidate_repos.json"


def set_candidate_github_repos_cache(repos: list[str]) -> None:
    cache_path = get_candidate_github_repos_cache_path()
    cache_path.write_text(json.dumps(repos, indent=2, sort_keys=True), encoding="utf-8")
    logger.debug(f"Saved candidate repos cache to: {cache_path}")


def get_candidate_github_repos_cache() -> list[str]:
    cache_path = get_candidate_github_repos_cache_path()
    if not cache_path.exists():
        raise KeyError("no candidate repos cache found")

    file_age = time.time() - cache_path.stat().st_mtime

    # release metadata cache expires after 24 hours
    # based on file modification time
    if file_age > 24 * 60 * 60:  # 24 hours
        logger.info("cache expired for candidate repos, removing file")
        cache_path.unlink()
        raise KeyError("expired candidate repos cache")

    return json.loads(cache_path.read_text())


def find_github_repos_with_plugins(token: str) -> list[str]:
    """Find GitHub repositories that contain ida-plugin.json files using GitHub's search API.

    Returns:
        List of repositories in "owner/repo" format
    """
    BATCH_SIZE = 25

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
            params = f"q={urllib.parse.quote(query)}&per_page={BATCH_SIZE}&page={page}"
            url = f"{search_url}?{params}"

            req = urllib.request.Request(url, headers=headers)
            with _urlopen_with_retry(req) as response:
                result = json.loads(response.read().decode("utf-8"))

                items = result.get("items", [])
                if not items:
                    break

                for item in items:
                    repo_full_name = item["repository"]["full_name"]
                    repos.add(repo_full_name)
                    logger.debug(
                        m(
                            "found repository via GitHub search: %s",
                            repo_full_name,
                            query=query,
                            page=page,
                            name=repo_full_name,
                        )
                    )

                if len(items) < BATCH_SIZE:
                    break

                page += 1

    return sorted(repos)


class GithubPluginRepo(BasePluginRepo):
    def __init__(self, token: str, extra_repos: list[str] | None = None, ignored_repos: list[str] | None = None):
        super().__init__()
        self.token = token
        self.extra_repos = set(extra_repos or [])
        self.ignored_repos = set(ignored_repos or [])
        self.client = GitHubGraphQLClient(token)

        # warm cache
        self._repos = self._get_repos()

        warm_releases_metadata_cache(self.client, self._repos)

    def _get_repos(self):
        try:
            repos = set(get_candidate_github_repos_cache())
        except KeyError:
            repos = set(find_github_repos_with_plugins(self.token))
            repos = {r.lower() for r in repos}
            set_candidate_github_repos_cache(sorted(repos))
        else:
            repos = {r.lower() for r in repos}

        extra = {r.lower() for r in self.extra_repos}
        ignored = {r.lower() for r in self.ignored_repos}

        for repo in sorted(extra & repos):
            logger.debug("extra repo already found by GitHub index: %s", repo)
        for repo in sorted(extra - repos):
            logger.debug("extra repo not yet found by GitHub index: %s", repo)
        for repo in sorted(repos - extra):
            logger.debug("GitHub repo not in extra repo list: %s", repo)
        repos |= extra

        for repo in sorted(ignored & repos):
            logger.debug("ignoring found repo: %s", repo)
        repos -= ignored

        return [parse_repository(repo) for repo in sorted(repos)]

    @functools.cache  # noqa: B019 - instance method caching is intentional; repo is long-lived
    def get_plugins(self) -> list[Plugin]:
        assets = []
        source_archives = []

        # first collect all the URLs
        # then fetch them in a second loop
        # so that we can have a meaningful progress bar.

        for owner, repo in sorted(self._repos):
            logger.debug("finding plugins in repo: %s/%s", owner, repo)

            try:
                md = get_releases_metadata(self.client, owner, repo)
            except KeyError as e:
                logger.warning("Skipping repository %s/%s: %s", owner, repo, e)
                continue
            seen_zipball_urls = set()
            for release in md.releases:
                context = {
                    "owner": owner,
                    "repo": repo,
                    "release": release.tag_name,
                    "date": release.published_at,
                }
                logger.debug(m("considering release: %s", release.tag_name, **context))

                if release.published_at < "2025-09-01":
                    logger.debug(
                        m(
                            "skipping old release: %s < 2025-09-01",
                            release.published_at,
                            **context,
                        )
                    )
                    continue

                logger.debug(m("found release: %s", release.tag_name, **context))

                # source archives
                source_archives.append((owner, repo, release.commit_hash, release.zipball_url, release.published_at))
                seen_zipball_urls.add(release.zipball_url)
                logger.debug(m("found zipball URL: %s", release.zipball_url, **context))

                # assets (distribution/binary archives)
                for asset in release.assets:
                    if asset.content_type not in {"application/zip", "application/x-zip-compressed", "raw"}:
                        # GitHub provides various mimetypes for manually attached ZIP archives, including:
                        # - https://github.com/binsync/binsync/releases/download/v5.10.1/binsync-ida-plugin.zip (raw)
                        # - https://github.com/arkup/tc_deer/releases/download/0.1.0/tc_deer_ida_plugin010.zip (application/x-zip-compressed)
                        # so we loosen the content type restrictions, but enforce filename ending with .zip below
                        logger.debug(
                            m(
                                "skipping asset with type: %s",
                                asset.content_type,
                                **dict(context, asset=asset.name),
                            )
                        )
                        continue

                    if not asset.name.lower().endswith(".zip"):
                        logger.debug(
                            m(
                                "skipping asset with name: %s",
                                asset.name,
                                **dict(context, asset=asset.name),
                            )
                        )
                        continue

                    assets.append((owner, repo, release.tag_name, asset, release.published_at))
                    logger.debug(
                        m(
                            "found zip asset: %s",
                            asset.download_url,
                            **dict(context, asset=asset.name),
                        )
                    )

            for tag in md.tags:
                context = {
                    "owner": owner,
                    "repo": repo,
                    "tag": tag.tag_name,
                    "date": tag.committed_date,
                }

                logger.debug(m("considering tag: %s", tag.tag_name, **context))

                if not tag.tag_name.startswith("v"):
                    logger.debug(m("skipping non-v* tag: %s", tag.tag_name, **context))
                    continue

                if tag.committed_date < "2025-09-01":
                    logger.debug(
                        m(
                            "skipping old tag: %s < 2025-09-01",
                            tag.committed_date,
                            **context,
                        )
                    )
                    continue

                logger.debug(m("found tag: %s", tag.tag_name, **context))

                if tag.zipball_url in seen_zipball_urls:
                    logger.debug(m("already found URL for tag: %s", tag.zipball_url, **context))
                else:
                    source_archives.append((owner, repo, tag.commit_hash, tag.zipball_url, tag.committed_date))
                    seen_zipball_urls.add(tag.zipball_url)
                    logger.debug(m("found zipball URL: %s", tag.zipball_url, **context))

        index = PluginArchiveIndex()

        for owner, repo, tag_name, asset, date in rich.progress.track(
            assets, description="Fetching plugin assests", transient=True, console=stderr_console
        ):
            logger.debug(m("fetching release asset: %s", asset.download_url, owner=owner, repo=repo, tag=tag_name))
            try:
                buf = get_release_asset(owner, repo, tag_name, asset)
            except ValueError:
                continue

            host_url = f"https://github.com/{owner}/{repo}"
            index.index_plugin_archive(
                buf,
                asset.download_url,
                expected_host=host_url,
                context={
                    "owner": owner,
                    "repo": repo,
                    "type": "release asset",
                    "tag": tag_name,
                    "url": asset.download_url,
                    "date": date,
                },
            )

        for owner, repo, commit_hash, url, date in rich.progress.track(
            source_archives, description="Fetching plugin source archives", transient=True, console=stderr_console
        ):
            logger.debug(m("fetching source archive: %s", url, owner=owner, repo=repo, commit=commit_hash))
            try:
                buf = get_source_archive(owner, repo, commit_hash, url)
            except ValueError:
                continue

            host_url = f"https://github.com/{owner}/{repo}"
            index.index_plugin_archive(
                buf,
                url,
                expected_host=host_url,
                context={
                    "owner": owner,
                    "repo": repo,
                    "type": "source archive",
                    "commit": commit_hash,
                    "url": url,
                    "date": date,
                },
            )

        return index.get_plugins()
