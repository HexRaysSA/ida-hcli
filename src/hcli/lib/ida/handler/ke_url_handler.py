"""Handler for KE URLs — download assets from KE servers, cache locally, and launch IDA.

Matches URLs containing ``/api/v1/buckets/`` in the path.
Example: ``ida://ke.example.com:8080/api/v1/buckets/mybucket/assets/mykey``
"""

from __future__ import annotations

import logging
import platform
import subprocess
import time
from pathlib import Path
from urllib.parse import ParseResult, unquote

import httpx
import rich_click as click
from rich.console import Console

from hcli.env import ENV
from hcli.lib.ida.handler.url_handler import URLHandler
from hcli.lib.ida.resolve import _print, resolve_and_navigate

logger = logging.getLogger(__name__)

console = Console()


class KEURLHandler(URLHandler):
    """Handler for KE URLs: ``ida://host/api/v1/buckets/{bucket}/assets/{key}``.

    Downloads the asset from a KE server (with HTTPS/HTTP fallback),
    caches it locally, then finds or launches an IDA instance.
    """

    def matches(self, parsed: ParseResult) -> bool:
        return "/api/v1/buckets/" in parsed.path

    def handle(
        self,
        uri: str,
        parsed: ParseResult,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        if not parsed.netloc:
            console.print("[red]Error: No host in KE URL[/red]")
            raise click.Abort()

        bucket, key = _parse_asset_path(parsed.path)
        base_url = _resolve_base_url(parsed.netloc)

        asset_url = f"{base_url}{parsed.path}"
        download_url = f"{asset_url}/download"

        # Cleanup old downloads (best-effort)
        _cleanup_old_downloads()

        # Setup cache path
        downloads_dir = Path(ENV.HCLI_KE_DOWNLOADS_DIR or _default_downloads_dir())
        resource_path = downloads_dir / bucket / key
        sidecar_path = resource_path.parent / f"{resource_path.name}.ke.json"
        resource_path.parent.mkdir(parents=True, exist_ok=True)

        console.print(f"[blue]Downloading {key} from KE...[/blue]")

        filename = Path(key).name
        dialog = _show_download_dialog(filename)

        try:
            with httpx.Client(timeout=300.0) as client:
                _download_metadata(client, asset_url, sidecar_path)
                _download_file(client, download_url, resource_path)
        except click.ClickException:
            _dismiss_dialog(dialog)
            _show_error_dialog("Download failed")
            raise
        except Exception as e:
            _dismiss_dialog(dialog)
            _show_error_dialog(str(e) or "Download failed")
            raise click.ClickException(str(e) or "Download failed")

        console.print("[green]Download complete[/green]")
        time.sleep(1)
        _dismiss_dialog(dialog)

        if no_launch:
            console.print(f"[green]Downloaded file: {resource_path}[/green]")
            return

        # Launch IDA (or reuse a running instance) and navigate
        resolve_and_navigate(
            uri=uri,
            target_idb_name=resource_path.name,
            idb_path=resource_path,
            no_launch=False,
            timeout=timeout,
            skip_analysis=skip_analysis,
            navigate=bool(parsed.query),
            on_error=_show_error_dialog,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_downloads_dir() -> str:
    return str(Path.home() / ".ke" / "downloads")


def _parse_asset_path(path: str) -> tuple[str, str]:
    """Extract bucket and key from ``/api/v1/buckets/{bucket}/assets/{key}``."""
    parts = path.split("/")

    try:
        buckets_idx = parts.index("buckets")
        assets_idx = parts.index("assets")
    except ValueError:
        console.print("[red]Error: URL path must contain /buckets/{bucket}/assets/{key}[/red]")
        raise click.Abort()

    bucket = parts[buckets_idx + 1] if buckets_idx + 1 < len(parts) else ""
    key = "/".join(parts[assets_idx + 1 :]) if assets_idx + 1 < len(parts) else ""
    key = unquote(key)

    if not bucket or not key:
        console.print("[red]Error: Could not extract bucket or key from URL[/red]")
        raise click.Abort()

    return bucket, key


def _resolve_base_url(netloc: str) -> str:
    """Try HTTPS first (3 s timeout), fall back to HTTP."""
    https_url = f"https://{netloc}"
    try:
        with httpx.Client(timeout=3.0) as client:
            client.head(https_url)
        _print("[dim]Using HTTPS[/dim]")
        return https_url
    except (httpx.RequestError, httpx.TimeoutException):
        _print("[dim]HTTPS unavailable, using HTTP[/dim]")
        return f"http://{netloc}"


def _download_metadata(client: httpx.Client, asset_url: str, sidecar_path: Path) -> None:
    """Download asset metadata and save as ``.ke.json`` sidecar."""
    try:
        response = client.get(asset_url)
        if response.status_code == 200:
            sidecar_path.write_bytes(response.content)
        else:
            _print(f"[yellow]Warning: Metadata fetch failed (HTTP {response.status_code})[/yellow]")
    except (httpx.RequestError, httpx.TimeoutException) as e:
        _print(f"[yellow]Warning: Metadata fetch failed: {e}[/yellow]")


def _download_file(client: httpx.Client, download_url: str, resource_path: Path) -> None:
    """Download the resource file."""
    try:
        response = client.get(download_url)
        if response.status_code != 200:
            raise click.ClickException(f"Download failed: HTTP {response.status_code}")
        resource_path.write_bytes(response.content)
    except httpx.TimeoutException:
        raise click.ClickException("Download timed out")
    except httpx.RequestError as e:
        raise click.ClickException(f"Download failed: {e}")


def _cleanup_old_downloads() -> None:
    """Clean up downloads older than retention period (best-effort)."""
    downloads_dir = Path(ENV.HCLI_KE_DOWNLOADS_DIR or _default_downloads_dir())
    if not downloads_dir.exists():
        return

    cutoff_time = time.time() - (ENV.HCLI_KE_DOWNLOADS_RETENTION_DAYS * 24 * 60 * 60)

    try:
        for file_path in downloads_dir.rglob("*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                file_path.unlink()

        # Remove empty directories
        for dir_path in sorted(downloads_dir.rglob("*"), reverse=True):
            if dir_path.is_dir() and not any(dir_path.iterdir()):
                dir_path.rmdir()
    except Exception:
        pass


def _show_download_dialog(filename: str) -> subprocess.Popen[bytes] | None:
    """Show a platform-native dialog during download."""
    system = platform.system().lower()
    try:
        if system == "darwin":
            return subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    f'display dialog "Downloading {filename}\\n\\nPlease wait..." '
                    f'with title "KE" buttons {{}} giving up after 600',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if system == "linux":
            return subprocess.Popen(
                ["notify-send", "-t", "0", "KE", f"Downloading {filename}..."],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if system == "windows":
            return subprocess.Popen(
                [
                    "powershell",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    f"Add-Type -AssemblyName System.Windows.Forms; "
                    f"[System.Windows.Forms.MessageBox]::Show("
                    f'"Downloading {filename}...\\n\\nPlease wait...", "KE")',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass
    return None


def _dismiss_dialog(proc: subprocess.Popen[bytes] | None) -> None:
    """Dismiss the download dialog."""
    if proc is None:
        return
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def _show_error_dialog(message: str) -> None:
    """Show a platform-native error dialog (blocking)."""
    system = platform.system().lower()
    try:
        if system == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display dialog "{message}" with title "KE" buttons {{"OK"}} default button "OK" with icon stop',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif system == "linux":
            subprocess.run(
                ["notify-send", "-u", "critical", "KE", message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        elif system == "windows":
            subprocess.run(
                [
                    "powershell",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    f"Add-Type -AssemblyName System.Windows.Forms; "
                    f"[System.Windows.Forms.MessageBox]::Show("
                    f'"{message}", "KE", "OK", "Error")',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:
        pass
