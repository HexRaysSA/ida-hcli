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


class TestVerifyPeerUid:
    """_verify_peer_uid is the chokepoint guard: reject a peer owned by another uid,
    allow same-uid, and (best-effort) allow when the platform can't report it."""

    def test_rejects_foreign_peer(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_uid(os.getuid() + 12345), pytest.raises(IPCConnectionError, match="refusing IPC peer"):
            IDAIPCClient._verify_peer_uid(sock)
        sock.close()

    def test_allows_same_uid_peer(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_uid(os.getuid()):
            IDAIPCClient._verify_peer_uid(sock)  # must not raise
        sock.close()

    def test_allows_when_peer_unknown(self):
        # Best-effort: an unsupported platform / failed lookup must not break the flow.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with _force_peer_uid(None):
            IDAIPCClient._verify_peer_uid(sock)  # must not raise
        sock.close()


class TestPeerUidRealSocket:
    def test_socketpair_reports_self_or_none(self):
        # On a real connected AF_UNIX pair the peer is us; the platform must report
        # either our uid or None (best-effort) — never a different uid.
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            uid = IDAIPCClient._peer_uid(a)
            assert uid is None or uid == os.getuid()
        finally:
            a.close()
            b.close()


class TestSendCommandEnforcesPeer:
    """The guard lives at the connect chokepoint, so every _send_command goes through
    it — not just discovery."""

    def test_same_uid_peer_roundtrips(self, short_dir):
        sock_path = str(short_dir / "ida_ipc_4242")
        srv = _serve_once(sock_path, response=json.dumps({"status": "ok", "pong": True}).encode())
        try:
            resp = IDAIPCClient._send_command_unix(sock_path, {"cmd": "ping"})
            assert resp["status"] == "ok"
        finally:
            srv.close()

    def test_foreign_peer_is_rejected_before_send(self, short_dir):
        # Same real server, but pretend our uid differs so the live peer looks foreign.
        # _send_command must refuse without delivering the command.
        sock_path = str(short_dir / "ida_ipc_4242")
        received: list[bytes] = []
        srv = _serve_once(sock_path, response=b'{"status":"ok"}', sink=received)
        try:
            with _force_peer_uid(os.getuid() + 12345), pytest.raises(IPCConnectionError, match="refusing IPC peer"):
                IDAIPCClient._send_command_unix(sock_path, {"cmd": "open_ida_link", "uri": "ida://secret"})
        finally:
            srv.close()
        assert received == [], "command must not be sent to a foreign-owned peer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _force_peer_uid(value):
    """Patch IDAIPCClient._peer_uid to report a fixed uid (or None)."""
    return patch.object(IDAIPCClient, "_peer_uid", staticmethod(lambda sock: value))


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
