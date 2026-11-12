"""classify_error: every transport failure becomes a PacksError that names what failed and suggests a fix."""

from __future__ import annotations

import socket
import ssl
from pathlib import Path
from typing import Literal

import httpx
import pytest

from hcli.lib.packs.errors import LocalIOError, PacksError, RequestRefused, UnknownPackError, classify_error

PORTAL_HOST = "portal.test"
RELEASE = "lumina/release"


def _classify(exc: BaseException) -> PacksError:
    return classify_error(exc, action=f"read {RELEASE}", host=PORTAL_HOST)


def _verify_error() -> ssl.SSLCertVerificationError:
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    error.verify_message = "self-signed certificate in certificate chain"
    return error


@pytest.mark.parametrize(
    ("source", "status", "kind", "words"),
    [
        ("portal", 401, "credentials", "hcli login"),
        ("portal", 403, "access_denied", "assets:read"),
        ("portal", 404, "not_found", "try again in a minute"),
        ("storage", 403, "access_denied", "link may have expired"),
        ("storage", 404, "not_found", "report it to Hex-Rays"),
    ],
)
def test_refusals_are_blamed_on_who_refused(
    source: Literal["portal", "storage"], status: int, kind: str, words: str
) -> None:
    error = _classify(RequestRefused(status, source))
    assert error.kind == kind
    assert error.message.startswith(f"Could not read {RELEASE}: ")
    assert error.hint is not None and words in error.hint


def test_storage_and_portal_messages_differ_for_the_same_status() -> None:
    portal = _classify(RequestRefused(404, "portal")).message
    storage = _classify(RequestRefused(404, "storage")).message
    assert "not on the portal" in portal and "missing from storage" in storage


@pytest.mark.parametrize("source", ["portal", "storage"])
def test_an_unknown_status_names_its_source(source: Literal["portal", "storage"]) -> None:
    error = _classify(RequestRefused(502, source))
    assert error.kind == "repository_error"
    assert error.message == f"Could not read {RELEASE}: HTTP 502 from the {source}."


def test_httpx_errors_name_the_request_host() -> None:
    request = httpx.Request("GET", "https://bucket.s3.amazonaws.com/x")
    error = _classify(httpx.ReadTimeout("slow", request=request))
    assert error.kind == "network"
    assert "cannot reach bucket.s3.amazonaws.com (ReadTimeout)" in error.message
    assert error.hint == "check your network / VPN connection"


@pytest.mark.parametrize("exc", [ConnectionRefusedError(61, "refused"), socket.gaierror(8, "nodename"), TimeoutError()])
def test_raw_socket_failures_name_the_portal(exc: BaseException) -> None:
    error = _classify(exc)
    assert error.kind == "network"
    assert f"cannot reach {PORTAL_HOST}" in error.message


def test_certificate_failure_in_the_context_chain_is_tls() -> None:
    request = httpx.Request("GET", f"https://{PORTAL_HOST}/api/assets/packs/{RELEASE}")
    # httpcore links the ssl error as __context__ only.
    try:
        try:
            raise _verify_error()
        except ssl.SSLError:
            raise httpx.ConnectError("handshake", request=request)
    except httpx.ConnectError as e:
        error = _classify(e)
    assert error.kind == "tls"
    assert error.message == (
        f"Could not read {RELEASE}: cannot verify the TLS certificate of {PORTAL_HOST}: "
        "self-signed certificate in certificate chain."
    )
    assert error.hint is not None and error.hint.startswith("if your network uses a proxy")
    assert "SSL_CERT_FILE" in error.hint and not error.hint.endswith(".")


def test_a_plain_ssl_error_is_not_a_certificate_failure() -> None:
    request = httpx.Request("GET", f"https://{PORTAL_HOST}/x")
    try:
        try:
            raise ssl.SSLError(1, "wrong version number")
        except ssl.SSLError:
            raise httpx.ConnectError("handshake", request=request)
    except httpx.ConnectError as e:
        assert _classify(e).kind == "network"


@pytest.mark.parametrize("exc", [PermissionError(13, "Permission denied"), OSError(28, "No space left on device")])
def test_bare_os_errors_are_not_network_errors(exc: OSError) -> None:
    assert _classify(exc).kind == "repository_error"


def test_local_io_errors_pass_through_unchanged(tmp_path: Path) -> None:
    original = LocalIOError("write", tmp_path / "x.tar.zst", OSError(28, "No space left on device"))
    error = _classify(original)
    assert error is original
    assert error.message == f"Could not write {tmp_path / 'x.tar.zst'}: No space left on device."
    assert error.hint is not None and "close any program" in error.hint


def test_packs_errors_pass_through() -> None:
    original = UnknownPackError("x", "y")
    assert _classify(original) is original


def test_unexpected_errors_keep_their_type_name() -> None:
    error = _classify(RuntimeError("boom"))
    assert type(error) is PacksError
    assert error.to_json() == {
        "error": f"Could not read {RELEASE}: RuntimeError: boom",
        "kind": "repository_error",
        "hint": None,
    }
