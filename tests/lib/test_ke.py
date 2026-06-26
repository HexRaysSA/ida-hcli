"""Tests for KE deep-link handling in the ida:// protocol."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote, urlparse

import click
import pytest

from hcli.env import ENV as ENV_CLS
from hcli.lib.ida.handler.ke_url_handler import (
    KEURLHandler,
    _cleanup_old_downloads,
    _confirm_open_dialog,
    _default_downloads_dir,
    _download_url,
    _has_nav_params,
    _idb_name_from_path,
    _ns,
    _pinned_request_args,
    _strip_query_param,
    _validate_asset_url,
)

# A representative KE asset base — what KE percent-encodes into the ``url=`` parameter.
ASSET_URL = "http://host:8080/api/v1/buckets/mybucket/assets/test.i64"


def _ke_link(idb: str, resource: str, asset_url: str, *, nav: str = "") -> str:
    """Build a KE deep link the way KE emits it (asset URL percent-encoded into url=)."""
    query = (f"{nav}&" if nav else "") + f"url={quote(asset_url, safe='')}"
    return f"ida://ke/{idb}/{resource}?{query}"


class TestMatches:
    def test_ke_link_with_url_param_detected(self):
        parsed = urlparse(_ke_link("test.i64", "functions", ASSET_URL, nav="ea=0x1000&view=pseudocode"))
        assert KEURLHandler().matches(parsed) is True

    def test_open_only_link_detected(self):
        parsed = urlparse(_ke_link("test.i64", "addresses", ASSET_URL))
        assert KEURLHandler().matches(parsed) is True

    def test_plain_navigation_link_not_detected(self):
        # No url= param → handled by DefaultURLHandler, not KE.
        parsed = urlparse("ida://malwares/trojan.i64/functions?rva=0x1000")
        assert KEURLHandler().matches(parsed) is False

    def test_obsolete_locator_not_detected(self):
        # The old download-locator form carried no url= param — it no longer matches.
        parsed = urlparse("ida://host:8080/api/v1/buckets/mybucket/assets/test.i64")
        assert KEURLHandler().matches(parsed) is False

    def test_url_param_on_non_ke_host_not_detected(self):
        # Only the documented ida://ke/... host gets the download path; a url= param
        # on any other host must fall through to DefaultURLHandler.
        parsed = urlparse("ida://evil/test.i64/functions?url=http://attacker/x")
        assert KEURLHandler().matches(parsed) is False

    def test_non_ida_scheme_not_detected(self):
        parsed = urlparse("http://ke/test.i64/functions?url=http://attacker/x")
        assert KEURLHandler().matches(parsed) is False

    def test_no_path_segment_not_detected(self):
        # No <idb> segment at all isn't a KE link.
        parsed = urlparse("ida://ke/?url=http://attacker/x")
        assert KEURLHandler().matches(parsed) is False

    def test_single_segment_open_only_link_detected(self):
        # An open-only link with just the <idb> segment still routes to KE.
        parsed = urlparse("ida://ke/test.i64?url=" + quote(ASSET_URL, safe=""))
        assert KEURLHandler().matches(parsed) is True


class TestDownloadUrl:
    def test_appends_download_segment(self):
        assert _download_url("https://host/a/b") == "https://host/a/b/download"

    def test_preserves_query(self):
        # Naive f"{url}/download" would put the segment inside the query string.
        assert _download_url("https://host/a?token=x") == "https://host/a/download?token=x"

    def test_handles_trailing_slash(self):
        assert _download_url("https://host/a/") == "https://host/a/download"


class TestConfirmOpenDialog:
    def test_skip_confirm_returns_true_without_prompting(self):
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", True),
            patch("hcli.lib.ida.handler.ke_url_handler.subprocess.run") as mock_run,
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is True
            mock_run.assert_not_called()

    def test_linux_without_gui_tool_fails_closed(self):
        # No zenity/kdialog → cannot get consent → must NOT auto-open (fail closed).
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", False),
            patch("hcli.lib.ida.handler.ke_url_handler.platform.system", return_value="Linux"),
            patch("hcli.lib.ida.handler.ke_url_handler.shutil.which", return_value=None),
            patch("hcli.lib.ida.handler.ke_url_handler._print"),
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is False

    def test_linux_zenity_decline_denies(self):
        completed = MagicMock()
        completed.returncode = 1  # user clicked No / closed
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", False),
            patch("hcli.lib.ida.handler.ke_url_handler.platform.system", return_value="Linux"),
            patch(
                "hcli.lib.ida.handler.ke_url_handler.shutil.which",
                side_effect=lambda c: "/usr/bin/zenity" if c == "zenity" else None,
            ),
            patch("hcli.lib.ida.handler.ke_url_handler.subprocess.run", return_value=completed),
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is False

    def test_unknown_platform_fails_closed(self):
        # A platform with no scriptable dialog cannot get consent → fail closed.
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", False),
            patch("hcli.lib.ida.handler.ke_url_handler.platform.system", return_value="FreeBSD"),
            patch("hcli.lib.ida.handler.ke_url_handler.shutil.which", return_value=None),
            patch("hcli.lib.ida.handler.ke_url_handler._print") as mock_print,
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is False
            assert mock_print.called  # user was told how to enable opening

    def test_macos_dialog_error_fails_closed(self):
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", False),
            patch("hcli.lib.ida.handler.ke_url_handler.platform.system", return_value="Darwin"),
            patch("hcli.lib.ida.handler.ke_url_handler.subprocess.run", side_effect=OSError("no osascript")),
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is False

    def test_windows_dialog_error_fails_closed(self):
        with (
            patch.object(ENV_CLS, "HCLI_KE_SKIP_CONFIRM", False),
            patch("hcli.lib.ida.handler.ke_url_handler.platform.system", return_value="Windows"),
            patch("hcli.lib.ida.handler.ke_url_handler.subprocess.run", side_effect=FileNotFoundError("no powershell")),
        ):
            assert _confirm_open_dialog("chall.i64", "ke.example.com") is False


class TestDownloadMetadataCap:
    def test_oversized_metadata_skips_sidecar(self, tmp_path):
        # The metadata fetch is bounded independently of HCLI_KE_MAX_DOWNLOAD_MB so a
        # huge attacker body cannot exhaust memory/disk via the .ke.json sidecar.
        from hcli.lib.ida.handler import ke_url_handler

        sidecar = tmp_path / "x.ke.json"
        huge = b"A" * (ke_url_handler._METADATA_MAX_BYTES + 1)

        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_bytes.return_value = [huge]
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=stream_response)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = cm

        with patch("hcli.lib.ida.handler.ke_url_handler._print"):
            ke_url_handler._download_metadata(client, "https://h/a", sidecar, None)

        assert not sidecar.exists()  # refused to write the oversized body

    def test_non_json_metadata_skips_sidecar(self, tmp_path):
        # The .ke.json sidecar must only persist valid JSON, not arbitrary 200 bodies.
        from hcli.lib.ida.handler import ke_url_handler

        sidecar = tmp_path / "x.ke.json"
        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_bytes.return_value = [b"<html>not json</html>"]
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=stream_response)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = cm

        with patch("hcli.lib.ida.handler.ke_url_handler._print"):
            ke_url_handler._download_metadata(client, "https://h/a", sidecar, None)

        assert not sidecar.exists()  # non-JSON body rejected

    def test_valid_json_metadata_is_written(self, tmp_path):
        from hcli.lib.ida.handler import ke_url_handler

        sidecar = tmp_path / "x.ke.json"
        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_bytes.return_value = [b'{"ok": true}']
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=stream_response)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = cm

        ke_url_handler._download_metadata(client, "https://h/a", sidecar, None)
        assert sidecar.read_bytes() == b'{"ok": true}'


class TestDownloadFileDiskGuard:
    def test_aborts_and_cleans_up_when_disk_nearly_full(self, tmp_path):
        # Even with no size cap, the download must not fill the disk: when free space
        # is below the reserve, it aborts and removes the partial file.
        from types import SimpleNamespace

        from hcli.lib.ida.handler import ke_url_handler

        dest = tmp_path / "big.i64"
        stream_response = MagicMock()
        stream_response.status_code = 200
        stream_response.iter_bytes.return_value = [b"x" * 4096]
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=stream_response)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = cm

        with (
            patch(
                "hcli.lib.ida.handler.ke_url_handler.shutil.disk_usage",
                return_value=SimpleNamespace(total=0, used=0, free=1024),  # only 1 KB free
            ),
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            pytest.raises(click.ClickException, match="disk space"),
        ):
            mock_env.HCLI_KE_MAX_DOWNLOAD_MB = 0
            ke_url_handler._download_file(client, "https://h/a/download", dest, None)

        assert not dest.exists()  # partial file removed

    def test_failed_redownload_preserves_cached_file(self, tmp_path):
        # A re-download that fails must not destroy a previously-cached good copy:
        # the download goes to a .part file and only atomically replaces on success.
        from hcli.lib.ida.handler import ke_url_handler

        dest = tmp_path / "cached.i64"
        dest.write_bytes(b"GOOD CACHED COPY")

        stream_response = MagicMock()
        stream_response.status_code = 500  # transient server failure
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=stream_response)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.stream.return_value = cm

        with patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env, pytest.raises(click.ClickException):
            mock_env.HCLI_KE_MAX_DOWNLOAD_MB = 0
            ke_url_handler._download_file(client, "https://h/a/download", dest, None)

        assert dest.read_bytes() == b"GOOD CACHED COPY"  # untouched
        assert not (tmp_path / "cached.i64.part").exists()  # partial cleaned up


class TestIdbNameFromPath:
    def test_first_segment_is_the_idb(self):
        assert _idb_name_from_path("/test.i64/functions") == "test.i64"

    def test_open_only_path(self):
        assert _idb_name_from_path("/chall.i64/addresses") == "chall.i64"

    def test_percent_encoded_name_decoded(self):
        assert _idb_name_from_path("/my%20file.i64/functions") == "my file.i64"

    def test_empty_path(self):
        assert _idb_name_from_path("") == ""

    def test_rejects_encoded_traversal(self):
        # %2F-encoded separators survive urlparse and would escape the downloads dir.
        assert _idb_name_from_path("/..%2F..%2F..%2Fhome%2Fvictim%2F.bashrc/functions") == ""

    def test_rejects_encoded_absolute_path(self):
        assert _idb_name_from_path("/%2Fetc%2Fcron.d%2Fevil/functions") == ""

    def test_rejects_dotdot(self):
        assert _idb_name_from_path("/../functions") == ""

    def test_rejects_backslash(self):
        assert _idb_name_from_path("/a%5Cb/functions") == ""

    def test_rejects_embedded_nul(self):
        # A NUL would make the later Path.resolve() raise ValueError; reject up front.
        assert _idb_name_from_path("/foo%00.i64/functions") == ""


def _addrinfo(*ips: str):
    """Build a getaddrinfo-style result list for the given IP strings."""
    return [(2, 1, 6, "", (ip, 443)) for ip in ips]


class TestValidateAssetUrl:
    """Tests for the SSRF guard / DNS-rebinding pin in _validate_asset_url."""

    def test_rejects_non_http_scheme(self):
        with pytest.raises(click.ClickException, match="non-HTTP"):
            _validate_asset_url("file:///etc/passwd")

    def test_rejects_missing_host(self):
        with pytest.raises(click.ClickException, match="no host"):
            _validate_asset_url("http:///path/only")

    def test_rejects_malformed_port(self):
        # parsed.port raises ValueError — must surface as a clean ClickException.
        with pytest.raises(click.ClickException, match="Invalid asset URL"):
            _validate_asset_url("http://host:notaport/x")

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_rejects_loopback(self, mock_gai):
        mock_gai.return_value = _addrinfo("127.0.0.1")
        with (
            patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False),
            pytest.raises(click.ClickException, match="non-public"),
        ):
            _validate_asset_url("http://evil.example/x")

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_rejects_link_local_metadata(self, mock_gai):
        mock_gai.return_value = _addrinfo("169.254.169.254")
        with (
            patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False),
            pytest.raises(click.ClickException, match="non-public"),
        ):
            _validate_asset_url("http://metadata.example/x")

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_rejects_cgnat(self, mock_gai):
        mock_gai.return_value = _addrinfo("100.64.1.1")
        with (
            patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False),
            pytest.raises(click.ClickException, match="non-public"),
        ):
            _validate_asset_url("http://cgnat.example/x")

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_rejects_when_any_resolved_ip_is_private(self, mock_gai):
        # A host that returns one public + one private IP must be rejected (rebinding hedge).
        mock_gai.return_value = _addrinfo("93.184.216.34", "10.0.0.5")
        with (
            patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False),
            pytest.raises(click.ClickException, match="non-public"),
        ):
            _validate_asset_url("http://mixed.example/x")

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_returns_pinned_ip_for_public_host(self, mock_gai):
        mock_gai.return_value = _addrinfo("93.184.216.34")
        with patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False):
            assert _validate_asset_url("https://public.example/x") == ["93.184.216.34"]

    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    def test_returns_all_public_ips_deduped_for_failover(self, mock_gai):
        # All validated IPs are returned (deduped) so the download can fail over.
        mock_gai.return_value = _addrinfo("93.184.216.34", "93.184.216.35", "93.184.216.34")
        with patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", False):
            assert _validate_asset_url("https://public.example/x") == ["93.184.216.34", "93.184.216.35"]

    def test_returns_none_when_private_hosts_allowed(self):
        # Opt-out: no resolution, no pinning — preserves self-hosted/internal KE.
        with patch.object(ENV_CLS, "HCLI_KE_ALLOW_PRIVATE_HOSTS", True):
            assert _validate_asset_url("http://internal-ke.lan:8080/x") is None


class TestPinnedRequestArgs:
    """Tests for the IP-pinning rewrite that defeats DNS rebinding at fetch time."""

    def test_none_pin_passes_through(self):
        url, kwargs = _pinned_request_args("https://host:8080/a/b", None)
        assert url == "https://host:8080/a/b"
        assert kwargs == {}

    def test_pins_ip_and_preserves_host_and_sni(self):
        url, kwargs = _pinned_request_args("https://host.example:8080/a/b", "93.184.216.34")
        assert url == "https://93.184.216.34:8080/a/b"
        assert kwargs["headers"]["Host"] == "host.example:8080"
        assert kwargs["extensions"]["sni_hostname"] == "host.example"

    def test_pins_ipv6_with_brackets(self):
        url, kwargs = _pinned_request_args("http://host.example/a", "2606:2800:220:1:248:1893:25c8:1946")
        assert url == "http://[2606:2800:220:1:248:1893:25c8:1946]/a"
        assert kwargs["extensions"]["sni_hostname"] == "host.example"


class TestNs:
    def test_stable_and_short(self):
        assert _ns(ASSET_URL) == _ns(ASSET_URL)
        assert len(_ns(ASSET_URL)) == 16

    def test_differs_per_url(self):
        assert _ns(ASSET_URL) != _ns(ASSET_URL + "x")


class TestHasNavParams:
    def test_ea_is_navigation(self):
        assert _has_nav_params("ea=0x1000&url=x") is True

    def test_view_is_navigation(self):
        assert _has_nav_params("view=pseudocode&url=x") is True

    def test_url_only_is_open_only(self):
        assert _has_nav_params("url=x") is False

    def test_empty_query(self):
        assert _has_nav_params("") is False


class TestStripQueryParam:
    def test_drops_url_keeps_the_rest(self):
        uri = "ida://ke/test.i64/functions?ea=0x1000&view=pseudocode&url=" + quote(ASSET_URL, safe="")
        assert _strip_query_param(uri, "url") == "ida://ke/test.i64/functions?ea=0x1000&view=pseudocode"

    def test_drops_only_url_when_open_only(self):
        uri = "ida://ke/test.i64/addresses?url=" + quote(ASSET_URL, safe="")
        assert _strip_query_param(uri, "url") == "ida://ke/test.i64/addresses"


class TestDefaultDownloadsDir:
    def test_default_path(self):
        result = Path(_default_downloads_dir())
        assert result == Path.home() / ".ke" / "downloads"


class TestCleanupOldDownloads:
    def test_cleanup_old_files(self, tmp_path):
        # Create a file older than retention
        old_file = tmp_path / "bucket" / "old.idb"
        old_file.parent.mkdir(parents=True)
        old_file.write_text("old")
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 24 * 60 * 60)
        import os

        os.utime(old_file, (old_time, old_time))

        # Create a recent file
        new_file = tmp_path / "bucket" / "new.idb"
        new_file.write_text("new")

        with patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env:
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            _cleanup_old_downloads()

        assert not old_file.exists()
        assert new_file.exists()

    def test_cleanup_nonexistent_dir(self, tmp_path):
        with patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env:
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path / "nonexistent")
            _cleanup_old_downloads()  # should not raise


def _stream_cm(content: bytes, status: int = 200):
    """A mock httpx streaming-response context manager yielding *content*."""
    response = MagicMock()
    response.status_code = status
    response.iter_bytes.return_value = [content]
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=response)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _mock_httpx_client(content: bytes = b"file content", meta_content: bytes = b'{"meta":true}', status: int = 200):
    """A mock httpx client whose .stream yields the metadata body then the file body.

    handle() streams metadata first (asset_url) then the file (<asset_url>/download),
    so distinct bodies let tests verify each independently.
    """
    mock_client = MagicMock()
    mock_client.stream.side_effect = [_stream_cm(meta_content, 200), _stream_cm(content, status)]
    return mock_client


def _set_handler_env(mock_env, tmp_path, *, allow_private=True, skip_confirm=True):
    """Populate the patched ENV with concrete (non-MagicMock) values the handler reads."""
    mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
    mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
    mock_env.HCLI_KE_MAX_DOWNLOAD_MB = 0
    mock_env.HCLI_KE_SKIP_CONFIRM = skip_confirm
    mock_env.HCLI_KE_ALLOW_PRIVATE_HOSTS = allow_private


class TestHandleKeUrl:
    @patch("hcli.lib.ida.handler.ke_url_handler._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.handler.ke_url_handler._dismiss_dialog")
    @patch("hcli.lib.ida.handler.ke_url_handler._cleanup_old_downloads")
    @patch("hcli.lib.ida.handler.ke_url_handler.httpx.Client")
    def test_no_launch_downloads_to_hashed_dir(
        self, mock_client_cls, mock_cleanup, mock_dismiss, mock_dialog, tmp_path
    ):
        mock_client = _mock_httpx_client(b"file content")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "addresses", ASSET_URL)
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
        ):
            _set_handler_env(mock_env, tmp_path)
            KEURLHandler().handle(uri, parsed, no_launch=True, timeout=120.0, skip_analysis=False)

        # File and its .ke.json sidecar land under the url-hash namespace dir, each
        # with its own body (so a broken metadata path can't masquerade as the file).
        downloaded = tmp_path / _ns(ASSET_URL) / "test.i64"
        sidecar = tmp_path / _ns(ASSET_URL) / "test.i64.ke.json"
        assert downloaded.read_bytes() == b"file content"
        assert sidecar.read_bytes() == b'{"meta":true}'

    @patch("hcli.lib.ida.handler.ke_url_handler._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.handler.ke_url_handler._dismiss_dialog")
    @patch("hcli.lib.ida.handler.ke_url_handler._cleanup_old_downloads")
    @patch("hcli.lib.ida.handler.ke_url_handler.httpx.Client")
    @patch("hcli.lib.ida.resolve.IDAIPCClient")
    def test_relays_navigation_link_without_url_param(
        self, mock_ipc, mock_client_cls, mock_cleanup, mock_dismiss, mock_dialog, tmp_path
    ):
        """The link relayed to IDA must be the url=-stripped nav URI (the IDA 9.4 fix)."""
        from hcli.lib.ida.ipc import IDAInstance

        running_instance = IDAInstance(pid=1234, socket_path="/tmp/ida_ipc_1234", idb_name="test.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [running_instance]
        mock_ipc.query_instance.return_value = running_instance
        mock_ipc.send_open_ida_link.return_value = (True, "OK")

        mock_client = _mock_httpx_client(b"file content")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "functions", ASSET_URL, nav="ea=0x1000&view=pseudocode")
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
            patch("hcli.lib.ida.resolve._print"),
        ):
            _set_handler_env(mock_env, tmp_path)
            KEURLHandler().handle(uri, parsed, no_launch=False, timeout=120.0, skip_analysis=False)

        # Reuse the running instance and forward the nav link WITHOUT url=.
        mock_ipc.send_open_ida_link.assert_called_once_with(
            "/tmp/ida_ipc_1234", "ida://ke/test.i64/functions?ea=0x1000&view=pseudocode"
        )

    @patch("hcli.lib.ida.handler.ke_url_handler._confirm_open_dialog", return_value=True)
    @patch("hcli.lib.ida.handler.ke_url_handler._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.handler.ke_url_handler._dismiss_dialog")
    @patch("hcli.lib.ida.handler.ke_url_handler._cleanup_old_downloads")
    @patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo")
    @patch("hcli.lib.ida.handler.ke_url_handler.httpx.Client")
    @patch("hcli.lib.ida.resolve.IDAIPCClient")
    def test_default_path_pins_ip_and_confirms(
        self, mock_ipc, mock_client_cls, mock_gai, mock_cleanup, mock_dismiss, mock_dialog, mock_confirm, tmp_path
    ):
        """Exercise the SECURITY-CRITICAL DEFAULT path: SSRF validation + IP pinning ON
        and the confirm-before-open gate ON (not the relaxed test-only config)."""
        from hcli.lib.ida.ipc import IDAInstance

        # url= host resolves to a public IP, so the SSRF guard passes and pinning engages.
        mock_gai.return_value = [(2, 1, 6, "", ("93.184.216.34", 443))]

        running = IDAInstance(pid=1234, socket_path="/tmp/ida_ipc_1234", idb_name="test.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [running]
        mock_ipc.query_instance.return_value = running
        mock_ipc.send_open_ida_link.return_value = (True, "OK")

        mock_client = _mock_httpx_client(b"file content")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "functions", ASSET_URL, nav="ea=0x1000")
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
            patch("hcli.lib.ida.resolve._print"),
        ):
            _set_handler_env(mock_env, tmp_path, allow_private=False, skip_confirm=False)
            KEURLHandler().handle(uri, parsed, no_launch=False, timeout=120.0, skip_analysis=False)

        # The confirm-before-open gate actually ran.
        mock_confirm.assert_called_once()

        # The file download connected to the validated IP literal, carrying the real
        # host in the Host header and TLS SNI (the DNS-rebinding pin).
        file_call = mock_client.stream.call_args_list[-1]
        assert "93.184.216.34:8080" in file_call.args[1]
        assert file_call.kwargs["headers"]["Host"] == "host:8080"
        assert file_call.kwargs["extensions"]["sni_hostname"] == "host"

        # And it still downloaded to the hashed dir.
        assert (tmp_path / _ns(ASSET_URL) / "test.i64").read_bytes() == b"file content"

    @patch("hcli.lib.ida.handler.ke_url_handler._confirm_open_dialog", return_value=False)
    @patch("hcli.lib.ida.handler.ke_url_handler._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.handler.ke_url_handler._dismiss_dialog")
    @patch("hcli.lib.ida.handler.ke_url_handler._cleanup_old_downloads")
    @patch("hcli.lib.ida.handler.ke_url_handler.httpx.Client")
    def test_declined_confirmation_downloads_nothing(
        self, mock_client_cls, mock_cleanup, mock_dismiss, mock_dialog, mock_confirm, tmp_path
    ):
        """A passive (declined) ida://ke/... click must write NOTHING to disk — the
        confirm gate runs before any download, killing the drive-by disk-fill."""
        mock_client = _mock_httpx_client(b"file content")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "functions", ASSET_URL, nav="ea=0x1000")
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
            patch("hcli.lib.ida.handler.ke_url_handler.socket.getaddrinfo") as mock_gai,
        ):
            # allow_private=False so a reached _validate_asset_url WOULD resolve — proving
            # the decline path never gets there (no DNS beacon to the attacker host).
            _set_handler_env(mock_env, tmp_path, allow_private=False, skip_confirm=False)
            KEURLHandler().handle(uri, parsed, no_launch=False, timeout=120.0, skip_analysis=False)

        mock_confirm.assert_called_once()
        # No DNS lookup, no HTTP request, no cleanup, no directory creation.
        mock_gai.assert_not_called()
        mock_client.stream.assert_not_called()
        mock_cleanup.assert_not_called()
        assert not (tmp_path / _ns(ASSET_URL)).exists()

    @patch("hcli.lib.ida.handler.ke_url_handler._show_error_dialog")
    def test_missing_url_param_aborts(self, mock_error_dialog):
        # Patch the native dialog so the rejection path doesn't spawn a real
        # notify-send/osascript/PowerShell during the test run.
        uri = "ida://ke/test.i64/functions?ea=0x1000"
        parsed = urlparse(uri)
        with pytest.raises(click.Abort):
            KEURLHandler().handle(uri, parsed, False, 120.0, False)
        mock_error_dialog.assert_called_once()
