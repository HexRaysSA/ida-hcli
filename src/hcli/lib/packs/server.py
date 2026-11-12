"""The private Lumina server packs are pushed to: URL, API key, and read-only probes.

``HCLI_LUMINA_SERVER`` (or ``--server``) is the REST base URL; pushes go to
``/api/metadata``. The API key comes from ``LUMINA_API_KEY`` or
``--api-key-file`` and is never printed.
``HCLI_LUMINA_CA_CERT`` (or ``--ca-cert``) adds trusted CA certificates for a
server with a private CA, for the probes here and for the pushes.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

import httpx

from hcli import USER_AGENT
from hcli.env import ENV
from hcli.lib.packs.errors import (
    AccessDeniedError,
    InvalidServerError,
    LocalIOError,
    MissingApiKeyError,
    NetworkError,
    PacksError,
    PushConfigError,
    ServerRefusedError,
    TLSError,
    TransientError,
    tls_failure,
)

SERVER_ENV: Final = "HCLI_LUMINA_SERVER"
API_KEY_ENV: Final = "LUMINA_API_KEY"
CA_CERT_ENV: Final = "HCLI_LUMINA_CA_CERT"
READY_PATH: Final = "api/health/ready"
LOOKUP_PATH: Final = "api/metadata/lookup"
# A pattern no function hashes to in practice: the lookup validates the key without writing.
PROBE_PATTERN: Final = "00112233445566778899aabbccddeeff"
_TIMEOUT: Final = httpx.Timeout(15, connect=10)
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
_API_SUFFIXES: Final = ("/api", "/api/metadata")


@dataclass(frozen=True)
class LuminaServer:
    """A Lumina REST base URL, normalized: lowercase scheme and host, no default port, no trailing slash.

    ``url`` is also the server's identity for the push state.
    """

    url: str
    scheme: str
    host: str

    def url_for(self, relative: str) -> str:
        return f"{self.url}/{relative.lstrip('/')}"


def _server_hint() -> str:
    return f"--server https://lumina.example.com or export {SERVER_ENV}=https://lumina.example.com"


def base_url_hint(url: str) -> str:
    """For a server answering a redirect or 404: the URL is probably not the REST base."""
    return (
        f"check that --server / {SERVER_ENV} ({url}) is the Lumina REST base URL "
        "(/api/metadata is appended); http->https redirects are not followed"
    )


def key_hint(url: str | None = None) -> str:
    """For a refused key (401/403); a URL that already ends in an API path is the likelier cause.

    The Lumina server answers unknown ``/api/metadata/...`` paths with 403
    (admin only), so ``--server .../api/metadata`` looks like a bad key.
    """
    hint = f"check that {API_KEY_ENV} (or --api-key-file) is a valid importer key for this server"
    if url is not None and urlsplit(url).path.endswith(_API_SUFFIXES):
        return f"{base_url_hint(url)}; otherwise {hint}"
    return hint


def parse_server(value: str) -> LuminaServer:
    """Parse and normalize a server base URL, as InvalidServerError when unusable."""
    raw = value.strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as e:
        raise InvalidServerError(f"Invalid Lumina server URL {value!r}: {e}.", _server_hint()) from e
    scheme = parts.scheme.lower()

    def invalid(reason: str) -> InvalidServerError:
        return InvalidServerError(f"Invalid Lumina server URL {value!r}: {reason}.", _server_hint())

    if scheme not in _DEFAULT_PORTS:
        raise invalid("expected an https:// (or http://) URL")
    if not parts.hostname:
        raise invalid("the URL has no host")
    if parts.username or parts.password:
        raise invalid(f"credentials in the URL are not accepted; use {API_KEY_ENV} or --api-key-file")
    if parts.query or parts.fragment:
        raise invalid("the base URL must not have a query or fragment")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"  # IPv6 literal
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        host = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return LuminaServer(url=f"{scheme}://{host}{path}", scheme=scheme, host=host)


def server_from_option(value: str | None) -> LuminaServer:
    """The server from ``--server`` / ``HCLI_LUMINA_SERVER`` (click resolves the env var)."""
    if not value:
        raise InvalidServerError("No Lumina server given.", _server_hint())
    return parse_server(value)


def server_from_env() -> LuminaServer | None:
    """``HCLI_LUMINA_SERVER`` parsed, or None when unset (raises when set but invalid)."""
    value = ENV.HCLI_LUMINA_SERVER
    return parse_server(value) if value else None


_KEY_HINT: Final = f"put the importer key the Lumina server gave you in {API_KEY_ENV} or --api-key-file"


@dataclass(frozen=True)
class ApiKey:
    """Where the API key comes from: a file path or the environment's value."""

    path: Path | None = None
    value: str | None = None

    @property
    def source(self) -> str:
        return str(self.path) if self.path is not None else API_KEY_ENV

    def __repr__(self) -> str:
        # Never let the key reach logs or tracebacks.
        return f"ApiKey(source={self.source!r})"

    def read(self) -> str:
        """The key itself, stripped; it must be non-empty and header-safe (printable ASCII, no spaces)."""
        if self.path is not None:
            try:
                key = self.path.read_text(encoding="utf-8").strip()
            except OSError as e:
                raise LocalIOError("read", self.path, e) from e
            except UnicodeDecodeError as e:
                raise PushConfigError(
                    f"{self.source}: API key has characters outside printable ASCII", _KEY_HINT
                ) from e
        else:
            key = (self.value or "").strip()
        if not key:
            raise PushConfigError(f"{self.source}: API key is empty", _KEY_HINT)
        if not all(33 <= ord(char) <= 126 for char in key):
            raise PushConfigError(f"{self.source}: API key has characters outside printable ASCII", _KEY_HINT)
        return key


def missing_api_key() -> MissingApiKeyError:
    return MissingApiKeyError(
        f"No Lumina API key: {API_KEY_ENV} is not set and no --api-key-file was given.",
        f"export {API_KEY_ENV}=<importer key for the server>, or pass --api-key-file <file>",
    )


def resolve_api_key(api_key_file: Path | None) -> ApiKey:
    """``--api-key-file`` (preferred) or ``LUMINA_API_KEY``, else MissingApiKeyError."""
    if api_key_file is not None:
        return ApiKey(path=api_key_file)
    value = ENV.LUMINA_API_KEY or ""
    if value.strip():
        return ApiKey(value=value)
    raise missing_api_key()


def ca_cert_from_env() -> Path | None:
    """``HCLI_LUMINA_CA_CERT``, if set (set-but-empty counts as unset)."""
    value = ENV.HCLI_LUMINA_CA_CERT
    return Path(value).expanduser() if value else None


def tls_hint() -> str:
    return (
        f"pass --ca-cert <PEM> or set {CA_CERT_ENV} to the server's CA bundle, or install the CA "
        "in the operating system certificate store (or set SSL_CERT_FILE)"
    )


def _verify(ca_cert: Path | None) -> ssl.SSLContext | bool:
    """httpx's default trust, plus ``ca_cert`` (``--ca-cert`` adds to the roots, never replaces them)."""
    if ca_cert is None:
        return True
    context = httpx.create_ssl_context()
    try:
        context.load_verify_locations(cafile=ca_cert)
    except ssl.SSLError as e:
        raise TLSError(
            f"Could not load CA certificates from {ca_cert}: {e}.",
            f"point --ca-cert / {CA_CERT_ENV} at a PEM file of CA certificates",
        ) from e
    except OSError as e:
        raise LocalIOError("read", ca_cert, e) from e
    return context


def lumina_client(
    transport: httpx.BaseTransport | None, ca_cert: Path | None = None, timeout: httpx.Timeout = _TIMEOUT
) -> httpx.Client:
    """A client for the Lumina server: hcli's user agent, ``ca_cert`` trusted too, redirects never followed."""
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        transport=transport,
        follow_redirects=False,
        verify=_verify(ca_cert),
    )


def _request_error(e: httpx.RequestError, server: LuminaServer, action: str) -> PacksError:
    if (failure := tls_failure(e)) is not None:
        return TLSError(
            f"Could not {action}: cannot verify the TLS certificate of {server.host}: {failure}.", tls_hint()
        )
    return NetworkError(
        f"Could not {action}: cannot reach {server.host} ({type(e).__name__}).",
        f"check that the Lumina server is running and {SERVER_ENV}={server.url} is its REST base URL",
    )


def probe_ready(server: LuminaServer, transport: httpx.BaseTransport | None = None, ca_cert: Path | None = None) -> str:
    """GET ``/api/health/ready``; returns a detail line, raises a PacksError unless ``ready`` is true."""
    url = server.url_for(READY_PATH)
    action = f"check {url}"
    try:
        with lumina_client(transport, ca_cert) as client:
            response = client.get(url)
    except httpx.RequestError as e:
        raise _request_error(e, server, action) from e
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.is_success and isinstance(payload, dict) and payload.get("ready") is True:
        return f"{server.url} ready"
    reason = f"HTTP {response.status_code}" if not response.is_success else f"answered {response.text[:200]!r}"
    raise PacksError(
        f"Lumina server {server.url} is not ready: {reason}.",
        f"check that {SERVER_ENV}={server.url} is the Lumina REST base URL and the server's database is up",
    )


def _server_message(response: httpx.Response) -> str:
    """``: <message>`` from a Lumina JSON error body, or nothing."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    message = payload.get("message") if isinstance(payload, dict) else None
    return f": {message[:200]}" if isinstance(message, str) and message else ""


def check_api_key(
    server: LuminaServer, key: str, transport: httpx.BaseTransport | None = None, ca_cert: Path | None = None
) -> httpx.Response:
    """A read-only metadata lookup with ``key``; raises a PacksError unless the server answers 2xx.

    ``push`` runs it before downloading anything, so a wrong key, URL or CA
    fails in a second instead of after a pack download.
    """
    url = server.url_for(LOOKUP_PATH)
    action = f"look up a test pattern at {url}"
    try:
        with lumina_client(transport, ca_cert) as client:
            response = client.post(url, json={"patterns": [PROBE_PATTERN]}, headers={"Authorization": f"Bearer {key}"})
    except httpx.RequestError as e:
        raise _request_error(e, server, action) from e
    status = response.status_code
    if status in (401, 403):
        raise AccessDeniedError(
            f"The Lumina server refused the API key (HTTP {status}{_server_message(response)}).", key_hint(server.url)
        )
    if response.is_redirect or status == 404:
        location = response.headers.get("Location")
        where = f" (redirect to {location})" if response.is_redirect and location else ""
        raise InvalidServerError(f"Could not {action}: HTTP {status}{where}.", base_url_hint(server.url))
    if response.is_server_error:
        raise TransientError(
            f"Could not {action}: HTTP {status}.", "check that the server is up and its database is reachable"
        )
    if not response.is_success:
        raise ServerRefusedError(
            f"Could not {action}: HTTP {status}: {response.text[:200]!r}.",
            f"check that {SERVER_ENV}={server.url} is a next-gen Lumina server",
        )
    return response


def probe_key(
    server: LuminaServer, key: str, transport: httpx.BaseTransport | None = None, ca_cert: Path | None = None
) -> str:
    """Validate ``key`` with a read-only metadata lookup that should answer NOT_FOUND."""
    response = check_api_key(server, key, transport, ca_cert)
    try:
        codes = [result.get("code") for result in response.json().get("results", [])]
    except (ValueError, AttributeError):
        codes = []
    if codes != ["NOT_FOUND"]:
        raise PacksError(
            f"Unexpected answer from {server.url_for(LOOKUP_PATH)}: {response.text[:200]!r}.",
            f"check that {SERVER_ENV}={server.url} is a next-gen Lumina server",
        )
    return "accepted (read-only lookup)"
