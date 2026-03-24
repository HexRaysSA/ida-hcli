"""Tests for DefaultURLHandler — standard ida:// URL handling."""

from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import click
import pytest

from hcli.lib.ida.handler.default_url_handler import DefaultURLHandler
from hcli.lib.ida.ipc import IDAInstance


class TestMatches:
    def test_matches_any_url(self):
        handler = DefaultURLHandler()
        assert handler.matches(urlparse("ida:///file.i64/functions?rva=0x0")) is True
        assert handler.matches(urlparse("ida://source/file.i64/functions?rva=0x0")) is True
        assert handler.matches(urlparse("ida://host")) is True


class TestUrlValidation:
    def test_rejects_non_ida_scheme(self):
        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("http://example.com", urlparse("http://example.com"), False, 120.0, False)

    def test_rejects_empty_path_no_query(self):
        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("ida://host", urlparse("ida://host"), False, 120.0, False)

    def test_rejects_single_segment_no_query(self):
        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("ida:///file.i64", urlparse("ida:///file.i64"), False, 120.0, False)


class TestRelativeUrl:
    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    @patch("hcli.lib.ida.handler.default_url_handler._print")
    def test_single_instance_navigates(self, mock_print, mock_ipc):
        """Relative URL with one running instance should navigate to it."""
        instance = IDAInstance(pid=100, socket_path="/tmp/ida_ipc_100", idb_name="test.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [instance]
        mock_ipc.query_instance.return_value = instance
        mock_ipc.send_open_ida_link.return_value = (True, "OK")

        handler = DefaultURLHandler()
        uri = "ida:///functions?rva=0x1000"
        handler.handle(uri, urlparse(uri), False, 120.0, False)

        mock_ipc.send_open_ida_link.assert_called_once_with("/tmp/ida_ipc_100", uri)

    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    def test_no_instances_aborts(self, mock_ipc):
        """Relative URL with no running instances should abort."""
        mock_ipc.discover_instances.return_value = []

        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("ida:///functions?rva=0x1000", urlparse("ida:///functions?rva=0x1000"), False, 120.0, False)

    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    def test_multiple_instances_aborts(self, mock_ipc):
        """Relative URL with multiple running instances should abort."""
        inst1 = IDAInstance(pid=100, socket_path="/tmp/ida_ipc_100", idb_name="a.i64", has_idb=True)
        inst2 = IDAInstance(pid=200, socket_path="/tmp/ida_ipc_200", idb_name="b.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [inst1, inst2]
        mock_ipc.query_instance.side_effect = lambda p: inst1 if "100" in p else inst2

        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("ida:///functions?rva=0x1000", urlparse("ida:///functions?rva=0x1000"), False, 120.0, False)

    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    @patch("hcli.lib.ida.handler.default_url_handler._print")
    def test_navigation_failure_aborts(self, mock_print, mock_ipc):
        """Relative URL where IPC navigation fails should abort."""
        instance = IDAInstance(pid=100, socket_path="/tmp/ida_ipc_100", idb_name="test.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [instance]
        mock_ipc.query_instance.return_value = instance
        mock_ipc.send_open_ida_link.return_value = (False, "IDA error")

        handler = DefaultURLHandler()
        with pytest.raises(click.Abort):
            handler.handle("ida:///functions?rva=0x1000", urlparse("ida:///functions?rva=0x1000"), False, 120.0, False)


class TestNamedIdb:
    @patch("hcli.lib.ida.handler.default_url_handler.resolve_and_navigate")
    @patch("hcli.lib.ida.handler.default_url_handler.IDALauncher")
    def test_found_idb_delegates_to_resolve(self, mock_launcher_cls, mock_resolve):
        """Named IDB found on disk should delegate to resolve_and_navigate."""
        mock_launcher = MagicMock()
        mock_launcher.find_idb_file.return_value = "/some/path/test.i64"
        mock_launcher_cls.return_value = mock_launcher

        handler = DefaultURLHandler()
        uri = "ida:///test.i64/functions?rva=0x1000"
        handler.handle(uri, urlparse(uri), False, 120.0, False)

        mock_resolve.assert_called_once_with(
            uri=uri,
            target_idb_name="test.i64",
            idb_path="/some/path/test.i64",
            no_launch=False,
            timeout=120.0,
            skip_analysis=False,
        )

    @patch("hcli.lib.ida.handler.default_url_handler.resolve_and_navigate")
    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    @patch("hcli.lib.ida.handler.default_url_handler.IDALauncher")
    def test_not_found_but_running_delegates_without_path(self, mock_launcher_cls, mock_ipc, mock_resolve):
        """Named IDB not on disk but running in IDA should delegate with idb_path=None."""
        mock_launcher = MagicMock()
        mock_launcher.find_idb_file.return_value = None
        mock_launcher_cls.return_value = mock_launcher

        running = IDAInstance(pid=100, socket_path="/tmp/ida_ipc_100", idb_name="test.i64", has_idb=True)
        mock_ipc.discover_instances.return_value = [running]
        mock_ipc.query_instance.return_value = running

        handler = DefaultURLHandler()
        uri = "ida:///test.i64/functions?rva=0x1000"
        handler.handle(uri, urlparse(uri), False, 120.0, False)

        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["idb_path"] is None

    @patch("hcli.lib.ida.handler.default_url_handler.resolve_and_navigate")
    @patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
    @patch("hcli.lib.ida.handler.default_url_handler.IDALauncher")
    def test_named_source_passed_to_find(self, mock_launcher_cls, mock_ipc, mock_resolve):
        """Named source in URL should be passed to find_idb_file."""
        mock_launcher = MagicMock()
        mock_launcher.find_idb_file.return_value = "/src/test.i64"
        mock_launcher_cls.return_value = mock_launcher

        handler = DefaultURLHandler()
        uri = "ida://malwares/test.i64/functions?rva=0x1000"
        handler.handle(uri, urlparse(uri), False, 120.0, False)

        mock_launcher.find_idb_file.assert_called_once_with("test.i64", "malwares")
