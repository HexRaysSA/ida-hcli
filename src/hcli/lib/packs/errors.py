"""The packs error hierarchy and the mapping from transport failures onto it.

Every error states what failed and carries a ``hint``: a suggested fix,
phrased as a suggestion because the classification is a best guess. ``kind``
is a stable slug for scripts and agents (``--json`` prints it); never rename
one, only add new ones.
"""

from __future__ import annotations

import socket
import ssl
from pathlib import Path
from typing import Any, ClassVar, Final, Literal, TypeVar

import httpx

from hcli.lib.api.common import TLSVerificationError

# A pack the Release lists but the portal lacks: usually the portal is still updating.
_MISSING_PACK_HINT: Final = "the portal may be updating: try again in a minute; if it persists, report it to Hex-Rays"


E = TypeVar("E", bound=BaseException)


class PacksError(Exception):
    """Base for all packs errors, so commands can catch one type."""

    kind: ClassVar[str] = "repository_error"

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        # What a multi-step command finished before this failure (``push``
        # and ``sync``: the packs already pushed); ``--json`` prints it.
        self.completed: dict[str, Any] | None = None

    def add_context(self, prefix: str) -> PacksError:
        """Prefix the message with what was being done (``Could not push X: ...``), keeping the class and hint."""
        self.message = f"{prefix}{self.message}"
        self.args = (self.message,)
        return self

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"error": self.message, "kind": self.kind, "hint": self.hint}
        if self.completed is not None:
            data["completed"] = self.completed
        return data


class CredentialsError(PacksError):
    """hcli has no credentials, or the portal refused them (401)."""

    kind = "credentials"


class NetworkError(PacksError):
    """The portal or the storage could not be reached (DNS, connect, timeout)."""

    kind = "network"


class TLSError(PacksError):
    """The endpoint's TLS certificate could not be verified (or the handshake failed)."""

    kind = "tls"


class AccessDeniedError(PacksError):
    """The portal or the storage refused the request (403)."""

    kind = "access_denied"


class ObjectNotFoundError(PacksError):
    """A file is not on the portal, or not in storage (404)."""

    kind = "not_found"


class ReleaseUnavailableError(PacksError):
    """The portal has no stable Release of a format the caller can see: not published yet, or not visible."""

    kind = "release_unavailable"


class IntegrityError(PacksError):
    """Bytes do not match the size/sha256 the Release promises."""

    kind = "integrity"


class UnknownPackError(PacksError):
    """A PACK address or glob matched nothing in the Release."""

    kind = "unknown_pack"


class InvalidReleaseError(PacksError):
    """A Release or sidecar could not be parsed, or uses an unsupported version."""

    kind = "invalid_release"


class LocalIOError(PacksError):
    """A local file operation (cache or output folder) failed: disk full, permissions, a locked file."""

    kind = "local_io"

    def __init__(self, action: str, path: Path, error: OSError, *, directory: Path | None = None) -> None:
        reason = error.strerror or str(error) or type(error).__name__
        directory = directory or path.parent
        super().__init__(
            f"Could not {action} {path}: {reason}.",
            f"check free space and permissions under {directory}; on Windows, close any program that has the file open",
        )


class InvalidServerError(PacksError):
    """The Lumina server URL is missing or unusable."""

    kind = "invalid_server"


class MissingApiKeyError(PacksError):
    """No Lumina API key: neither ``LUMINA_API_KEY`` nor ``--api-key-file``."""

    kind = "missing_api_key"


class PushConfigError(PacksError):
    """A push stopped on its configuration: the key, an untrusted certificate, a server that is not Lumina."""

    kind = "uploader_config"


class ServerRefusedError(PacksError):
    """The Lumina server refused a request: a bad key, or a function over its body cap."""

    kind = "server_refused"


class TransientError(PacksError):
    """A failure that persisted through the push's retries; safe to re-run."""

    kind = "transient"


class PushInterruptedError(PacksError):
    """A push was interrupted (Ctrl-C)."""

    kind = "interrupted"


class InvalidStateError(PacksError):
    """A local push state file could not be parsed."""

    kind = "invalid_state"


class UnsupportedArchError(PacksError):
    """A FLIRT pack's architecture has no known IDA signature folder."""

    kind = "unsupported_arch"


class RequestRefused(Exception):
    """An HTTP refusal, tagged with who refused: the portal's API, or the storage behind a signed link.

    PortalBackend raises it where it makes each request; classify_error turns it into a PacksError.
    """

    def __init__(self, status: int, source: Literal["portal", "storage"]) -> None:
        super().__init__(f"HTTP {status} from the {source}")
        self.status = status
        self.source = source


def find_cause(exc: BaseException, kind: type[E]) -> E | None:
    """The first exception of type ``kind`` in ``exc``'s ``__cause__``/``__context__`` chain (``exc`` included)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, kind):
            return current
        current = current.__cause__ or current.__context__
    return None


def tls_failure(exc: BaseException) -> ssl.SSLCertVerificationError | None:
    """The certificate failure behind ``exc`` (httpcore links the ssl error only as ``__context__``)."""
    return find_cause(exc, ssl.SSLCertVerificationError)


def _tls_error(action: str, host: str, failure: ssl.SSLCertVerificationError) -> TLSError:
    """hcli's own wording for a certificate failure (its fix depends on HCLI_USE_SYSTEM_CERTS)."""
    text = str(TLSVerificationError(host, failure))
    problem, _, fix = text.partition("\n")
    fix = fix.rstrip(".")
    return TLSError(f"Could not {action}: {problem[:1].lower()}{problem[1:]}", f"{fix[:1].lower()}{fix[1:]}" or None)


def _request_host(exc: BaseException, fallback: str) -> str:
    if isinstance(exc, httpx.RequestError):
        try:
            return exc.request.url.host
        except RuntimeError:
            pass  # no request attached
    return fallback


_REFUSALS: Final[dict[tuple[str, int], tuple[type[PacksError], str, str]]] = {
    ("portal", 401): (
        CredentialsError,
        "the portal refused your credentials (HTTP 401)",
        "log in again with `hcli login`, or check HCLI_API_KEY",
    ),
    ("portal", 403): (
        AccessDeniedError,
        "access denied (HTTP 403)",
        "your login or API key needs the assets:read scope",
    ),
    # The portal also answers 404 for a record the caller may not see.
    ("portal", 404): (
        ObjectNotFoundError,
        "not on the portal (or not visible to you), although the stable Release lists it",
        _MISSING_PACK_HINT,
    ),
    ("storage", 403): (
        AccessDeniedError,
        "the storage refused the download link (HTTP 403)",
        "the link may have expired: re-run the command",
    ),
    ("storage", 404): (
        ObjectNotFoundError,
        "the file is missing from storage, although the portal lists it",
        "report it to Hex-Rays",
    ),
}


def classify_error(exc: BaseException, *, action: str, host: str) -> PacksError:
    """Turn a refusal or an httpx/socket failure into a PacksError with a hint.

    ``action`` completes "Could not ...", e.g. ``read lumina/release``;
    ``host`` is the portal's host, named when a failure carries no request.
    """
    if isinstance(exc, PacksError):
        return exc

    if isinstance(exc, RequestRefused):
        if (refusal := _REFUSALS.get((exc.source, exc.status))) is not None:
            error_class, problem, hint = refusal
            return error_class(f"Could not {action}: {problem}.", hint)
        return PacksError(f"Could not {action}: {exc}.")

    # Before the generic connection branch: httpx reports a bad certificate as ConnectError.
    if (failure := tls_failure(exc)) is not None:
        return _tls_error(action, _request_host(exc, host), failure)

    if isinstance(exc, httpx.TransportError):
        return _network_error(exc, action=action, host=_request_host(exc, host))

    if isinstance(exc, ValueError):
        # Not the JSON record (JSONDecodeError, or a pydantic ValidationError). The type
        # only: the details quote the answer, signed download link included.
        return PacksError(f"Could not {action}: the portal sent an unreadable answer ({type(exc).__name__}).")

    # Raw socket failures (the doctor's TCP probe). Never bare OSError here:
    # a local disk failure is not a network problem.
    if isinstance(exc, (socket.gaierror, ConnectionError, TimeoutError)):
        return _network_error(exc, action=action, host=host)

    return PacksError(f"Could not {action}: {type(exc).__name__}: {exc}")


def _network_error(exc: BaseException, *, action: str, host: str) -> NetworkError:
    return NetworkError(
        f"Could not {action}: cannot reach {host} ({type(exc).__name__}).",
        "check your network / VPN connection",
    )
