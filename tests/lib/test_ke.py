"""Tests for KE deep-link handling in the ida:// protocol."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import quote, urlparse

import click
import pytest

from hcli.lib.ida.handler.ke_url_handler import (
    KEURLHandler,
    _cleanup_old_downloads,
    _default_downloads_dir,
    _has_nav_params,
    _idb_name_from_path,
    _ns,
    _strip_query_param,
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


class TestIdbNameFromPath:
    def test_first_segment_is_the_idb(self):
        assert _idb_name_from_path("/test.i64/functions") == "test.i64"

    def test_open_only_path(self):
        assert _idb_name_from_path("/chall.i64/addresses") == "chall.i64"

    def test_percent_encoded_name_decoded(self):
        assert _idb_name_from_path("/my%20file.i64/functions") == "my file.i64"

    def test_empty_path(self):
        assert _idb_name_from_path("") == ""


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


class TestHandleKeUrl:
    @patch("hcli.lib.ida.handler.ke_url_handler._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.handler.ke_url_handler._dismiss_dialog")
    @patch("hcli.lib.ida.handler.ke_url_handler._cleanup_old_downloads")
    @patch("hcli.lib.ida.handler.ke_url_handler.httpx.Client")
    def test_no_launch_downloads_to_hashed_dir(
        self, mock_client_cls, mock_cleanup, mock_dismiss, mock_dialog, tmp_path
    ):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"file content"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "addresses", ASSET_URL)
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
        ):
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            KEURLHandler().handle(uri, parsed, no_launch=True, timeout=120.0, skip_analysis=False)

        # File and its .ke.json sidecar land under the url-hash namespace dir.
        downloaded = tmp_path / _ns(ASSET_URL) / "test.i64"
        assert downloaded.exists()
        assert downloaded.read_bytes() == b"file content"
        assert (tmp_path / _ns(ASSET_URL) / "test.i64.ke.json").exists()

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

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"file content"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = _ke_link("test.i64", "functions", ASSET_URL, nav="ea=0x1000&view=pseudocode")
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.handler.ke_url_handler.ENV") as mock_env,
            patch("hcli.lib.ida.handler.ke_url_handler.time.sleep"),
            patch("hcli.lib.ida.resolve._print"),
        ):
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            KEURLHandler().handle(uri, parsed, no_launch=False, timeout=120.0, skip_analysis=False)

        # Reuse the running instance and forward the nav link WITHOUT url=.
        mock_ipc.send_open_ida_link.assert_called_once_with(
            "/tmp/ida_ipc_1234", "ida://ke/test.i64/functions?ea=0x1000&view=pseudocode"
        )

    def test_missing_url_param_aborts(self):
        uri = "ida://ke/test.i64/functions?ea=0x1000"
        parsed = urlparse(uri)
        with pytest.raises(click.Abort):
            KEURLHandler().handle(uri, parsed, False, 120.0, False)
