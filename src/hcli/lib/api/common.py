import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from httpx import AsyncByteStream
from rich.progress import (
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from hcli import __version__
from hcli.env import ENV
from hcli.lib.auth import get_auth_service
from hcli.lib.console import console
from hcli.lib.constants.auth import CredentialType


class NotLoggedInError(Exception):
    """Raised when authentication is required but user is not logged in."""

    pass


class APIError(Exception):
    """Base API exception with HTTP context."""

    def __init__(self, message: str, status_code: int | None = None, response: httpx.Response | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class AuthenticationError(APIError):
    """401/403 authentication failures."""

    pass


class NotFoundError(APIError):
    """404 resource not found."""

    pass


class RateLimitError(APIError):
    """429 rate limit exceeded."""

    pass


class ProgressAsyncStream(AsyncByteStream):
    def __init__(self, file_path, progress_bar, task_id, chunk_size=64 * 1024):
        self.file_path = file_path
        self.progress_bar = progress_bar
        self.task_id = task_id
        self.chunk_size = chunk_size
        self.file = None

    async def __aenter__(self):
        self.file = open(self.file_path, "rb")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.file:
            self.file.close()

    async def aiter_bytes(self):
        if not self.file:
            raise RuntimeError("File not opened. Use this stream as an async context manager.")

        while True:
            chunk = self.file.read(self.chunk_size)
            if not chunk:
                break
            self.progress_bar.update(self.task_id, advance=len(chunk))
            yield chunk


class APIClient:
    """HTTP client with automatic authentication header injection."""

    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=ENV.HCLI_API_URL,
            timeout=httpx.Timeout(60.0, write=None),  # No timeout for uploads
            headers={"User-Agent": f"hcli/{__version__}"},
        )
        self._cache_dir = Path.home() / ".hcli" / "cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _get_headers(self, auth: bool = True) -> dict[str, str]:
        """Get headers with authentication if required."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if auth:
            auth_service = get_auth_service()
            if auth_service.is_logged_in():
                auth_type = auth_service.get_auth_type()
                if auth_type["type"] == CredentialType.INTERACTIVE:
                    token = auth_service.get_access_token()
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                else:
                    api_key = auth_service.get_api_key()
                    if api_key:
                        headers["x-api-key"] = api_key
            else:
                raise NotLoggedInError("Authentication required but user is not logged in")

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

    async def get_json(self, url: str, auth: bool = True) -> Any:
        """GET request returning JSON."""
        headers = self._get_headers(auth)
        response = await self.client.get(url, headers=headers)
        await self._handle_response(response)
        return response.json()

    async def post_json(self, url: str, data: Any, auth: bool = True) -> Any:
        """POST request with JSON body."""
        headers = self._get_headers(auth)
        response = await self.client.post(url, json=data, headers=headers)
        await self._handle_response(response)
        return response.json()

    async def delete_json(self, url: str, auth: bool = True) -> Any:
        """DELETE request returning JSON."""
        headers = self._get_headers(auth)
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

        with open(file_path, "rb") as f:
            with Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
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
    ) -> str:
        """Download file with progress bar and caching."""
        target_dir = Path(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Determine filename
        if target_filename:
            filename = target_filename
        else:
            parsed = urlparse(url)
            filename = Path(parsed.path).name or "download"

        cache_path = self._cache_dir / filename
        target_path = target_dir / filename

        # Check cache
        if cache_path.exists() and not force:
            try:
                # Check if cached file matches remote size
                headers = self._get_headers(auth) if auth else {}
                head_response = await self.client.head(url, headers=headers)
                await self._handle_response(head_response)
                content_length = head_response.headers.get("content-length")

                if content_length and cache_path.stat().st_size == int(content_length):
                    console.print(f"Using cached file: {cache_path}")
                    import shutil

                    shutil.copy2(cache_path, target_path)
                    return str(target_path)
            except Exception:
                # Continue with download if cache check fails
                pass

        # Download file
        headers = self._get_headers(auth) if auth else {}

        async with self.client.stream("GET", url, headers=headers) as response:
            await self._handle_response(response)

            total_size = int(response.headers.get("content-length", 0))

            with Progress(
                "[progress.description]{task.description}",
                "[progress.percentage]{task.percentage:>3.0f}%",
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                download_task = progress.add_task(
                    f"Downloading {filename}",
                    total=total_size if total_size > 0 else None,
                )

                # Write to cache first
                with open(cache_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        f.write(chunk)
                        if total_size > 0:
                            progress.update(download_task, advance=len(chunk))

        # Copy from cache to target
        import shutil

        shutil.copy2(cache_path, target_path)

        return str(target_path)


# Global API client instance
_api_client: APIClient | None = None


async def get_api_client() -> APIClient:
    """Get or create the global API client instance."""
    global _api_client
    if _api_client is None:
        _api_client = APIClient()
    return _api_client
