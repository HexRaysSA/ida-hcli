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

import contextlib
import hashlib
import ipaddress
import json
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
        # KE deep links have the documented shape ``ida://ke/<idb>/<resource>?…&url=``:
        # the ``ida`` scheme, the ``ke`` host, at least an ``<idb>`` path segment, AND a
        # ``url=`` download parameter. Requiring all of them keeps the download path
        # from being reached by any other ``ida://...?url=`` link — those (and plain
        # navigation links) fall through to DefaultURLHandler.
        if parsed.scheme != "ida" or parsed.hostname != "ke":
            return False
        # At least the <idb> segment (handle() needs it); a missing resource is tolerated
        # so open-only links (ida://ke/<idb>?...&url=) still route here.
        segments = [s for s in parsed.path.split("/") if s]
        if len(segments) < 1:
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
            _reject("KE URL is missing the 'url' download parameter")

        # Validate the idb segment FIRST (cheap, no network) so a malformed link does
        # not trigger a DNS lookup of the attacker-controlled url= host.
        idb_name = _idb_name_from_path(parsed.path)
        if not idb_name:
            _reject("KE URL has no valid <idb> path segment")

        # Compute the cache paths (under a hash of the asset URL so two assets that
        # share a basename don't collide). No filesystem writes happen yet — these are
        # pure path ops, and resolve() does not create anything.
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
            _reject("refusing to write outside the downloads directory")

        # Confirm BEFORE any filesystem side effect. The handler is reachable from any
        # web page, so a passive ida://ke/... click must not touch disk at all (no
        # cleanup, no dir creation, no download) without the user's consent. Skipped
        # only for --no-launch (an explicit local CLI invocation, not the drive-by
        # surface).
        host = urlparse(asset_url).hostname or "an unknown host"
        if not no_launch and not _confirm_open_dialog(idb_name, host):
            console.print("[yellow]Cancelled — nothing downloaded.[/yellow]")
            console.print("[dim]Set HCLI_KE_SKIP_CONFIRM=1 to skip this prompt.[/dim]")
            return

        # Consent given (or explicit CLI). Only NOW validate url= and resolve/pin the
        # host — doing the DNS lookup earlier would let a passive, never-consented click
        # beacon out to the attacker-controlled host. Pinning the resolved IP(s) still
        # defeats DNS rebinding between this check and the fetch. Rejections surface via
        # the native dialog (browser-launched: no visible console).
        try:
            pinned_ips = _validate_asset_url(asset_url)
        except click.ClickException as e:
            _show_error_dialog(e.message or "Invalid KE link")
            raise

        # Safe to touch the filesystem now.
        _cleanup_old_downloads()
        resource_path.parent.mkdir(parents=True, exist_ok=True)

        console.print(f"[blue]Downloading {idb_name} from KE...[/blue]")

        dialog = _show_download_dialog(idb_name)

        try:
            with httpx.Client(timeout=300.0) as client:
                _download_metadata(client, asset_url, sidecar_path, pinned_ips)
                _download_file(client, _download_url(asset_url), resource_path, pinned_ips)
        except click.ClickException as e:
            _dismiss_dialog(dialog)
            # Surface the specific reason (HTTP status, size limit, SSRF reject) in the
            # native dialog — the handler is browser-launched with no visible console.
            _show_error_dialog(e.message or "Download failed")
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


def _reject(message: str) -> None:
    """Print and surface (via the native dialog) a rejection, then abort.

    Used for the pre-download validation failures: this handler is normally
    launched from a browser with no visible console, so the dialog is the only
    feedback the user gets.
    """
    console.print(f"[red]Error: {message}[/red]")
    _show_error_dialog(message)
    raise click.Abort()


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
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv6 forms that embed an IPv4 address (e.g. "::ffff:127.0.0.1") are NOT
        # flagged by is_private/is_loopback on Python <= 3.10 — the ::ffff:0:0/96
        # range was only classified in 3.11 — yet the OS connects them to the
        # embedded IPv4 (so ::ffff:127.0.0.1 reaches loopback). Recurse on the
        # embedded address so the SSRF guard holds on every supported Python.
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is not None:
            return _is_blocked_ip(embedded)
    elif ip in _CGNAT_NET:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def _validate_asset_url(asset_url: str) -> list[str] | None:
    """Validate ``url=`` and return the IPs to pin the download connection to.

    KE links are attacker-reachable (any web page can launch ``ida://...``), so an
    unrestricted ``url=`` would let a page make hcli fetch arbitrary internal or
    loopback services. Allow only http(s), and — unless the operator opted into
    private hosts — resolve the host once, reject if ANY resolved address is
    non-public, and return ALL of the validated addresses. The caller connects only
    to these IPs, so the host cannot re-resolve to a blocked address between this
    check and the fetch (DNS rebinding), while still preserving multi-IP failover.

    Returns the validated IP literals, or ``None`` when pinning is disabled
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

    pinned_ips: list[str] = []
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(ip):
            raise click.ClickException(f"Refusing to download from non-public address: {ip}")
        ip_str = str(ip)
        if ip_str not in pinned_ips:
            pinned_ips.append(ip_str)

    if not pinned_ips:
        raise click.ClickException("Asset host did not resolve to any address")
    return pinned_ips


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


# Hard ceiling on the metadata sidecar regardless of HCLI_KE_MAX_DOWNLOAD_MB: it is
# small JSON, but the body comes from the attacker-controlled url= host, so bound it
# so a huge response can't exhaust memory/disk via the (otherwise uncapped) sidecar.
_METADATA_MAX_BYTES = 16 * 1024 * 1024

# Always keep at least this much free disk during a download, so a server can't fill
# the disk even after the user approves the download (independent of any size cap).
_MIN_FREE_BYTES = 512 * 1024 * 1024
_SPACE_CHECK_INTERVAL = 32 * 1024 * 1024


# Failures that mean "this validated IP wouldn't accept a connection" — worth
# retrying on the next pinned IP. Mid-stream resets, read timeouts, and non-200
# responses are treated as terminal (a server that answered is "up").
_RETRYABLE_CONNECT = (httpx.ConnectError, httpx.ConnectTimeout)


def _pinned_attempts(pinned_ips: list[str] | None) -> list[str | None]:
    """The list of IPs to try in order, or ``[None]`` when pinning is disabled."""
    return list(pinned_ips) if pinned_ips else [None]


# KE serves assets via a redirect (e.g. to object storage), so we must follow 3xx —
# but httpx's own follow_redirects would connect straight to the Location host,
# bypassing the SSRF pin. So we follow manually and re-validate/re-pin every hop.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def _connect_first_pinned(
    client: httpx.Client, stack: contextlib.ExitStack, url: str, pinned_ips: list[str] | None
) -> httpx.Response:
    """Open a streaming GET, trying each validated IP in turn for failover.

    The opened response is registered on *stack* so the caller controls when the
    connection is released. Raises ClickException if no validated IP will connect.
    """
    attempts = _pinned_attempts(pinned_ips)
    last_err: Exception | None = None
    for pinned_ip in attempts:
        req_url, kwargs = _pinned_request_args(url, pinned_ip)
        try:
            return stack.enter_context(client.stream("GET", req_url, **kwargs))
        except _RETRYABLE_CONNECT as e:
            last_err = e  # this IP wouldn't connect — try the next validated one
    raise click.ClickException(f"Download failed: {last_err}")


@contextlib.contextmanager
def _open_validated_stream(client: httpx.Client, url: str, pinned_ips: list[str] | None):
    """Stream a GET to *url*, following up to ``_MAX_REDIRECTS`` redirects safely.

    Each redirect ``Location`` is resolved and re-checked through
    :func:`_validate_asset_url` (SSRF guard + fresh IP pin) before the next hop, so a
    3xx — from KE or anything in the chain — can never steer the fetch to a private,
    loopback, or otherwise non-public address. Yields the final non-redirect response,
    still open for streaming; the connection is released when the ``with`` block exits.
    """
    with contextlib.ExitStack() as stack:
        for _hop in range(_MAX_REDIRECTS + 1):
            response = _connect_first_pinned(client, stack, url, pinned_ips)
            if response.status_code in _REDIRECT_STATUSES and "location" in response.headers:
                target = str(httpx.URL(url).join(response.headers["location"]))
                stack.close()  # release the redirect response before fetching the next hop
                # Re-validate (and re-pin) the new target exactly like the original url=.
                pinned_ips = _validate_asset_url(target)
                url = target
                continue
            yield response
            return
        raise click.ClickException("Download failed: too many redirects")


def _download_metadata(
    client: httpx.Client, asset_url: str, sidecar_path: Path, pinned_ips: list[str] | None = None
) -> None:
    """Download asset metadata and save as ``.ke.json`` sidecar (best-effort, bounded).

    Tries each validated IP in turn when an IP won't accept a connection and follows
    redirects (re-validated per hop); any other failure (non-200, read error, timeout,
    SSRF reject on a redirect) is reported as a warning — the sidecar is optional, so a
    missing one never blocks the download.
    """
    try:
        with _open_validated_stream(client, asset_url, pinned_ips) as response:
            if response.status_code != 200:
                _print(f"[yellow]Warning: Metadata fetch failed (HTTP {response.status_code})[/yellow]")
                return
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > _METADATA_MAX_BYTES:
                    _print("[yellow]Warning: Metadata exceeded size limit; skipping sidecar[/yellow]")
                    return
            # The body is attacker-influenced; only persist it as the .ke.json
            # sidecar if it's actually JSON, so the sidecar can't become an
            # arbitrary-content write primitive.
            try:
                json.loads(bytes(data))
            except ValueError:
                _print("[yellow]Warning: Metadata is not valid JSON; skipping sidecar[/yellow]")
                return
            sidecar_path.write_bytes(bytes(data))
    except click.ClickException as e:
        # Connect failure, too-many-redirects, or an SSRF reject on a redirect: the
        # sidecar is best-effort, so warn rather than abort the whole download.
        _print(f"[yellow]Warning: Metadata fetch failed: {e.message}[/yellow]")
    except (httpx.RequestError, httpx.TimeoutException) as e:
        _print(f"[yellow]Warning: Metadata fetch failed: {e}[/yellow]")


def _download_file(
    client: httpx.Client, download_url: str, resource_path: Path, pinned_ips: list[str] | None = None
) -> None:
    """Stream the resource file to disk.

    Streams rather than buffering the whole body in memory, enforces the optional
    ``HCLI_KE_MAX_DOWNLOAD_MB`` cap, and — even without that cap — refuses to fill the
    disk: it aborts if free space would drop below ``_MIN_FREE_BYTES``, so a malicious
    server can't stream until the disk is full after one approval. When an IP won't
    accept a connection it tries the next validated IP, and redirects are followed
    (re-validated and re-pinned per hop).

    Downloads to a temporary ``.part`` file and atomically renames on success, so a
    failed re-download never destroys a previously-cached copy at *resource_path*.
    """
    max_bytes = ENV.HCLI_KE_MAX_DOWNLOAD_MB * 1024 * 1024 if ENV.HCLI_KE_MAX_DOWNLOAD_MB > 0 else None
    tmp_path = resource_path.with_name(resource_path.name + ".part")

    try:
        with _open_validated_stream(client, download_url, pinned_ips) as response:
            if response.status_code != 200:
                raise click.ClickException(f"Download failed: HTTP {response.status_code}")
            written = 0
            next_space_check = 0
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise click.ClickException(f"Download exceeded the {ENV.HCLI_KE_MAX_DOWNLOAD_MB} MB limit")
                    # Periodically (and up front) ensure the download isn't filling
                    # the disk — bounds disk use regardless of any configured cap.
                    if written >= next_space_check:
                        if shutil.disk_usage(resource_path.parent).free < _MIN_FREE_BYTES:
                            raise click.ClickException("Download aborted: insufficient free disk space")
                        next_space_check = written + _SPACE_CHECK_INTERVAL
                    f.write(chunk)
        # Only now replace any existing cached copy — atomic on the same filesystem.
        os.replace(tmp_path, resource_path)
    except httpx.TimeoutException:
        _unlink_quiet(tmp_path)
        raise click.ClickException("Download timed out")
    except (httpx.RequestError, OSError) as e:
        _unlink_quiet(tmp_path)
        raise click.ClickException(f"Download failed: {e}")
    except click.ClickException:
        # Non-200, size/disk limit, too-many-redirects, all pinned IPs unreachable, or
        # an SSRF reject on a redirect — drop the partial and surface the reason.
        _unlink_quiet(tmp_path)
        raise


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _confirm_open_dialog(filename: str, host: str) -> bool:
    """Native yes/no confirmation before opening downloaded content in IDA.

    Returns True only if the user explicitly approved (or confirmation is suppressed
    via ``HCLI_KE_SKIP_CONFIRM``). Every platform fails CLOSED: if no prompt can be
    shown (no dialog tool, or the dialog errors), it returns False rather than
    auto-opening attacker-influenced content. The handler is browser-launched with no
    usable console, so "no prompt" cannot count as consent.
    """
    if ENV.HCLI_KE_SKIP_CONFIRM:
        return True

    system = platform.system().lower()
    prompt = f"Download and open IDB '{filename}' from {host} in IDA?"

    if system == "darwin":
        try:
            return _run_confirm(
                [
                    "osascript",
                    "-e",
                    'display dialog (system attribute "KE_DLG_MSG") with title "KE" '
                    'buttons {"Cancel", "Open"} default button "Open" cancel button "Cancel"',
                ],
                prompt,
            )
        except Exception:
            return False  # fail closed — a dialog is always available on macOS

    if system == "windows":
        try:
            return _run_confirm(
                [
                    "powershell",
                    "-WindowStyle",
                    "Hidden",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "if ([System.Windows.Forms.MessageBox]::Show("
                    '$env:KE_DLG_MSG, "KE", "YesNo", "Warning") -eq "Yes") { exit 0 } else { exit 1 }',
                ],
                prompt,
            )
        except Exception:
            return False  # fail closed — a dialog is always available on Windows

    # Linux and any other platform: use zenity/kdialog when present. If neither is
    # available (or it errors), fail CLOSED — we cannot get consent, so we must not
    # open. The file is left on disk; the user can open it manually or set
    # HCLI_KE_SKIP_CONFIRM=1 for one-click flows.
    for cmd in (
        ["zenity", "--question", "--title=KE", f"--text={prompt}"],
        ["kdialog", "--yesno", prompt, "--title", "KE"],
    ):
        if shutil.which(cmd[0]):
            try:
                return (
                    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
                    == 0
                )
            except Exception:
                return False
    _print("[yellow]No confirmation prompt available (install zenity/kdialog, or set HCLI_KE_SKIP_CONFIRM=1).[/yellow]")
    return False


def _run_confirm(cmd: list[str], prompt: str) -> bool:
    """Run a confirmation subprocess that returns exit 0 for "yes"/approve.

    The prompt text is passed as data via the environment (read by the script as
    ``system attribute``/``$env:``), never interpolated into interpreter source.
    """
    env = {**os.environ, "KE_DLG_MSG": prompt}
    return (
        subprocess.run(cmd, check=False, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    )


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
