import dataclasses
import itertools
import json
import logging
import re
import shutil
import sys
import tempfile
import typing
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from semantic_version import SimpleSpec, Version

from hcli.env import ENV


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
        if path.endswith(".git"):
            path = path[:-4]

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
    header: dict[str, str] = dict()

    @classmethod
    def init(cls, repo: GitHubRepo):
        if cls.header or not repo.token:
            return
        cls.header = dict(Authorization=f"Bearer {repo.token}")


def check_and_download_updates(
    repo: GitHubRepo,
    compatibility_spec: SimpleSpec = None,
    current_version: Version = None,
    assets_mask=re.compile(".*"),
    downloads_dir=Path(),
    download_callback: Callable[[ReleaseAsset, int], None] | None = None,
):
    if download_callback is None:
        download_callback = default_download_callback
    if current_version is None:
        current_version = Version("0.0.0")
    AuthSession.init(repo)
    if compatibility_spec is None:
        logging.info("No compatibility requirements set")
        download_version = get_latest_version(repo)
    else:
        logging.info(f"Compatibility requirement: '{compatibility_spec}'")
        download_version = get_compatible_version(repo, compatibility_spec)

    if download_version is None:
        compatible = " compatible" if compatibility_spec is not None else ""
        logging.warning(f"No newer{compatible} versions available.")
        return

    if is_already_installed(download_version, current_version, compatibility_spec):
        return
    tag_name = getattr(download_version, "_origin_tag_name", str(download_version))
    assets = get_assets(repo, tag_name, assets_mask)
    if not assets:
        logging.error("No assets found")
        return
    download_assets(repo, assets, out_dir=downloads_dir, callback=download_callback)
    logging.info("Done!")


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


def download_assets(
    repo: GitHubRepo,
    assets: typing.Iterable[ReleaseAsset],
    out_dir=Path(),
    block_size=2**20,
    callback: Callable[[ReleaseAsset, int], None] = lambda _, __: None,
):
    logging.info(f"Start downloading assets: {tuple(asset.name for asset in assets)}")
    for asset in assets:
        download_asset(repo, asset, out_dir, block_size, lambda downloaded, _: callback(asset, downloaded))


def download_asset(
    repo: GitHubRepo,
    asset: ReleaseAsset,
    out_dir=Path(),
    block_size=2**20,
    callback: Callable[[int, int], None] = lambda _, __: None,
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

    response = requests.get(asset_url, stream=True, headers=headers)
    with open(out_dir.joinpath(asset.name), "wb") as file:
        for i, data in enumerate(response.iter_content(block_size)):
            file.write(data)
            callback(i * block_size, asset.size)
        callback(asset.size, asset.size)


def default_download_callback(asset: ReleaseAsset, downloaded: int):
    logging.info(
        f"'{asset.name}' downloading progress: "
        f"{downloaded // 2**13}/{asset.size // 2**13}kb "
        f"({100 * downloaded / asset.size:.2f}%)"
    )


def get_available_versions(repo: GitHubRepo, process_tag: Callable[[str], Version | None] | None = None):
    if process_tag is None:
        process_tag = parse_tag
    logging.info(f"Searching for releases in 'https://github.com/{repo.user}/{repo.repo}/'...")
    request_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases"
    page_size = 100
    for i in itertools.count(1):
        data = json.loads(requests.get(request_url, dict(page=i, per_page=page_size), headers=AuthSession.header).text)
        if "message" in data or not isinstance(data, list):
            break
        for release in data:
            tag_name = release.get("tag_name")
            if tag_name is None:
                continue
            version = process_tag(tag_name)
            if version is None:
                continue
            setattr(version, "_origin_tag_name", tag_name)
            yield version
        logging.info(f"Version's page#{i} loaded")
        if len(data) < page_size:
            logging.info("No more pages")
            break


def get_latest_version(
    repo: GitHubRepo, process_tag: Callable[[str], Version | None] | None = None, include_dev: bool = False
):
    if process_tag is None:
        process_tag = parse_tag

    if include_dev:
        # Use the existing logic for latest release (which might be dev)
        logging.info(f"Searching for latest release in 'https://github.com/{repo.user}/{repo.repo}/'...")
        request_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases/latest"
        data = json.loads(requests.get(request_url, headers=AuthSession.header).text)
        if "message" in data:
            return
        tag_name = data.get("tag_name")
        if tag_name is None:
            return
        version = process_tag(tag_name)
        if version is not None:
            version._origin_tag_name = tag_name
        return version
    else:
        # Search through all releases to find latest stable
        logging.info(f"Searching for latest stable release in 'https://github.com/{repo.user}/{repo.repo}/'...")
        for version in get_available_versions(repo, process_tag):
            tag_name = getattr(version, "_origin_tag_name", str(version))
            if not is_dev_version(tag_name):
                return version
        return None


def parse_tag(tag_name: str) -> Version | None:
    try:
        return Version(tag_name.lstrip("v").strip())
    except ValueError:
        return None


def get_assets(repo: GitHubRepo, tag_name: str, assets_mask=re.compile(".*")):
    logging.info(f"Searching for assets by tag '{tag_name}' and mask: '{assets_mask.pattern}'")
    request_url = f"{ENV.HCLI_GITHUB_API_URL}/repos/{repo.user}/{repo.repo}/releases/tags/{tag_name}"
    data = json.loads(requests.get(request_url, headers=AuthSession.header).text)
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


def is_already_installed(latest: Version, current: Version, compatibility_spec: SimpleSpec):
    if current < latest:
        return False
    logging.info(f"Latest version is already installed: {current}")
    if current > latest:
        logging.warning(
            f"Current version newer then latest found ({latest})"
            + (", but still compatible." if compatibility_spec.match(current) else ", and incompatible!")
        )
    return True


def update_asset(repo: GitHubRepo, asset: ReleaseAsset, binary_path: Path) -> bool:
    """
    Download an asset to a temporary file and replace the running binary.

    Args:
        repo: The GitHub repository information
        asset: The ReleaseAsset to download
        binary_path: Path to the current binary to replace

    Returns:
        True if update was successful, False otherwise
    """
    if not asset.is_valid:
        logging.error(f"Invalid asset: {asset.name}")
        return False

    binary_path = binary_path.resolve()
    if not binary_path.exists():
        logging.error(f"Binary not found: {binary_path}")
        return False

    # Store original permissions
    original_stat = binary_path.stat()
    original_mode = original_stat.st_mode

    try:
        # Create temporary directory for download
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir_path = Path(tmp_dir)

            logging.info(f"Downloading {asset.name} to temporary directory: {tmp_dir_path}")

            # Use existing download_asset function
            download_asset(repo, asset, tmp_dir_path)

            tmp_path = tmp_dir_path / asset.name

            # Set executable permissions on the temporary file
            tmp_path.chmod(original_mode)

            # Perform atomic replacement
            # On Unix systems, this works even if the original file is currently running
            if sys.platform == "win32":
                # On Windows, we need to move the original file first
                backup_path = binary_path.with_suffix(binary_path.suffix + ".old")
                if backup_path.exists():
                    backup_path.unlink()
                shutil.move(str(binary_path), str(backup_path))
                shutil.move(str(tmp_path), str(binary_path))
                # Clean up backup file
                try:
                    backup_path.unlink()
                except OSError:
                    # Backup file might be in use, leave it
                    pass
            else:
                # On Unix systems, atomic replacement using rename
                tmp_path.replace(binary_path)

        logging.info(f"Successfully updated binary: {binary_path}")
        return True

    except Exception as e:
        logging.error(f"Failed to update binary: {e}")
        # Clean up temporary file if it exists
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False
