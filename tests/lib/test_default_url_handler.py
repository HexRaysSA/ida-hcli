"""Tests for DefaultURLHandler — standard ida:// URL handling."""

from unittest.mock import patch
from urllib.parse import urlparse

import click
import pytest

from hcli.lib.ida.handler.default_url_handler import DefaultURLHandler
from hcli.lib.ida.ipc import IDAInstance

RELATIVE_URI = "ida:///functions?rva=0x1000"


def handle(uri: str) -> None:
    DefaultURLHandler().handle(uri, urlparse(uri), False, 120.0, False)


def stub_instances(mock_ipc, instances: list[IDAInstance]) -> None:
    mock_ipc.discover_instances.return_value = instances
    mock_ipc.query_instance.side_effect = {i.socket_path: i for i in instances}.get


def make_instance(pid: int, idb_name: str) -> IDAInstance:
    return IDAInstance(pid=pid, socket_path=f"/tmp/ida_ipc_{pid}", idb_name=idb_name, has_idb=True)


@pytest.mark.parametrize("uri", ["http://example.com", "ida://host"])
def test_rejects_unsupported_url(uri):
    with pytest.raises(click.Abort):
        handle(uri)


@patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
def test_relative_url_navigates_the_single_instance(mock_ipc):
    stub_instances(mock_ipc, [make_instance(100, "test.i64")])
    mock_ipc.send_open_ida_link.return_value = (True, "OK")

    handle(RELATIVE_URI)

    mock_ipc.send_open_ida_link.assert_called_once_with("/tmp/ida_ipc_100", RELATIVE_URI)


@pytest.mark.parametrize("instance_count", [0, 2])
@patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
def test_relative_url_needs_exactly_one_instance(mock_ipc, instance_count):
    stub_instances(mock_ipc, [make_instance(100 + i, f"{i}.i64") for i in range(instance_count)])

    with pytest.raises(click.Abort):
        handle(RELATIVE_URI)

    mock_ipc.send_open_ida_link.assert_not_called()


@patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
def test_relative_url_aborts_when_navigation_fails(mock_ipc):
    stub_instances(mock_ipc, [make_instance(100, "test.i64")])
    mock_ipc.send_open_ida_link.return_value = (False, "IDA error")

    with pytest.raises(click.Abort):
        handle(RELATIVE_URI)


@pytest.mark.parametrize("found_path", ["/some/path/test.i64", None])
@patch("hcli.lib.ida.handler.default_url_handler.resolve_and_navigate")
@patch("hcli.lib.ida.handler.default_url_handler.IDAIPCClient")
@patch("hcli.lib.ida.handler.default_url_handler.IDALauncher")
def test_named_idb_delegates_with_the_resolved_path(mock_launcher_cls, mock_ipc, mock_resolve, found_path):
    """The IDB may be on disk or only open in a running instance; either way the URL's
    IDB name and whatever path was found are handed to resolve_and_navigate."""
    mock_launcher_cls.return_value.find_idb_file.return_value = found_path
    stub_instances(mock_ipc, [make_instance(100, "test.i64")])

    handle("ida:///test.i64/functions?rva=0x1000")

    kwargs = mock_resolve.call_args.kwargs
    assert kwargs["target_idb_name"] == "test.i64"
    assert kwargs["idb_path"] == found_path


@patch("hcli.lib.ida.handler.default_url_handler.resolve_and_navigate")
@patch("hcli.lib.ida.handler.default_url_handler.IDALauncher")
def test_named_source_is_passed_to_the_idb_search(mock_launcher_cls, mock_resolve):
    launcher = mock_launcher_cls.return_value
    launcher.find_idb_file.return_value = "/src/test.i64"

    handle("ida://malwares/test.i64/functions?rva=0x1000")

    launcher.find_idb_file.assert_called_once_with("test.i64", "malwares")
