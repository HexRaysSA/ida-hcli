# TODO: make all this async

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from hcli.lib.ida.plugin import (
    IDAPluginMetadata,
    get_metadata_from_plugin_archive,
    is_plugin_archive,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin, PluginVersion
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
    def from_dict(cls, data: Dict[str, Any]) -> "GitHubReleaseAsset":
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
    assets: List[GitHubReleaseAsset]

    @classmethod
    def from_dict(cls, data: Dict[str, Any], owner: str, repo: str) -> "GitHubRelease":
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


class GitHubCommit(BaseModel):
    commit_hash: str
    committed_date: str
    zipball_url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GitHubCommit":
        return cls(
            commit_hash=data["oid"],
            committed_date=data["committedDate"],
            zipball_url=data["zipballUrl"],
        )


class GitHubReleases(BaseModel):
    default_branch: GitHubCommit
    releases: list[GitHubRelease]


class GitHubGraphQLClient:
    """GitHub GraphQL API client"""

    def __init__(self, token: str):
        self.token = token
        self.api_url = "https://api.github.com/graphql"
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def query(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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

    def get_many_releases(self, repos: List[Tuple[str, str]], count: int = 10) -> Dict[Tuple[str, str], GitHubReleases]:
        """Fetch releases for multiple repositories in a single query"""
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
                                    }}
                                    ... on Tag {{
                                        target {{
                                            ... on Commit {{
                                                zipballUrl
                                                oid
                                            }}
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
            result[(owner, repo)] = GitHubReleases(
                default_branch=GitHubCommit.from_dict(repo_data["defaultBranchRef"]["target"]),
                releases=[GitHubRelease.from_dict(release_data, owner, repo) for release_data in releases_data],
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


def warm_releases_metadata_cache(client: GitHubGraphQLClient, repos: List[Tuple[str, str]]) -> None:
    """Warm the releases metadata cache for multiple repositories"""

    # TODO: add progress bar

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

    # Process in batches of 20
    batch_size = 20
    for i in range(0, len(repos_to_fetch), batch_size):
        batch = repos_to_fetch[i : i + batch_size]
        logging.debug(
            f"Processing batch {i // batch_size + 1}/{(len(repos_to_fetch) + batch_size - 1) // batch_size} ({len(batch)} repositories)"
        )

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


class HeadRequest(urllib.request.Request):
    def get_method(self) -> str:
        return "HEAD"


def fetch_http_resource_size(url: str) -> int:
    # Try Content-Length header first with HEAD request
    try:
        req = HeadRequest(url)
        with urllib.request.urlopen(req) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                return int(content_length)
    except Exception as e:
        logging.debug(f"HEAD request failed for {url}: {e}")

    # If HEAD fails, try Range request
    try:
        range_req = urllib.request.Request(url)
        range_req.add_header("Range", "bytes=0-0")
        with urllib.request.urlopen(range_req) as response:
            content_range = response.headers.get("Content-Range")
            if content_range:
                return int(content_range.split("/")[-1])
    except Exception as e:
        logging.debug(f"Range request failed for {url}: {e}")

    # If both fail, return 0 to indicate unknown size
    logging.debug(f"Could not determine size for {url}")
    return 0


def download_source_archive(zip_url: str) -> bytes:
    size = fetch_http_resource_size(zip_url)
    if size > 0 and size > MAX_DOWNLOAD_SIZE:
        raise ValueError(f"Source archive too large ({size}) - exceeds {MAX_DOWNLOAD_SIZE} limit")

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


def get_plugin_metadata(client: GitHubGraphQLClient, owner: str, repo: str, release_id: str) -> IDAPluginMetadata:
    """Extract IDA plugin metadata from a release, trying assets first then source archive"""

    release = get_release_metadata(client, owner, repo, release_id)

    for asset in release.assets:
        if asset.name.lower().endswith(".zip"):
            try:
                asset_data = get_release_asset(owner, repo, release_id, asset)
                return get_metadata_from_plugin_archive(asset_data)
            except (ValueError, KeyError) as e:
                logging.debug(f"No plugin metadata found in asset {asset.name}: {e}")
                continue

    if release.zipball_url:
        try:
            source_data = get_source_archive(owner, repo, release.commit_hash, release.zipball_url)
            return get_metadata_from_plugin_archive(source_data)
        except ValueError as e:
            logging.debug(f"No plugin metadata found in source archive: {e}")

    raise ValueError(f"No IDA plugin metadata found in release {release_id} for {owner}/{repo}")


INITIAL_REPOSITORIES = [
    "0rganizers/nmips",
    "0xdea/augur",
    "0xdea/haruspex",
    "0xdea/rhabdomancer",
    "0xgalz/virtuailor",
    "a1ext/labeless",
    "accenture/condstanta",
    "accenture/firmloader",
    "accenture/vulfi",
    "airbus-cert/comida",
    "airbus-cert/ttddbg",
    "airbus-cert/yagi",
    "airbuscyber/grap",
    "alexander-pick/shannon_modem_loader",
    "allthingsida/qscripts",
    "archercreat/ida_names",
    "argp/iboot64helper",
    "arizvisa/ida-minsc",
    "atredispartners/aidapal",
    "avast/retdec-idaplugin",
    "binarly-io/efixplorer",
    "binarly-io/idalib",
    "binarly-io/idapcode",
    "binsync/binsync",
    "bizarrus/assemport",
    "blackberry/pe_tree",
    "carlosgprado/jarvis",
    "cellebrite-labs/functioninliner",
    "cellebrite-labs/ida_kcpp",
    "cellebrite-labs/labsync",
    "cellebrite-labs/pacxplorer",
    "cemalgnlts/jside",
    "checkpointsw/karta",
    "cisco-talos/dyndataresolver",
    "cisco-talos/ghida",
    "coldzer0/ida-for-delphi",
    "danielplohmann/idascope",
    "danigargu/dereferencing",
    "danigargu/heap-viewer",
    "danigargu/idatropy",
    "danigargu/syms2elf",
    "david-lazar/idapatternsearch",
    "dead-null/idascope",
    "deadeert/ews",
    "dga-mi-ssi/yaco",
    "elastic/hexforge",
    "es3n1n/ida-wakatime-py",
    "eset/delphihelper",
    "eset/ipyida",
    "eternalklaus/refhunter",
    "felixber/findfunc",
    "fox-it/mkyara",
    "ga-ryo/idafuzzy",
    "gaasedelen/lighthouse",
    "gaasedelen/lucid",
    "gaasedelen/tenet",
    "gdelugre/ida-arm-system-highlight",
    "gerhart01/extract.hvcalls",
    "giladreich/ida_migrator",
    "google/bindiff",
    "google/binexport",
    "gsmk/hexagon",
    "harding-stardust/community_base",
    "harlamism/idaclu",
    "herosi/cto",
    "herosi/pyclassinformer",
    "hexrayssa/ida-terminal-plugin",
    "hyuunnn/hyara",
    "idarlingteam/idarling",
    "idkhidden/idashare",
    "igogo-x86/hexrayspytools",
    "illera88/ponce",
    "interruptlabs/heimdallr-ida",
    "iphelix/ida-patcher",
    "iphelix/ida-sploiter",
    "jendabenda/fingermatch",
    "jhftss/ida2obj",
    "jinmo/idapkg",
    "jinmo/ifred",
    "joxeankoret/diaphora",
    "joydo/d810",
    "junron/auto-enum",
    "justicerage/gepetto",
    "kasperskylab/hrtng",
    "keowu/swiftstringinspector",
    "keystone-engine/keypatch",
    "l4ys/idasignsrch",
    "l4ys/lazycross",
    "l4ys/lazyida",
    "lac-japan/ida_plugin_antidebugseeker",
    "mahaloz/decomp2dbg",
    "mandiant/capa",
    "mandiant/fidl",
    "mandiant/flare-emu",
    "mandiant/simplifygraph",
    "mandiant/xrefer",
    "matteyeux/ida-iboot-loader",
    "medigateio/ida_medigate",
    "merces/showcomments",
    "milankovo/hexinlay",
    "milankovo/instrlen",
    "milankovo/navcolor",
    "milankovo/yaravm",
    "mrexodia/ida-pro-mcp",
    "mxiris-reverse-engineering/ida-mcp-server",
    "naim94a/lumen",
    "nccgroup/idahunt",
    "oalabs/findyara-ida",
    "oalabs/hashdb",
    "oalabs/hashdb-ida",
    "patois/abyss",
    "patois/dsync",
    "patois/genmc",
    "patois/hexraystoolbox",
    "patois/hrdevhelper",
    "patois/idacyber",
    "patois/xray",
    "pgarba/switchidaproloader",
    "pwcuk-cto/smartjump",
    "qilingframework/qiling",
    "quarkslab/quokka",
    "ramikg/openwithida",
    "ramikg/search-from-ida",
    "ramikg/tdinfo-parser",
    "ramikg/uncertaintifier",
    "revengai/reai-ida",
    "reversedcodes/ida-rpc",
    "riskeco/syncreven",
    "s3rg0x/aimachdec",
    "secrary/idenlib",
    "seifreed/xrefgen",
    "singleghost2/ida-notepad-plus",
    "strazzere/golang_loader_assist",
    "synacktiv/bip",
    "terrynini/feelinglucky",
    "thalium/ida_kmdf",
    "thalium/symless",
    "theneonai/mnemocrypt",
    "therealdreg/ida_bochs_windows",
    "tkmru/idapm",
    "tmr232/brutal-ida",
    "tmr232/idabuddy",
    "ufwt/idadiscover",
    "unknown-cyber-inc/ida",
    "virustotal/vt-ida-plugin",
    "williballenthin/idawilli",
    "vlad902/findcrypt2-with-mmx",
    "voidsec/driverbuddyreloaded",
    "yoavst/graffiti",
    "yoavst/ida-ios-helper",
    "zerotypic/wilhelm",
]


class GithubPluginRepo(BasePluginRepo):
    def __init__(self, token: str):
        super().__init__()
        self.token = token
        self.client = GitHubGraphQLClient(token)

    def get_plugins(self) -> list[Plugin]:
        repos = [parse_repository(repo) for repo in INITIAL_REPOSITORIES]
        warm_releases_metadata_cache(self.client, repos)

        # TODO: for binary plugins, there are often different builds for each platform
        # so need to include that in the map, too.
        # TODO: also by IDA version
        # name -> version -> url
        index: dict[str, dict[str, str]] = defaultdict(dict)

        # the following should be pretty hot, given all the caching
        # TODO: refactor this ugly logic. collect URLs first, fetch next.
        # TODO: progress bar
        #
        for owner, repo in sorted(repos):
            releases = get_releases_metadata(self.client, owner, repo).releases
            for release in releases:
                for asset in release.assets:
                    # TODO: check file extension
                    try:
                        buf = get_release_asset(owner, repo, release.tag_name, asset)
                    except ValueError:
                        continue

                    if not is_plugin_archive(buf):
                        continue

                    try:
                        metadata = get_metadata_from_plugin_archive(buf)
                        validate_metadata_in_plugin_archive(buf)
                    except ValueError:
                        continue

                    try:
                        metadata = get_metadata_from_plugin_archive(buf)
                    except ValueError:
                        logger.debug(
                            "skipping invalid plugin archive: %s/%s %s %s %s",
                            owner,
                            repo,
                            release.tag_name,
                            asset.name,
                            asset.download_url,
                        )
                        continue
                    else:
                        # TODO: last write wins
                        logger.debug("found plugin: %s %s %s", metadata.name, metadata.version, release.zipball_url)
                        index[metadata.name][metadata.version] = asset.download_url

                # TODO: refactor this ugly logic
                try:
                    buf = get_source_archive(owner, repo, release.commit_hash, release.zipball_url)
                except ValueError:
                    pass
                else:
                    if is_plugin_archive(buf):
                        try:
                            metadata = get_metadata_from_plugin_archive(buf)
                            validate_metadata_in_plugin_archive(buf)
                        except ValueError:
                            pass
                        else:
                            # TODO: last write wins
                            logger.debug("found plugin: %s %s %s", metadata.name, metadata.version, release.zipball_url)
                            index[metadata.name][metadata.version] = release.zipball_url

        return [
            Plugin(name, [PluginVersion(version, url) for version, url in versions.items()])
            for name, versions in index.items()
        ]
