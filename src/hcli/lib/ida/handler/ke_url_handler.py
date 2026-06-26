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
import ipaddress
import logging
import os
import platform
import shutil
import socket
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
        # KE deep links have the documented shape ``ida://ke/<idb>/...?...&url=<asset>``:
        # the ``ke`` host AND a ``url=`` download parameter. Requiring both keeps the
        # download path from being reached by any other ``ida://...?url=`` link — those
        # (and plain navigation links) fall through to DefaultURLHandler.
        if parsed.hostname != "ke":
            return False
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
        # Validate and (when pinning is enabled) get the exact IP to connect to, so the
        # download can't re-resolve to a blocked address after the check (DNS rebinding).
        pinned_ip = _validate_asset_url(asset_url)

        idb_name = _idb_name_from_path(parsed.path)
        if not idb_name:
            console.print("[red]Error: KE URL has no valid <idb> path segment[/red]")
            raise click.Abort()

        # Cleanup old downloads (best-effort)
        _cleanup_old_downloads()

        # Cache under a hash of the asset URL so two assets that share a basename
        # (e.g. both "chall.i64") don't collide in the downloads dir.
        downloads_dir = Path(ENV.HCLI_KE_DOWNLOADS_DIR or _default_downloads_dir())
        resource_path = downloads_dir / _ns(asset_url) / idb_name
        sidecar_path = resource_path.parent / f"{resource_path.name}.ke.json"

        # Defense in depth: never write outside the downloads directory, even if
        # idb_name somehow carried traversal/absolute-path components. Any resolve
        # error (e.g. an unexpected invalid path byte) is treated as "outside".
        downloads_root = downloads_dir.resolve()
        try:
            outside = not resource_path.resolve().is_relative_to(downloads_root)
        except (ValueError, OSError):
            outside = True
        if outside:
            console.print("[red]Error: refusing to write outside the downloads directory[/red]")
            raise click.Abort()

        resource_path.parent.mkdir(parents=True, exist_ok=True)

        console.print(f"[blue]Downloading {idb_name} from KE...[/blue]")

        dialog = _show_download_dialog(idb_name)

        try:
            with httpx.Client(timeout=300.0) as client:
                _download_metadata(client, asset_url, sidecar_path, pinned_ip)
                _download_file(client, _download_url(asset_url), resource_path, pinned_ip)
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

        # The download path is reachable from any web page, so confirm before handing
        # attacker-influenced content to IDA (loaders/IDBs can auto-run scripts).
        host = urlparse(asset_url).hostname or "an unknown host"
        if not _confirm_open_dialog(resource_path.name, host):
            console.print("[yellow]Cancelled — not opening in IDA.[/yellow]")
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
    ``ida://ke/<idb>/<resource>`` link — the IDB filename IDA reports.

    The decoded segment MUST be a bare filename. An attacker controls the deep
    link and can percent-encode separators (``%2F``, ``%5C``) or ``..`` to smuggle
    traversal/absolute paths that would escape the downloads directory once used
    in a path join, so reject anything that isn't a plain filename here.
    """
    segments = [seg for seg in path.split("/") if seg]
    if not segments:
        return ""
    name = unquote(segments[0])
    # Reject separators, traversal, and NUL/control bytes (a NUL makes the later
    # Path.resolve() raise ValueError and would bypass the clean error path).
    if "/" in name or "\\" in name or name in (".", ".."):
        return ""
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in name):
        return ""
    return name


# RFC 6598 carrier-grade NAT shared address space — not flagged by any of the
# ipaddress is_* properties, but must be treated as non-public for SSRF.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for addresses an attacker-supplied download must never reach."""
    if ip.version == 4 and ip in _CGNAT_NET:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_asset_url(asset_url: str) -> str | None:
    """Validate ``url=`` and return the IP to pin the download connection to.

    KE links are attacker-reachable (any web page can launch ``ida://...``), so an
    unrestricted ``url=`` would let a page make hcli fetch arbitrary internal or
    loopback services. Allow only http(s), and — unless the operator opted into
    private hosts — resolve the host once, reject non-public addresses, and return
    the validated IP. The caller pins the connection to that IP so the host cannot
    re-resolve to a blocked address between this check and the fetch (DNS rebinding).

    Returns the IP literal to connect to, or ``None`` when pinning is disabled
    (private hosts allowed), leaving normal hostname resolution in place.
    """
    try:
        parsed = urlparse(asset_url)
        port = parsed.port  # property access parses (and may reject) the port
    except ValueError as e:
        raise click.ClickException(f"Invalid asset URL: {e}")

    if parsed.scheme not in ("http", "https"):
        raise click.ClickException(f"Refusing non-HTTP(S) asset URL: {parsed.scheme or 'missing'}://")

    host = parsed.hostname
    if not host:
        raise click.ClickException("Asset URL has no host")

    if ENV.HCLI_KE_ALLOW_PRIVATE_HOSTS:
        return None

    resolve_port = port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, resolve_port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise click.ClickException(f"Cannot resolve asset host: {e}")

    pinned_ip: str | None = None
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(ip):
            raise click.ClickException(f"Refusing to download from non-public address: {ip}")
        if pinned_ip is None:
            pinned_ip = str(ip)

    if pinned_ip is None:
        raise click.ClickException("Asset host did not resolve to any address")
    return pinned_ip


def _pinned_request_args(url: str, pinned_ip: str | None) -> tuple[str, dict]:
    """Rewrite *url* to connect to *pinned_ip* while preserving Host/SNI.

    Connecting to the validated IP literal stops httpx from re-resolving the host
    at fetch time. The original hostname is carried in the ``Host`` header and the
    ``sni_hostname`` extension so virtual hosting and (for https) TLS SNI plus
    certificate verification still work against the real name. With *pinned_ip*
    ``None`` the URL is returned unchanged.
    """
    if pinned_ip is None:
        return url, {}
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = f"{ip_host}:{parsed.port}" if parsed.port else ip_host
    pinned_url = urlunparse(parsed._replace(netloc=netloc))
    host_header = f"{host}:{parsed.port}" if parsed.port else host
    return pinned_url, {"headers": {"Host": host_header}, "extensions": {"sni_hostname": host}}


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


def _download_url(asset_url: str) -> str:
    """Append the ``/download`` path segment to *asset_url*, preserving query/fragment.

    KE serves the file at ``<asset>/download``. Plain ``f"{asset_url}/download"`` is
    wrong when the asset URL carries a query or fragment (the segment would land in
    the query string), so splice it into the path component instead.
    """
    parsed = urlparse(asset_url)
    new_path = parsed.path.rstrip("/") + "/download"
    return urlunparse(parsed._replace(path=new_path))


def _download_metadata(
    client: httpx.Client, asset_url: str, sidecar_path: Path, pinned_ip: str | None = None
) -> None:
    """Download asset metadata and save as ``.ke.json`` sidecar."""
    url, kwargs = _pinned_request_args(asset_url, pinned_ip)
    try:
        response = client.get(url, **kwargs)
        if response.status_code == 200:
            sidecar_path.write_bytes(response.content)
        else:
            _print(f"[yellow]Warning: Metadata fetch failed (HTTP {response.status_code})[/yellow]")
    except (httpx.RequestError, httpx.TimeoutException) as e:
        _print(f"[yellow]Warning: Metadata fetch failed: {e}[/yellow]")


def _download_file(
    client: httpx.Client, download_url: str, resource_path: Path, pinned_ip: str | None = None
) -> None:
    """Stream the resource file to disk.

    Streams rather than buffering the whole body in memory, and enforces the
    optional ``HCLI_KE_MAX_DOWNLOAD_MB`` cap. A partial file is removed on failure.
    """
    url, kwargs = _pinned_request_args(download_url, pinned_ip)
    max_bytes = ENV.HCLI_KE_MAX_DOWNLOAD_MB * 1024 * 1024 if ENV.HCLI_KE_MAX_DOWNLOAD_MB > 0 else None
    try:
        with client.stream("GET", url, **kwargs) as response:
            if response.status_code != 200:
                raise click.ClickException(f"Download failed: HTTP {response.status_code}")
            written = 0
            with open(resource_path, "wb") as f:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise click.ClickException(
                            f"Download exceeded the {ENV.HCLI_KE_MAX_DOWNLOAD_MB} MB limit"
                        )
                    f.write(chunk)
    except httpx.TimeoutException:
        _unlink_quiet(resource_path)
        raise click.ClickException("Download timed out")
    except httpx.RequestError as e:
        _unlink_quiet(resource_path)
        raise click.ClickException(f"Download failed: {e}")
    except click.ClickException:
        _unlink_quiet(resource_path)
        raise


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _confirm_open_dialog(filename: str, host: str) -> bool:
    """Native yes/no confirmation before opening downloaded content in IDA.

    Returns True if the user approved (or confirmation is suppressed via
    ``HCLI_KE_SKIP_CONFIRM``). On macOS/Windows a dialog is always available, so an
    error there fails closed (deny). Linux uses zenity/kdialog when present and
    otherwise proceeds — the desktop launcher click is itself the user's consent.
    """
    if ENV.HCLI_KE_SKIP_CONFIRM:
        return True

    system = platform.system().lower()
    prompt = f"Open downloaded IDB '{filename}' from {host} in IDA?"
    try:
        if system == "darwin":
            env = {**os.environ, "KE_DLG_MSG": prompt}
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display dialog (system attribute "KE_DLG_MSG") with title "KE" '
                    'buttons {"Cancel", "Open"} default button "Open" cancel button "Cancel"',
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0  # osascript exits non-zero when Cancel is pressed
        if system == "windows":
            env = {**os.environ, "KE_DLG_MSG": prompt}
            result = subprocess.run(
                [
                    "powershell",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "if ([System.Windows.Forms.MessageBox]::Show("
                    '$env:KE_DLG_MSG, "KE", "YesNo", "Warning") -eq "Yes") { exit 0 } else { exit 1 }',
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return result.returncode == 0
        if system == "linux":
            for cmd in (
                ["zenity", "--question", "--title=KE", f"--text={prompt}"],
                ["kdialog", "--yesno", prompt, "--title", "KE"],
            ):
                if shutil.which(cmd[0]):
                    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            _print("[yellow]No GUI prompt available (install zenity/kdialog to confirm); proceeding.[/yellow]")
            return True
    except Exception:
        # Fail closed where a dialog is expected; don't hard-break Linux.
        return system == "linux"
    return True


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
    # The filename comes from an attacker-reachable deep link, so pass it as data
    # via the environment — never inline it into osascript/PowerShell source, where
    # quotes/newlines would let it break out and execute arbitrary commands.
    env = {**os.environ, "KE_DLG_FILE": filename}
    try:
        if system == "darwin":
            return subprocess.Popen(
                [
                    "osascript",
                    "-e",
                    'display dialog ("Downloading " & (system attribute "KE_DLG_FILE") '
                    '& "\\n\\nPlease wait...") with title "KE" buttons {} giving up after 600',
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if system == "linux":
            # notify-send receives the name as a separate argv element — already safe.
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
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "[System.Windows.Forms.MessageBox]::Show("
                    '"Downloading " + $env:KE_DLG_FILE + "...`n`nPlease wait...", "KE")',
                ],
                env=env,
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
    # message may embed attacker-controlled text (e.g. a URL or server response in
    # an exception), so pass it as data via the environment rather than inlining it
    # into osascript/PowerShell source.
    env = {**os.environ, "KE_DLG_MSG": message}
    try:
        if system == "darwin":
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display dialog (system attribute "KE_DLG_MSG") with title "KE" '
                    'buttons {"OK"} default button "OK" with icon stop',
                ],
                env=env,
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
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    '[System.Windows.Forms.MessageBox]::Show($env:KE_DLG_MSG, "KE", "OK", "Error")',
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:
        pass
