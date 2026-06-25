"""Handler for KE "open in IDA" deep links — download the asset, cache it, launch IDA, navigate.

KE emits IDA-native navigation links that carry the asset's download location as a
``url=`` query parameter::

    ida://ke/<idb>/<resource>?ea=0x<HEX>&view=<view>&url=<percent-encoded asset URL>

This handler matches on the presence of ``url=`` — plain navigation links without it
fall through to the default handler. It downloads the IDB from ``url=`` (and the
``.ke.json`` metadata sidecar from the same base), then relays the link, minus
``url=``, to IDA for navigation. The scheme (http/https) comes from ``url=`` as KE
emits it, so it matches however KE is served — this handler does no scheme probing.
See the KE ``docs/deep-links.md``.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import subprocess
import time
from pathlib import Path
from urllib.parse import ParseResult, parse_qsl, unquote, urlparse, urlunparse

import httpx
import rich_click as click
from rich.console import Console

from hcli.env import ENV
from hcli.lib.ida.handler.url_handler import URLHandler
from hcli.lib.ida.resolve import _print, resolve_and_navigate

logger = logging.getLogger(__name__)

console = Console()


class KEURLHandler(URLHandler):
    """Handler for KE deep links: ``ida://ke/<idb>/<resource>?…&url=<asset URL>``.

    Downloads the asset from the ``url=`` location, caches it locally, then finds
    or launches an IDA instance and relays the link (minus ``url=``) for navigation.
    """

    def matches(self, parsed: ParseResult) -> bool:
        # KE deep links carry the asset download location as a ``url=`` parameter.
        # Plain navigation links (rva/ea/name/view only) fall through to DefaultURLHandler.
        return "url" in dict(parse_qsl(parsed.query, keep_blank_values=True))

    def handle(
        self,
        uri: str,
        parsed: ParseResult,
        no_launch: bool,
        timeout: float,
        skip_analysis: bool,
    ) -> None:
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        asset_url = params.get("url", "")
        if not asset_url:
            console.print("[red]Error: KE URL is missing the 'url' download parameter[/red]")
            raise click.Abort()

        idb_name = _idb_name_from_path(parsed.path)
        if not idb_name:
            console.print("[red]Error: KE URL has no <idb> path segment[/red]")
            raise click.Abort()

        # Cleanup old downloads (best-effort)
        _cleanup_old_downloads()

        # Cache under a hash of the asset URL so two assets that share a basename
        # (e.g. both "chall.i64") don't collide in the downloads dir.
        downloads_dir = Path(ENV.HCLI_KE_DOWNLOADS_DIR or _default_downloads_dir())
        resource_path = downloads_dir / _ns(asset_url) / idb_name
        sidecar_path = resource_path.parent / f"{resource_path.name}.ke.json"
        resource_path.parent.mkdir(parents=True, exist_ok=True)

        console.print(f"[blue]Downloading {idb_name} from KE...[/blue]")

        dialog = _show_download_dialog(idb_name)

        try:
            with httpx.Client(timeout=300.0) as client:
                _download_metadata(client, asset_url, sidecar_path)
                _download_file(client, f"{asset_url}/download", resource_path)
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

        # Relay the link to IDA for navigation, minus the url= download annotation
        # (IDA ignores url= anyway; we drop it for hygiene).
        resolve_and_navigate(
            uri=_strip_query_param(uri, "url"),
            target_idb_name=resource_path.name,
            idb_path=resource_path,
            no_launch=False,
            timeout=timeout,
            skip_analysis=skip_analysis,
            navigate=_has_nav_params(parsed.query),
            on_error=_show_error_dialog,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_downloads_dir() -> str:
    return str(Path.home() / ".ke" / "downloads")


def _idb_name_from_path(path: str) -> str:
    """Return the ``<idb>`` segment (the first path segment) of an
    ``ida://ke/<idb>/<resource>`` link — the IDB filename IDA reports."""
    segments = [seg for seg in path.split("/") if seg]
    return unquote(segments[0]) if segments else ""


def _ns(url: str) -> str:
    """Short stable hash of the asset URL, used to namespace local downloads."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


_NAV_PARAMS = frozenset({"ea", "rva", "name", "view"})


def _has_nav_params(query: str) -> bool:
    """True if the link carries any IDA navigation parameter (else it is open-only)."""
    return any(key in _NAV_PARAMS for key, _ in parse_qsl(query, keep_blank_values=True))


def _strip_query_param(uri: str, param: str) -> str:
    """Return *uri* with the given query parameter removed, preserving the rest verbatim."""
    parsed = urlparse(uri)
    kept = [kv for kv in parsed.query.split("&") if kv and kv.split("=", 1)[0] != param]
    return urlunparse(parsed._replace(query="&".join(kept)))


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
