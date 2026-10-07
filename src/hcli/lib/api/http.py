"""HTTP access shared by all of hcli.

Kept free of auth imports so that every HTTP user, including the auth client
itself, can build on it.
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

from hcli import USER_AGENT
from hcli.env import ENV


class NetworkError(Exception):
    """A request got no response: the connection failed or timed out."""

    def __init__(self, message: str, host: str):
        super().__init__(message)
        self.host = host


class TLSVerificationError(NetworkError):
    """The server's TLS certificate could not be verified."""

    def __init__(self, host: str, cause: ssl.SSLCertVerificationError):
        if ENV.HCLI_USE_SYSTEM_CERTS:
            fix = "install its root certificate in the operating system certificate store, or set SSL_CERT_FILE to it"
        else:
            fix = "unset HCLI_USE_SYSTEM_CERTS to use the operating system certificate store, or set SSL_CERT_FILE"
        reason = getattr(cause, "verify_message", None) or cause
        super().__init__(
            f"Cannot verify the TLS certificate of {host}: {reason}.\n"
            f"If your network uses a proxy that inspects HTTPS traffic, {fix}.",
            host,
        )


def _convert_transport_error(error: httpx.TransportError, request: httpx.Request) -> NetworkError | None:
    """The domain exception for a request that got no response, or None to keep *error* as is."""
    host = request.url.host
    if isinstance(error, httpx.ConnectError):
        # httpx.ConnectError <- httpcore.ConnectError <- ssl.SSLCertVerificationError;
        # httpcore links the ssl error only as __context__, not __cause__.
        cause: BaseException | None = error
        while cause is not None and not isinstance(cause, ssl.SSLCertVerificationError):
            cause = cause.__cause__ or cause.__context__
        if cause is not None:
            return TLSVerificationError(host, cause)
        # httpx connection errors often stringify to "".
        return NetworkError(f"Cannot connect to {host}" + (f": {error}" if str(error) else "."), host)
    if isinstance(error, httpx.TimeoutException):
        return NetworkError(f"The request to {host} timed out.", host)
    return None


class _SyncClient(httpx.Client):
    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            return super().send(request, **kwargs)
        except httpx.TransportError as e:
            converted = _convert_transport_error(e, request)
            if converted is None:
                raise
            raise converted from e


class _AsyncClient(httpx.AsyncClient):
    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        try:
            return await super().send(request, **kwargs)
        except httpx.TransportError as e:
            converted = _convert_transport_error(e, request)
            if converted is None:
                raise
            raise converted from e


class HTTPClient:
    """HTTP access shared by all of hcli: one User-Agent, one way to report connection failures.

    Offers a sync and an async httpx client, created on first use, because the API
    code is async while the plugin and GitHub code is sync. Both raise NetworkError,
    or its TLSVerificationError subclass, when a request gets no response.
    """

    def __init__(self, base_url: str = "", timeout: httpx.Timeout | float = 60.0):
        self._base_url = base_url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._sync_client: httpx.Client | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = _AsyncClient(
                base_url=self._base_url, timeout=self._timeout, headers={"User-Agent": USER_AGENT}
            )
        return self._client

    @property
    def sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            self._sync_client = _SyncClient(
                base_url=self._base_url, timeout=self._timeout, headers={"User-Agent": USER_AGENT}
            )
        return self._sync_client

    def close(self) -> None:
        if self._sync_client is not None:
            self._sync_client.close()
            self._sync_client = None

    async def aclose(self) -> None:
        self.close()
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()
