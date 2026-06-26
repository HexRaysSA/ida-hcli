"""Tests for IPC socket-ownership filtering in instance discovery."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from hcli.lib.ida.ipc import IDAIPCClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Unix domain sockets only")


@pytest.fixture
def short_dir():
    """A short temp dir for binding AF_UNIX sockets.

    macOS caps AF_UNIX paths at ~104 bytes, and pytest's tmp_path under
    /var/folders/... is too long to bind on, so use a short /tmp base instead.
    """
    path = tempfile.mkdtemp(prefix="ke_ipc_", dir="/tmp")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TestIsOwnSocket:
    def test_accepts_own_unix_socket(self, short_dir):
        sock_path = str(short_dir / "ida_ipc_4242")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            assert IDAIPCClient._is_own_socket(sock_path) is True
        finally:
            srv.close()

    def test_rejects_regular_file(self, tmp_path):
        # A non-socket squatting the name (e.g. a planted regular file) is rejected.
        f = tmp_path / "ida_ipc_4242"
        f.write_text("not a socket")
        assert IDAIPCClient._is_own_socket(str(f)) is False

    def test_rejects_symlink(self, short_dir):
        # A planted symlink must not be followed/trusted, even if it points at a socket.
        sock_path = short_dir / "real_sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(str(sock_path))
            link = short_dir / "ida_ipc_4242"
            link.symlink_to(sock_path)
            assert IDAIPCClient._is_own_socket(str(link)) is False
        finally:
            srv.close()

    def test_rejects_missing_path(self, tmp_path):
        assert IDAIPCClient._is_own_socket(str(tmp_path / "nope")) is False

    def test_rejects_socket_owned_by_other_user(self, short_dir, monkeypatch):
        # Simulate a socket owned by a different uid (a cross-user squat).
        sock_path = str(short_dir / "ida_ipc_4242")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(sock_path)
            other_uid = os.getuid() + 12345
            monkeypatch.setattr(os, "getuid", lambda: other_uid)
            assert IDAIPCClient._is_own_socket(sock_path) is False
        finally:
            srv.close()
