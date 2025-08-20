"""GitHub API client for release management and binary updates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from packaging.version import Version, parse

from hcli.env import ENV
from hcli.lib.api.common import APIError, AuthenticationError, NotFoundError
from hcli.lib.util.io import get_arch, get_os


class GitHubRelease:
    """Represents a GitHub release."""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.tag_name: str = data["tag_name"]
        self.name: str = data["name"]
        self.prerelease: bool = data["prerelease"]
        self.draft: bool = data["draft"]
        self.assets: List[Dict[str, Any]] = data["assets"]

    @property
    def version(self) -> Optional[Version]:
        """Parse version from tag name."""
        try:
            # Remove 'v' prefix if present
            version_str = self.tag_name.lstrip("v")
            return parse(version_str)
        except Exception:
            return None

    def get_binary_asset(self, binary_name: str = "hcli") -> Optional[Dict[str, Any]]:
        """Find the appropriate binary asset for current platform."""
        arch = get_arch()
        os = get_os()

        # Platform-specific patterns
        patterns = [
            f"{binary_name}-{os}-{arch}",
        ]

        # Debug output
        from hcli.env import ENV

        if ENV.HCLI_DEBUG:
            print(f"DEBUG: Platform: {os}-{arch}")
            print(f"DEBUG: Looking for patterns: {patterns}")
            print(f"DEBUG: Available assets: {[asset['name'] for asset in self.assets]}")

        # Find matching asset - prioritize exact platform matches
        for pattern in patterns:
            for asset in self.assets:
                asset_name = asset["name"].lower()
                if pattern.lower() in asset_name:
                    if ENV.HCLI_DEBUG:
                        print(f"DEBUG: Matched '{asset_name}' with pattern '{pattern}'")
                    return asset

        return None


class GitHubAPI:
    """GitHub API client with authentication support."""

    def __init__(self, token: Optional[str] = None):
        """Initialize GitHub API client.

        Args:
            token: GitHub personal access token or fine-grained token
        """
        self.token = token or ENV.HCLI_GITHUB_TOKEN
        self.base_url = ENV.HCLI_GITHUB_API_URL

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(60.0),
            headers=self._get_headers(),
        )

    def _get_headers(self) -> Dict[str, str]:
        """Get headers with GitHub authentication."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ida-hcli",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        return headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def _handle_response(self, response: httpx.Response) -> httpx.Response:
        """Handle GitHub API response with proper error context."""
        if response.status_code == 401:
            if self.token:
                error_msg = "GitHub authentication failed. Check your token permissions."
            else:
                error_msg = "GitHub authentication required. Set GITHUB_TOKEN or GH_TOKEN environment variable."
            raise AuthenticationError(error_msg, response.status_code, response)
        elif response.status_code == 403:
            rate_limit_remaining = response.headers.get("X-RateLimit-Remaining", "0")
            if rate_limit_remaining == "0":
                reset_time = response.headers.get("X-RateLimit-Reset", "unknown")
                raise APIError(f"GitHub rate limit exceeded. Resets at {reset_time}", response.status_code, response)
            raise AuthenticationError("Access forbidden. Check repository permissions.", response.status_code, response)
        elif response.status_code == 404:
            if self.token:
                error_msg = "Repository or release not found. Check repository name and token permissions."
            else:
                error_msg = "Repository not found. If this is a private repository, set GITHUB_TOKEN or GH_TOKEN environment variable."
            raise NotFoundError(error_msg, response.status_code, response)
        elif response.status_code >= 400:
            error_msg = f"GitHub API request failed: {response.status_code}"
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_msg = error_data["message"]
            except Exception:
                pass
            raise APIError(error_msg, response.status_code, response)

        return response

    async def get_latest_release(
        self, owner: str, repo: str, include_prereleases: bool = False
    ) -> Optional[GitHubRelease]:
        """Get the latest release from a GitHub repository.

        Args:
            owner: Repository owner (user or organization)
            repo: Repository name
            include_prereleases: Include pre-releases in search

        Returns:
            GitHubRelease object or None if not found
        """
        if include_prereleases:
            # Get all releases and find the latest
            releases = await self.get_releases(owner, repo, limit=10)
            if not releases:
                return None
            return releases[0]
        else:
            # Use GitHub's latest release endpoint
            url = f"/repos/{owner}/{repo}/releases/latest"
            try:
                response = await self.client.get(url)
                await self._handle_response(response)
                return GitHubRelease(response.json())
            except NotFoundError:
                return None

    async def get_releases(self, owner: str, repo: str, limit: int = 10) -> List[GitHubRelease]:
        """Get releases from a GitHub repository.

        Args:
            owner: Repository owner
            repo: Repository name
            limit: Maximum number of releases to fetch

        Returns:
            List of GitHubRelease objects
        """
        url = f"/repos/{owner}/{repo}/releases"
        params = {"per_page": min(limit, 100)}

        response = await self.client.get(url, params=params)
        await self._handle_response(response)

        releases_data = response.json()
        return [GitHubRelease(data) for data in releases_data if not data["draft"]]

    async def get_release_by_tag(self, owner: str, repo: str, tag: str) -> Optional[GitHubRelease]:
        """Get a specific release by tag name.

        Args:
            owner: Repository owner
            repo: Repository name
            tag: Release tag name

        Returns:
            GitHubRelease object or None if not found
        """
        url = f"/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            response = await self.client.get(url)
            await self._handle_response(response)
            return GitHubRelease(response.json())
        except NotFoundError:
            return None

    async def download_asset(self, asset: Dict[str, Any], target_path: Path, show_progress: bool = True) -> str:
        """Download a release asset.

        Args:
            asset: Asset dictionary from GitHub API
            target_path: Path where to save the file
            show_progress: Whether to show download progress

        Returns:
            Path to downloaded file
        """
        # For private repos, use the API download endpoint instead of browser_download_url
        if self.token:
            # Use GitHub API asset download endpoint
            download_url = f"{self.base_url}/repos/{asset['url'].split('/repos/')[1]}"
            headers = self._get_headers()
            headers["Accept"] = "application/octet-stream"
        else:
            # For public repos, use direct download
            download_url = asset["browser_download_url"]
            headers = {}

        if ENV.HCLI_DEBUG:
            print(f"DEBUG: Download URL: {download_url}")
            print(f"DEBUG: Using token: {bool(self.token)}")
            print(f"DEBUG: Headers: {list(headers.keys())}")

        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(download_url, headers=headers)
            await self._handle_response(response)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

            return str(target_path)

    def requires_authentication(self, owner: str, repo: str) -> bool:
        """Check if repository requires authentication (heuristic).

        This is a heuristic check - the definitive way is to try accessing the repo.
        """
        return bool(self.token)  # If token is provided, assume private repo access needed
