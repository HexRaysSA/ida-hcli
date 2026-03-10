"""Tests for KE URL handling in ida:// protocol."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import click
import pytest

from hcli.lib.ida.ke import (
    _cleanup_old_downloads,
    _default_downloads_dir,
    _parse_resource_path,
    _resolve_base_url,
    handle_ke_url,
    is_ke_url,
)


class TestIsKeUrl:
    def test_ke_url_detected(self):
        parsed = urlparse("ida://host:8080/api/v1/buckets/mybucket/resources/mykey")
        assert is_ke_url(parsed) is True

    def test_regular_ida_url_not_detected(self):
        parsed = urlparse("ida://malwares/trojan.i64/functions?rva=0x1000")
        assert is_ke_url(parsed) is False

    def test_relative_ida_url_not_detected(self):
        parsed = urlparse("ida:///myfile.i64/functions?rva=0x1000")
        assert is_ke_url(parsed) is False

    def test_ke_url_with_nested_key(self):
        parsed = urlparse("ida://host/api/v1/buckets/b/resources/path/to/file.idb")
        assert is_ke_url(parsed) is True

    def test_empty_path(self):
        parsed = urlparse("ida://host")
        assert is_ke_url(parsed) is False


class TestParseResourcePath:
    def test_basic_path(self):
        bucket, key = _parse_resource_path("/api/v1/buckets/mybucket/resources/mykey")
        assert bucket == "mybucket"
        assert key == "mykey"

    def test_nested_key(self):
        bucket, key = _parse_resource_path("/api/v1/buckets/mybucket/resources/path/to/file.idb")
        assert bucket == "mybucket"
        assert key == "path/to/file.idb"

    def test_url_encoded_key(self):
        bucket, key = _parse_resource_path("/api/v1/buckets/mybucket/resources/my%20file.idb")
        assert bucket == "mybucket"
        assert key == "my file.idb"

    def test_missing_buckets_raises(self):
        with pytest.raises(click.Abort):
            _parse_resource_path("/api/v1/something/mybucket/resources/mykey")

    def test_missing_resources_raises(self):
        with pytest.raises(click.Abort):
            _parse_resource_path("/api/v1/buckets/mybucket/something/mykey")

    def test_empty_bucket_raises(self):
        with pytest.raises(click.Abort):
            _parse_resource_path("/api/v1/buckets//resources/mykey")

    def test_empty_key_raises(self):
        with pytest.raises(click.Abort):
            _parse_resource_path("/api/v1/buckets/mybucket/resources/")


class TestResolveBaseUrl:
    @patch("hcli.lib.ida.ke.httpx.Client")
    def test_https_available(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = _resolve_base_url("example.com:8080")
        assert result == "https://example.com:8080"

    @patch("hcli.lib.ida.ke.httpx.Client")
    def test_https_unavailable_falls_back_to_http(self, mock_client_cls):
        import httpx

        mock_client = MagicMock()
        mock_client.head.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = _resolve_base_url("example.com:8080")
        assert result == "http://example.com:8080"


class TestDefaultDownloadsDir:
    def test_default_path(self):
        result = _default_downloads_dir()
        assert result.endswith(".ke/downloads")
        assert Path.home().name in result


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

        with patch("hcli.lib.ida.ke.ENV") as mock_env:
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            _cleanup_old_downloads()

        assert not old_file.exists()
        assert new_file.exists()

    def test_cleanup_nonexistent_dir(self, tmp_path):
        with patch("hcli.lib.ida.ke.ENV") as mock_env:
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path / "nonexistent")
            _cleanup_old_downloads()  # should not raise


class TestHandleKeUrl:
    @patch("hcli.lib.ida.ke._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.ke._dismiss_dialog")
    @patch("hcli.lib.ida.ke._cleanup_old_downloads")
    @patch("hcli.lib.ida.ke._resolve_base_url", return_value="https://host:8080")
    @patch("hcli.lib.ida.ke.httpx.Client")
    def test_no_launch_downloads_only(
        self, mock_client_cls, mock_resolve, mock_cleanup, mock_dismiss, mock_dialog, tmp_path
    ):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"file content"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = "ida://host:8080/api/v1/buckets/mybucket/resources/test.idb"
        parsed = urlparse(uri)

        with patch("hcli.lib.ida.ke.ENV") as mock_env, patch("hcli.lib.ida.ke.time.sleep"):
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            handle_ke_url(uri, parsed, no_launch=True, timeout=120.0, skip_analysis=False)

        # File should be downloaded
        downloaded = tmp_path / "mybucket" / "test.idb"
        assert downloaded.exists()
        assert downloaded.read_bytes() == b"file content"

    @patch("hcli.lib.ida.ke._show_download_dialog", return_value=None)
    @patch("hcli.lib.ida.ke._dismiss_dialog")
    @patch("hcli.lib.ida.ke._cleanup_old_downloads")
    @patch("hcli.lib.ida.ke._resolve_base_url", return_value="https://host:8080")
    @patch("hcli.lib.ida.ke.httpx.Client")
    @patch("hcli.lib.ida.resolve.IDAIPCClient")
    def test_reuses_running_instance(
        self, mock_ipc, mock_client_cls, mock_resolve, mock_cleanup, mock_dismiss, mock_dialog, tmp_path
    ):
        """KE should reuse an already-open IDA instance instead of launching a new one."""
        from hcli.lib.ida.ipc import IDAInstance

        # Setup: a running IDA instance already has test.idb open
        running_instance = IDAInstance(pid=1234, socket_path="/tmp/ida_ipc_1234", idb_name="test.idb", has_idb=True)
        mock_ipc.discover_instances.return_value = [running_instance]
        mock_ipc.query_instance.return_value = running_instance
        mock_ipc.send_open_ida_link.return_value = (True, "OK")

        # Setup: HTTP download mock
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"file content"
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        uri = "ida://host:8080/api/v1/buckets/mybucket/resources/test.idb?rva=0x1000"
        parsed = urlparse(uri)

        with (
            patch("hcli.lib.ida.ke.ENV") as mock_env,
            patch("hcli.lib.ida.ke.time.sleep"),
            patch("hcli.lib.ida.resolve._print"),
        ):
            mock_env.HCLI_KE_DOWNLOADS_DIR = str(tmp_path)
            mock_env.HCLI_KE_DOWNLOADS_RETENTION_DAYS = 3
            handle_ke_url(uri, parsed, no_launch=False, timeout=120.0, skip_analysis=False)

        # Should navigate to the running instance, NOT launch a new one
        mock_ipc.send_open_ida_link.assert_called_once_with("/tmp/ida_ipc_1234", uri)

    def test_no_host_aborts(self):
        parsed = urlparse("ida:///api/v1/buckets/b/resources/k")
        # parsed.netloc is empty for triple-slash
        with pytest.raises(click.Abort):
            handle_ke_url("ida:///api/v1/buckets/b/resources/k", parsed, False, 120.0, False)
