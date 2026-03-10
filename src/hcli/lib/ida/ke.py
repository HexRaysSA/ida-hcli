"""KE resource download and IDA launch for ida:// URLs targeting a KE server.

KE URLs are detected by the presence of ``/api/v1/buckets/`` in the path.
Example: ida://ke.example.com:8080/api/v1/buckets/mybucket/resources/mykey
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import ParseResult, unquote

import httpx
import rich_click as click
from rich.console import Console

from hcli.env import ENV
from hcli.lib.ida.ipc import IDAIPCClient
from hcli.lib.ida.launcher import MIN_IPC_VERSION, IDALauncher, LaunchConfig, _parse_version_tuple

logger = logging.getLogger(__name__)

console = Console()


def _print(msg: str) -> None:
    """Print only when running interactively."""
    if sys.stdin.isatty():
        console.print(msg)


def is_ke_url(parsed: ParseResult) -> bool:
    """Check if an ida:// URL targets a KE server."""
    return "/api/v1/buckets/" in parsed.path


def handle_ke_url(
    uri: str,
    parsed: ParseResult,
    no_launch: bool,
    timeout: float,
    skip_analysis: bool,
) -> None:
    """Download a resource from KE and launch IDA with it.

    Steps:
      1. Resolve base URL (HTTPS first, HTTP fallback)
      2. Parse bucket/key from path
      3. Download metadata sidecar + resource file
      4. Cleanup old cached downloads
      5. Find IDA and launch with downloaded file
      6. If IDA >= 9.4 and IPC available, navigate to address (if present)
    """
    if not parsed.netloc:
        console.print("[red]Error: No host in KE URL[/red]")
        raise click.Abort()

    bucket, key = _parse_resource_path(parsed.path)
    base_url = _resolve_base_url(parsed.netloc)

    asset_url = f"{base_url}{parsed.path}".replace("/resources/", "/assets/")
    download_url = f"{base_url}{parsed.path}".replace("/resources/", "/downloads/")

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

    # Launch IDA with the downloaded file
    launcher = IDALauncher(
        LaunchConfig(
            socket_timeout=min(30.0, timeout * 0.25),
            idb_loaded_timeout=min(90.0, timeout * 0.75),
            skip_analysis_wait=skip_analysis,
        )
    )

    ida_version = launcher.get_ida_version()
    use_ipc = ida_version is None or _parse_version_tuple(ida_version) >= MIN_IPC_VERSION

    if not use_ipc:
        # Pre-IPC IDA: just launch with the file
        result = launcher.launch_only(
            resource_path,
            progress_callback=lambda msg: _print(f"[dim]{msg}[/dim]"),
        )
        if not result.success:
            _show_error_dialog(f"Failed to launch IDA: {result.error_message}")
            console.print(f"[red]Failed to launch IDA: {result.error_message}[/red]")
            raise click.Abort()
        console.print(f"[green]Opening {resource_path} with IDA[/green]")
        return

    # IDA 9.4+: launch and wait for IPC, then optionally navigate
    result = launcher.launch_and_wait(
        resource_path,
        timeout=timeout,
        progress_callback=lambda msg: _print(f"[dim]{msg}[/dim]"),
    )

    if not result.success or result.instance is None:
        error_msg = result.error_message or "Unknown error"
        _show_error_dialog(f"Failed to launch IDA: {error_msg}")
        console.print(f"[red]Failed to launch IDA: {error_msg}[/red]")
        raise click.Abort()

    console.print(f"[green]Opening {resource_path} with IDA[/green]")

    # If the original URI contains query params (e.g. ?rva=0x1000), send navigation
    if parsed.query:
        _print(f"[dim]Sending navigation command to IDA (PID {result.instance.pid})...[/dim]")
        success, message = IDAIPCClient.send_open_ida_link(result.instance.socket_path, uri)
        if success:
            _print(f"[green]Navigated to: {uri}[/green]")
        else:
            console.print(f"[yellow]Warning: Navigation failed: {message}[/yellow]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_downloads_dir() -> str:
    return str(Path.home() / ".ke" / "downloads")


def _parse_resource_path(path: str) -> tuple[str, str]:
    """Extract bucket and key from ``/api/v1/buckets/{bucket}/resources/{key}``."""
    parts = path.split("/")

    try:
        buckets_idx = parts.index("buckets")
        resources_idx = parts.index("resources")
    except ValueError:
        console.print("[red]Error: URL path must contain /buckets/{bucket}/resources/{key}[/red]")
        raise click.Abort()

    bucket = parts[buckets_idx + 1] if buckets_idx + 1 < len(parts) else ""
    key = "/".join(parts[resources_idx + 1 :]) if resources_idx + 1 < len(parts) else ""
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
