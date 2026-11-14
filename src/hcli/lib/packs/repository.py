"""Read access to the data packs through the portal's asset API.

The packs live in the portal's ``packs`` bucket, one record per file, in line
with the pack builder's stable Release::

    <format>/release                                      the stable Release
    <format>/<component>/<platform>-<group>-<arch>/pack   the pack archive
    <format>/<component>/<platform>-<group>-<arch>/contents  its contents sidecar

``GET /api/assets/packs/<key>`` (``asset_api``, with hcli's credentials) answers
the record with a signed download link; the bytes come from that link, streamed
by hcli's API client without credentials.
``RepoBackend`` is the transport seam (tests use an in-memory one);
``PackRepository`` layers the protocol on top — Release and sidecar parsing,
verified downloads, the content-addressed cache — and translates every
failure into a PacksError with a hint, so commands handle exactly one error type.
"""

from __future__ import annotations

import contextlib
import socket
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Final, Literal

import httpx

from hcli.env import ENV
from hcli.lib.api.asset import asset as asset_api
from hcli.lib.api.common import APIError, NotLoggedInError, get_api_client
from hcli.lib.packs.errors import (
    CredentialsError,
    IntegrityError,
    LocalIOError,
    ObjectNotFoundError,
    PacksError,
    ReleaseUnavailableError,
    RequestRefused,
    classify_error,
)
from hcli.lib.packs.models import MetaRecord, PackFormat, Release, ReleasePack, parse_meta, parse_release
from hcli.lib.packs.store import (
    ProgressCallback,
    VerifiedWriter,
    cached_meta_path,
    cached_pack_path,
    verify_bytes,
    verify_file,
    write_verified,
)

if TYPE_CHECKING:
    # Type-only: typing.Self needs Python 3.11 (pydantic brings typing_extensions).
    from typing_extensions import Self

PACKS_BUCKET: Final = "packs"
_CHUNK_SIZE: Final = 1024 * 1024
_CONNECT_TIMEOUT: Final = 10


def release_key(fmt: PackFormat) -> str:
    """The portal key of a format's stable Release."""
    return f"{fmt.value}/release"


def pack_key(fmt: PackFormat, pack: ReleasePack) -> str:
    """The portal key of a pack archive."""
    return f"{fmt.value}/{pack.address}/pack"


def contents_key(fmt: PackFormat, pack: ReleasePack) -> str:
    """The portal key of a pack's contents sidecar."""
    return f"{fmt.value}/{pack.address}/contents"


class RepoBackend(ABC):
    """Raw file access by portal key.

    An HTTP refusal raises RequestRefused, naming who refused; other errors
    propagate untranslated.

    An async context manager: ``aclose`` releases connections (idempotent).
    """

    #: Where the packs are, for display (``list``, ``get --json``, ``doctor``).
    url: str

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:  # noqa: B027 - an optional hook, deliberately not abstract
        """Release connections; the default holds none."""

    @abstractmethod
    async def read(self, key: str) -> bytes:
        """The whole file."""

    @abstractmethod
    def iter_bytes(self, key: str) -> AsyncGenerator[bytes, None]:
        """The file streamed in chunks; ``aclose`` the generator to release the connection early."""

    @abstractmethod
    def endpoint(self) -> tuple[str, int]:
        """The host/port requests go to, for the network check."""


class PortalBackend(RepoBackend):
    """The portal's asset API through hcli's API client; downloads follow the signed links, without credentials."""

    def __init__(self) -> None:
        self.api_url = ENV.HCLI_API_URL.rstrip("/")
        self.url = f"{self.api_url}/api/assets/{PACKS_BUCKET}"

    async def _download_url(self, key: str) -> str:
        """The signed link of the latest record of ``key``."""
        try:
            asset = await asset_api.get_file(PACKS_BUCKET, key)
        except APIError as e:
            raise _refused(e, "portal") from e
        except NotLoggedInError as e:
            raise CredentialsError(
                "Not logged in to the portal.", "log in with `hcli login`, or set HCLI_API_KEY"
            ) from e
        if asset is None or not asset.url:
            raise ObjectNotFoundError(
                f"The portal has a record for {key} but no file to download.",
                "the file is missing from storage; report it to Hex-Rays",
            )
        return asset.url

    @contextlib.asynccontextmanager
    async def _storage(self, key: str) -> AsyncGenerator[httpx.Response, None]:
        """The file behind ``key``'s signed link, as a streamed response (a refusal is the storage's)."""
        url = await self._download_url(key)
        client = await get_api_client()
        try:
            async with client.stream(url) as response:
                yield response
        except APIError as e:
            raise _refused(e, "storage") from e

    async def read(self, key: str) -> bytes:
        async with self._storage(key) as response:
            return await response.aread()

    async def iter_bytes(self, key: str) -> AsyncGenerator[bytes, None]:
        async with self._storage(key) as response:
            async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                yield chunk

    def endpoint(self) -> tuple[str, int]:
        url = httpx.URL(self.api_url)
        return url.host, url.port or (443 if url.scheme == "https" else 80)


def _refused(error: APIError, source: Literal["portal", "storage"]) -> RequestRefused:
    """The API client's refusal, tagged with who refused (the hints differ)."""
    return RequestRefused(error.status_code or 0, source)


class PackRepository:
    """The repository protocol over a backend; every failure is a PacksError.

    An async context manager that closes its backend.
    """

    def __init__(self, backend: RepoBackend | None = None) -> None:
        self.backend = backend or PortalBackend()

    @property
    def url(self) -> str:
        return self.backend.url

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.backend.aclose()

    @contextlib.contextmanager
    def translate(self, action: str) -> Generator[None, None, None]:
        """Re-raise any failure inside the block as a classified PacksError.

        A PacksError raised inside (e.g. LocalIOError or IntegrityError from
        the store) passes through unchanged, never re-classified.
        """
        try:
            yield
        except PacksError:
            raise
        except Exception as e:
            raise classify_error(e, action=action, host=self.backend.endpoint()[0]) from e

    def probe_network(self) -> tuple[str, float]:
        """Open (and close) a TCP connection to the endpoint; returns ``host:port`` and seconds."""
        with self.translate(f"resolve the endpoint of {self.url}"):
            host, port = self.backend.endpoint()
        target = f"{host}:{port}"
        with self.translate(f"connect to {target}"):
            start = time.monotonic()
            with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT):
                elapsed = time.monotonic() - start
        return target, elapsed

    async def read_release(self, fmt: PackFormat) -> Release:
        """The current stable Release of a format (always fetched, never cached)."""
        key = release_key(fmt)
        with self.translate(f"read {key}"):
            try:
                data = await self.backend.read(key)
            except RequestRefused as e:
                if (e.source, e.status) != ("portal", 404):
                    raise
                raise ReleaseUnavailableError(
                    f"The portal has no {fmt.value} Release you can see.",
                    "your account may not have access to the packs; otherwise this format is not published yet",
                ) from e
        return parse_release(data, fmt, key)

    async def read_meta(self, fmt: PackFormat, pack: ReleasePack) -> list[MetaRecord]:
        """A pack's contents sidecar, verified against its receipt and cached by sha256."""
        receipt = pack.meta
        key = contents_key(fmt, pack)
        cached = cached_meta_path(receipt)
        data: bytes | None = None
        if cached.is_file():
            try:
                verify_file(cached, size=receipt.size, sha256=receipt.sha256, label=key)
                data = cached.read_bytes()
            except (IntegrityError, LocalIOError, OSError):
                # Sidecars are small: refetch a damaged cache entry silently
                # (packs, unlike sidecars, report a corrupt cache entry).
                data = None
        if data is None:
            with self.translate(f"read {key}"):
                data = await self.backend.read(key)
            verify_bytes(data, size=receipt.size, sha256=receipt.sha256, label=key)
            # Best effort: an unwritable cache only costs a refetch next time.
            with contextlib.suppress(LocalIOError):
                write_verified([data], cached, size=receipt.size, sha256=receipt.sha256, label=key)
        return parse_meta(data, key)

    async def download_verified(
        self,
        key: str,
        dest: Path,
        *,
        size: int,
        sha256: str,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Stream a file to ``dest``, exposing it only once size + sha256 match.

        Local disk failures surface as LocalIOError, not as repository errors.
        """
        chunks = self.backend.iter_bytes(key)
        try:
            with (
                self.translate(f"download {key}"),
                VerifiedWriter(dest, size=size, sha256=sha256, label=key, on_progress=on_progress) as writer,
            ):
                async for chunk in chunks:
                    writer.write(chunk)
        finally:
            # Releases the connection, also when the writer stopped reading early.
            await chunks.aclose()

    def needs_download(self, pack: ReleasePack, force: bool = False) -> bool:
        """Whether ``fetch_pack`` will download (rather than re-verify the cache entry)."""
        return force or not cached_pack_path(pack).is_file()

    async def fetch_pack(
        self,
        fmt: PackFormat,
        pack: ReleasePack,
        *,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[Path, bool]:
        """Ensure a verified copy of ``pack`` is in the cache.

        Returns the cache path and whether it was downloaded (False: an
        existing entry re-verified by sha256). A cached entry that fails
        verification raises IntegrityError rather than being replaced
        silently; ``force`` re-downloads unconditionally.
        """
        cached = cached_pack_path(pack)
        key = pack_key(fmt, pack)
        if not self.needs_download(pack, force):
            verify_file(cached, size=pack.size, sha256=pack.sha256, label=f"Cached {key}")
            return cached, False
        await self.download_verified(key, cached, size=pack.size, sha256=pack.sha256, on_progress=on_progress)
        return cached, True
