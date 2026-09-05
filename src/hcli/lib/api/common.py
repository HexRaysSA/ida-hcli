import errno
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from rich.progress import (
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from hcli import USER_AGENT
from hcli.env import ENV
from hcli.lib.auth import get_auth_service, get_optional_auth_headers, may_send_credentials
from hcli.lib.console import console, stderr_console
from hcli.lib.util.cache import get_cache_directory
from hcli.lib.util.io import NoSpaceError, check_free_space


class NotLoggedInError(Exception):
    """Raised when authentication is required but user is not logged in."""


class APIError(Exception):
    """Base API exception with HTTP context."""

    def __init__(self, message: str, status_code: int | None = None, response: httpx.Response | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AuthenticationError(APIError):
    """401/403 authentication failures."""


class NotFoundError(APIError):
    """404 resource not found."""


class RateLimitError(APIError):
    """429 rate limit exceeded."""


class APIClient:
    """HTTP client with automatic authentication header injection."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=ENV.HCLI_API_URL,
            timeout=httpx.Timeout(60.0, write=None),  # No timeout for uploads
            headers={"User-Agent": USER_AGENT},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _get_headers(self, auth: bool = True) -> dict[str, str]:
        """Get headers with authentication if required."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if auth:
            if not get_auth_service().is_logged_in():
                raise NotLoggedInError("Authentication required but user is not logged in")
            headers.update(get_optional_auth_headers())

        return headers

    async def _handle_response(self, response: httpx.Response) -> httpx.Response:
        """Handle response with proper error context."""
        if response.status_code == 401:
            raise AuthenticationError("Authentication failed", response.status_code, response)
        elif response.status_code == 403:
            raise AuthenticationError("Access forbidden", response.status_code, response)
        elif response.status_code == 404:
            raise NotFoundError("Resource not found", response.status_code, response)
        elif response.status_code == 429:
            raise RateLimitError("Rate limit exceeded", response.status_code, response)
        elif response.status_code >= 400:
            error_msg = f"API request failed: {response.status_code}"
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_msg = error_data["message"]
            except Exception:
                pass
            raise APIError(error_msg, response.status_code, response)

        return response

    def _headers_for_url(self, url: str, auth: bool) -> dict[str, str]:
        """Headers for a request that may target an absolute URL.

        Credentials only ever go to the configured API origin: relative URLs
        resolve against it, but an absolute URL pointing anywhere else (e.g. a
        presigned S3 link handed back by the API) gets no credentials
        regardless of `auth`.
        """
        if not auth:
            return {}
        if "://" in url and not may_send_credentials(url):
            return {}
        return self._get_headers(auth=True)

    async def _resolve_credentialed_redirects(self, url: str, headers: dict[str, str]) -> tuple[str, dict[str, str]]:
        """Follow redirects manually while credentials are attached.

        httpx only strips the Authorization header cross-origin — an x-api-key
        would be forwarded to e.g. the presigned S3 host behind the API's
        download 302. Walking the credentialed hops by hand re-decides per hop;
        once no credentials apply, the caller can let httpx follow freely.
        Returns the URL to fetch and the headers to send with it.
        """
        current = url
        for _ in range(10):
            if not headers:
                return current, headers
            request = self.client.build_request("GET", current, headers=headers)
            response = await self.client.send(request, stream=True, follow_redirects=False)
            try:
                if not response.has_redirect_location:
                    return current, headers
                current = str(response.url.join(response.headers["location"]))
            finally:
                await response.aclose()
            headers = headers if may_send_credentials(current) else {}
        raise APIError(f"too many redirects while resolving: {url}")

    async def get_json(self, url: str, auth: bool = True) -> Any:
        """GET request returning JSON."""
        headers = self._headers_for_url(url, auth)
        response = await self.client.get(url, headers=headers)
        await self._handle_response(response)
        return response.json()

    async def post_json(self, url: str, data: Any, auth: bool = True) -> Any:
        """POST request with JSON body."""
        headers = self._headers_for_url(url, auth)
        response = await self.client.post(url, json=data, headers=headers)
        await self._handle_response(response)
        return response.json()

    async def delete_json(self, url: str, auth: bool = True) -> Any:
        """DELETE request returning JSON."""
        headers = self._headers_for_url(url, auth)
        response = await self.client.delete(url, headers=headers)
        await self._handle_response(response)
        return response.json()

    async def put_file(self, url: str, file_path: str | Path):
        """Upload file via PUT request with progress bar."""
        file_path = Path(file_path)

        # Determine content type
        if file_path.suffix == ".zip":
            content_type = "application/zip"
        elif file_path.suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "application/octet-stream"

        file_size = os.path.getsize(file_path)

        with (
            open(file_path, "rb") as f,  # noqa: ASYNC230
            Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=stderr_console,
            ) as progress,
        ):
            task = progress.add_task(f"Uploading {file_path}", total=file_size)

            async def file_stream():
                while chunk := f.read(8192):
                    yield chunk
                    progress.update(task, advance=len(chunk))

            headers = {
                "Content-Type": content_type,
                "Content-Length": str(file_size),
            }

            response = await self.client.put(
                url,
                content=file_stream(),
                headers=headers,
                follow_redirects=True,
            )

            await self._handle_response(response)
            progress.update(task, description="[green]Upload Complete[/green]")

    async def download_file(
        self,
        url: str,
        target_dir: str | Path = "./",
        target_filename: str | None = None,
        force: bool = False,
        auth: bool = False,
        asset_key: str | None = None,
    ) -> str:
        """Download file with progress bar and caching.

        Args:
            url: URL to download from
            target_dir: Directory to save the file
            target_filename: Override filename (if not provided, extracted from URL)
            force: Skip cache and force download
            auth: Use authentication
            asset_key: Full asset key (e.g., 'release/9.2/ida-pro/filename.zip') for cache path
        """
        target_dir = Path(target_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Determine filename
        if target_filename:
            filename = target_filename
        else:
            parsed = urlparse(url)
            filename = Path(parsed.path).name or "download"

        # Credentials are scoped to the API origin, and credentialed redirects
        # are pre-resolved by hand so a token or x-api-key never rides a 302 to
        # e.g. a presigned S3 host. From here on, url + request_headers are safe
        # to use with follow_redirects=True (remaining hops are credential-free).
        request_headers = self._headers_for_url(url, auth)
        if request_headers:
            url, request_headers = await self._resolve_credentialed_redirects(url, request_headers)

        # Create cache path using XDG_CACHE_HOME with "downloads" key
        # Use the full asset_key if provided, otherwise fall back to filename
        cache_key = asset_key or filename
        cache_path = get_cache_directory("downloads") / cache_key
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        # Check cache
        if cache_path.exists() and not force:
            try:
                # Check if cached file matches remote size
                head_response = await self.client.head(url, headers=request_headers, follow_redirects=True)
                await self._handle_response(head_response)
                content_length = head_response.headers.get("content-length")

                if content_length and cache_path.stat().st_size == int(content_length):
                    console.print(f"Using cached file: {cache_path}")

                    # Check space at target before copying from cache
                    check_free_space(target_dir, cache_path.stat().st_size)
                    try:
                        shutil.copy2(cache_path, target_path)
                    except OSError as e:
                        if e.errno == errno.ENOSPC:
                            if target_path.exists():
                                target_path.unlink()
                            raise NoSpaceError(target_dir) from e
                        raise
                    return str(target_path)
            except NoSpaceError:
                raise
            except AuthenticationError:
                # Presigned URLs may not support HEAD requests (403), continue to download
                pass
            except Exception:
                # Continue with download if cache check fails
                pass

        # Download file
        async with self.client.stream("GET", url, headers=request_headers, follow_redirects=True) as response:
            await self._handle_response(response)

            total_size_str = response.headers.get("content-length")
            total_size = int(total_size_str) if total_size_str else 0

            # Proactive space check if total_size is known
            if total_size > 0:
                check_free_space(cache_path.parent, total_size)

            with Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=stderr_console,
            ) as progress:
                download_task = progress.add_task(
                    f"Downloading {filename}",
                    total=total_size if total_size > 0 else None,
                )

                try:
                    with open(cache_path, "wb") as f:  # noqa: ASYNC230
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                            if total_size > 0:
                                progress.update(download_task, advance=len(chunk))
                except OSError as e:
                    if e.errno == errno.ENOSPC:
                        if cache_path.exists():
                            cache_path.unlink()
                        raise NoSpaceError(cache_path.parent) from e
                    raise

        # Check space at target before copying from cache
        file_size = cache_path.stat().st_size
        check_free_space(target_dir, file_size)

        # Copy from cache to target
        try:
            shutil.copy2(cache_path, target_path)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                if target_path.exists():
                    target_path.unlink()
                raise NoSpaceError(target_dir) from e
            raise

        return str(target_path)


# Global API client instance
_api_client: APIClient | None = None


async def get_api_client() -> APIClient:
    """Get or create the global API client instance."""
    global _api_client
    if _api_client is None:
        _api_client = APIClient()
    return _api_client
