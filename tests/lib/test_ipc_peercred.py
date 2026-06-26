"""Tests for IPC peer-credential verification (the socket-squatting defense)."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from hcli.lib.ida.ipc import IDAIPCClient, IPCConnectionError

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="AF_UNIX peer credentials are Unix-only")


@pytest.fixture
def short_dir():
    """A short temp dir for binding AF_UNIX sockets (macOS caps AF_UNIX paths ~104B)."""
    path = tempfile.mkdtemp(prefix="hcli_ipc_", dir="/tmp")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class TestPidFromSocketPath:
    def test_parses_pid(self):
        assert IDAIPCClient._pid_from_socket_path("/tmp/ida_ipc_4242") == 4242
        assert IDAIPCClient._pid_from_socket_path(r"\\.\pipe\ida_ipc_99") == 99

    def test_rejects_non_matching(self):
        assert IDAIPCClient._pid_from_socket_path("/tmp/other_4242") is None
        assert IDAIPCClient._pid_from_socket_path("/tmp/ida_ipc_notanint") is None


class TestVerifyPeer:
    """_verify_peer is the chokepoint guard: reject a peer with a different uid OR a
    pid that doesn't match the one the socket name claims; allow a match; and (best
    effort) allow when the platform can't report a credential."""

    def test_rejects_foreign_uid(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_cred(os.getuid() + 12345, 4242), pytest.raises(IPCConnectionError, match="uid"):
            IDAIPCClient._verify_peer(sock, 4242)
        sock.close()

    def test_rejects_mismatched_pid_same_user(self):
        # Same-user impostor: right uid, but its pid != the pid the socket name claims.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_cred(os.getuid(), 9999), pytest.raises(IPCConnectionError, match="pid"):
            IDAIPCClient._verify_peer(sock, 4242)
        sock.close()

    def test_allows_matching_uid_and_pid(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_cred(os.getuid(), 4242):
            IDAIPCClient._verify_peer(sock, 4242)  # must not raise
        sock.close()

    def test_allows_when_creds_unknown(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_cred(None, None):
            IDAIPCClient._verify_peer(sock, 4242)  # best-effort: must not raise
        sock.close()


class TestPeerCredRealSocket:
    def test_socketpair_reports_self_or_none(self):
        # On a real connected pair the peer is us; the platform must report our
        # uid/pid (or None) — never a different value.
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            uid, pid = IDAIPCClient._peer_cred(a)
            assert uid in (None, os.getuid())
            assert pid in (None, os.getpid())
        finally:
            a.close()
            b.close()


class TestSendCommandEnforcesPeer:
    """The guard lives at the connect chokepoint, so every _send_command goes through
    it — not just discovery."""

    def test_matching_peer_roundtrips(self, short_dir):
        # Server runs in this process, so peer pid == our pid; name the socket after it.
        sock_path = str(short_dir / f"ida_ipc_{os.getpid()}")
        srv = _serve_once(sock_path, response=json.dumps({"status": "ok"}).encode())
        try:
            resp = IDAIPCClient._send_command_unix(sock_path, {"cmd": "ping"})
            assert resp["status"] == "ok"
        finally:
            srv.close()

    def test_foreign_peer_is_rejected_before_send(self, short_dir):
        sock_path = str(short_dir / f"ida_ipc_{os.getpid()}")
        received: list[bytes] = []
        srv = _serve_once(sock_path, response=b'{"status":"ok"}', sink=received)
        try:
            with _force_peer_cred(os.getuid() + 12345, os.getpid()), pytest.raises(IPCConnectionError):
                IDAIPCClient._send_command_unix(sock_path, {"cmd": "open_ida_link", "uri": "ida://secret"})
        finally:
            srv.close()
        assert received == [], "command must not be sent to a foreign-owned peer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_peer_cred(uid, pid):
    """Patch IDAIPCClient._peer_cred to report fixed (uid, pid)."""
    return patch.object(IDAIPCClient, "_peer_cred", staticmethod(lambda sock: (uid, pid)))


def _serve_once(sock_path: str, response: bytes, sink: list | None = None):
    """Bind a one-shot AF_UNIX server that optionally records the client's bytes."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(5)

    def handle():
        try:
            conn, _ = srv.accept()
            data = conn.recv(4096)
            if sink is not None and data:
                sink.append(data)
            conn.sendall(response)
            conn.close()
        except OSError:
            pass

    threading.Thread(target=handle, daemon=True).start()
    return srv
