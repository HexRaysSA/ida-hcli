import dataclasses
import errno
import itertools
import json
import logging
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

import httpx
from semantic_version import SimpleSpec, Version

from hcli.env import ENV
from hcli.lib.util.io import NoSpaceError, check_free_space


@dataclasses.dataclass
class GitHubRepo:
    user: str
    repo: str
    token: str = ""

    @classmethod
    def from_url(cls, url: str, token: str = "") -> "GitHubRepo":
        """
        Create a GitHubRepo from a URL like:
        - https://github.com/user/repo
        - git@github.com:user/repo.git
        """
        if url.startswith("git@"):  # SSH style
            # e.g. git@github.com:user/repo.git
            path = url.split(":", 1)[1]
        else:
            # e.g. https://github.com/user/repo(.git)
            parsed = urlparse(url)
            path = parsed.path.lstrip("/")

        # Remove optional `.git` suffix
        path = path.removesuffix(".git")

        # Split into user/repo
        try:
            user, repo = path.split("/", 1)
        except ValueError:
            raise ValueError(f"Invalid GitHub URL: {url}")

        return cls(user=user, repo=repo, token=token)


@dataclasses.dataclass
class ReleaseAsset:
    asset_id: int
    name: str
    size: int

    @property
    def is_valid(self):
        return not (
            self.name is None
            or not self.name.strip(" ")
            or self.asset_id is None
            or self.asset_id <= 0
            or self.size is None
            or self.size <= 0
        )


class AuthSession:
    header: ClassVar[dict[str, str]] = {}

    @classmethod
    def init(cls, repo: GitHubRepo):
        if cls.header or not repo.token:
            return
        cls.header = {"Authorization": f"Bearer {repo.token}"}


def get_compatible_version(repo: GitHubRepo, compatibility_spec: SimpleSpec, include_dev: bool = False):
    all_versions = get_available_versions(repo)

    # Filter out dev versions if include_dev is False
    if not include_dev:
        filtered_versions = []
        for version in all_versions:
            tag_name = getattr(version, "_origin_tag_name", str(version))
            if not is_dev_version(tag_name):
                filtered_versions.append(version)
        all_versions = filtered_versions

    versions = sorted(compatibility_spec.filter(all_versions))[-10:]
    if not versions:
        return
    logging.info(f"Available versions: {tuple(map(str, versions))}")
    return versions[-1]


def is_dev_version(version_string: str) -> bool:
    """Check if a version string contains development indicators"""
    dev_indicators = ["dev", "alpha", "beta", "rc", "pre", "snapshot", "nightly"]
    version_lower = version_string.lower()
    return any(indicator in version_lower for indicator in dev_indicators)


def download_asset(
    repo: GitHubRepo,
    asset: ReleaseAsset,
    out_dir=Path(),
    block_size=2**20,
    callback: Callable[[int, int], None] = lambda *_: None,
):
    logging.info(f"Start downloading asset: '{asset.name}'")
    if out_dir.is_file():
        out_dir = out_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Construct GitHub API URL for asset download
    asset_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases/assets/{asset.asset_id}"

    # Set proper headers for asset download
    headers = AuthSession.header.copy()
    headers["Accept"] = "application/octet-stream"

    check_free_space(out_dir, asset.size)

    out_path = out_dir.joinpath(asset.name)
    try:
        with (
            httpx.stream("GET", asset_url, headers=headers, follow_redirects=True) as response,
            open(out_path, "wb") as file,
        ):
            if response.status_code != 200:
                raise RuntimeError(
                    f"Unexpected HTTP status {response.status_code} downloading {asset.name} from {asset_url}"
                )
            for i, data in enumerate(response.iter_bytes(block_size)):
                file.write(data)
                callback(i * block_size, asset.size)
            callback(asset.size, asset.size)

        # Guards update_asset against overwriting the running binary with a truncated/empty download.
        actual_size = out_path.stat().st_size
        if actual_size != asset.size:
            raise RuntimeError(f"Downloaded {asset.name} has size {actual_size} bytes, expected {asset.size} bytes")
    except Exception as e:
        out_path.unlink(missing_ok=True)
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            raise NoSpaceError(out_dir) from e
        raise


def get_available_versions(repo: GitHubRepo, process_tag: Callable[[str], Version | None] | None = None):
    if process_tag is None:
        process_tag = parse_tag
    logging.info(f"Searching for releases in 'https://github.com/{repo.user}/{repo.repo}/'...")
    request_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases"
    page_size = 100
    for i in itertools.count(1):
        data = json.loads(
            httpx.get(request_url, params={"page": i, "per_page": page_size}, headers=AuthSession.header).text
        )
        if "message" in data or not isinstance(data, list):
            break
        for release in data:
            tag_name = release.get("tag_name")
            if tag_name is None:
                continue
            version = process_tag(tag_name)
            if version is None:
                continue
            version._origin_tag_name = tag_name
            yield version
        logging.info(f"Version's page#{i} loaded")
        if len(data) < page_size:
            logging.info("No more pages")
            break


def parse_tag(tag_name: str) -> Version | None:
    try:
        return Version(tag_name.lstrip("v").strip())
    except ValueError:
        return None


def get_assets(repo: GitHubRepo, tag_name: str, assets_mask=re.compile(".*")):
    logging.info(f"Searching for assets by tag '{tag_name}' and mask: '{assets_mask.pattern}'")
    request_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases/tags/{tag_name}"
    data = json.loads(httpx.get(request_url, headers=AuthSession.header).text)
    if "message" in data:
        return []
    assets = data.get("assets")
    if not assets:
        return []
    assets = (
        ReleaseAsset(
            asset.get("id"),
            asset.get("name"),
            asset.get("size"),
        )
        for asset in assets
    )
    return tuple(asset for asset in assets if asset.is_valid and assets_mask.match(asset.name) is not None)


def update_asset(repo: GitHubRepo, asset: ReleaseAsset, binary_path: Path) -> None:
    """Download an asset and replace the running binary.

    Raises:
        ValueError: when `asset` is not a valid `ReleaseAsset`.
        FileNotFoundError: when `binary_path` does not exist.
        RuntimeError: when the download fails (non-200, size mismatch, etc.).
        OSError: when the temporary file cannot be moved into place.
    """
    if not asset.is_valid:
        raise ValueError(f"Invalid asset: {asset.name}")

    binary_path = binary_path.resolve()
    if not binary_path.exists():
        raise FileNotFoundError(f"Binary not found: {binary_path}")

    original_mode = binary_path.stat().st_mode

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        logging.info(f"Downloading {asset.name} to temporary directory: {tmp_dir_path}")
        download_asset(repo, asset, tmp_dir_path)

        tmp_path = tmp_dir_path / asset.name
        tmp_path.chmod(original_mode)

        # shutil.move across filesystems (e.g. /tmp -> ~/.local/bin) falls back to
        # copy-then-delete, which fails with ETXTBSY when the destination is a running
        # executable. Renaming the binary aside first avoids writing to the active inode.
        backup_path = binary_path.with_suffix(binary_path.suffix + ".old")
        if backup_path.exists():
            backup_path.unlink()
        shutil.move(str(binary_path), str(backup_path))
        try:
            shutil.move(str(tmp_path), str(binary_path))
        except Exception:
            if backup_path.exists() and not binary_path.exists():
                shutil.move(str(backup_path), str(binary_path))
            raise
        try:
            backup_path.unlink()
        except OSError:
            pass

    logging.info(f"Successfully updated binary: {binary_path}")
