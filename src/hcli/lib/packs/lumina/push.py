"""Posting a member's rows to a Lumina server's ``/api/metadata``, as the pack builder's importer does:

- eligibility: a non-empty name and IDA's default minimum function size (32);
- collision groups: rows sharing an MD5 with different (capped) names are all
  dropped, identical ones collapse to the first;
- batches by an estimated JSON byte budget; a 413 halves the budget and
  re-splits the batch, a 413 on one function is a refusal;
- 429 and 5xx retry up to 5 times with a deterministic jittered backoff;
  any other non-2xx (redirects included: they are never followed) is a refusal.

Failures raise hcli's PacksError classes, classified as the importer classifies
them, with its wording and a hint chosen where the cause is known.
"""

from __future__ import annotations

import ssl
import time
from collections import Counter, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from hcli.lib.packs.errors import (
    PacksError,
    PushConfigError,
    ServerRefusedError,
    TLSError,
    TransientError,
    find_cause,
    tls_failure,
)
from hcli.lib.packs.lumina.arrow import MAX_NAME_CHARS, FuncRef, LuminaArrow, LuminaArrowError, LuminaIndex
from hcli.lib.packs.lumina.pack import verification
from hcli.lib.packs.server import LuminaServer, base_url_hint, key_hint, tls_hint

HOSTNAME: Final = "hcli"
MIN_FUNCTION_SIZE: Final = 32
DEFAULT_CHUNK_BYTES: Final = 8 << 20
DEFAULT_RETRY_BASE_BACKOFF: Final = 2.0
MAX_BATCH_RETRIES: Final = 5
_PATTERN_HEX_LEN: Final = 32
# Field names, quotes, colons, commas and braces of one func, over-estimated.
_FUNC_JSON_OVERHEAD: Final = 100
_SNIPPET_CHARS: Final = 2000
_MASK64: Final = (1 << 64) - 1

METADATA_PATH: Final = "api/metadata"

TRANSIENT_HINT: Final = "safe to re-run (pushes are idempotent); check that the server is up and reachable"
BODY_CAP_HINT: Final = (
    "a function is over the server's request body cap: raise the server's limit (re-pushing will not help)"
)
TLS_HINT: Final = (
    "the TLS handshake failed; check that the --server scheme and port match the server (https:// vs http://)"
)
NOT_LUMINA_HINT: Final = (
    "the server answered 200 without a Lumina metadata response; check that --server is the Lumina REST "
    "base URL (/api/metadata is appended)"
)


# -- filtering --------------------------------------------------


def is_push_eligible(func: FuncRef) -> bool:
    """A non-empty name (whitespace counts) and at least IDA's default minimum function size."""
    return bool(func.name) and func.size >= MIN_FUNCTION_SIZE


def drop_collision_groups(funcs: Sequence[FuncRef]) -> list[FuncRef]:
    """One row per MD5 whose rows all share a name (on the pushed prefix); none for an MD5 whose names disagree."""
    names: dict[bytes, str] = {}
    conflicting: set[bytes] = set()
    for func in funcs:
        name = func.name[:MAX_NAME_CHARS]
        if names.setdefault(func.md5, name) != name:
            conflicting.add(func.md5)
    admitted: set[bytes] = set()
    survivors = []
    for func in funcs:
        if func.md5 in conflicting or func.md5 in admitted:
            continue
        admitted.add(func.md5)
        survivors.append(func)
    return survivors


def estimated_size(func: FuncRef) -> int:
    """The func's JSON size, over-estimated: the hex pattern, the name, ea and blob, plus fixed overhead."""
    return (
        _PATTERN_HEX_LEN + len(func.name.encode("utf-8")) + len(str(func.ea)) + func.meta_b64_len + _FUNC_JSON_OVERHEAD
    )


def chunk_by_budget(funcs: Sequence[FuncRef], budget: int) -> list[list[FuncRef]]:
    """Consecutive batches of at most ``budget`` estimated bytes; a func over the budget goes alone."""
    chunks: list[list[FuncRef]] = []
    current: list[FuncRef] = []
    current_bytes = 0
    for func in funcs:
        size = estimated_size(func)
        if current and current_bytes + size > budget:
            chunks.append(current)
            current, current_bytes = [], 0
        current_bytes += size
        current.append(func)
    if current:
        chunks.append(current)
    return chunks


# -- the request identity --------------------------------------------------------


def identity(sha256: str, index: LuminaIndex) -> dict[str, Any]:
    """The worker's identity for a member, without provenance (customer keys may not send it).

    Optional fields are omitted when their key-value is absent: the server refuses empty ones.
    """
    name = index.input_name or sha256
    body: dict[str, Any] = {"input": name, "idb": f"{name}.i64", "hostname": HOSTNAME}
    for wire, value in (
        ("inputHash", index.input_md5),
        ("inputSha256", index.input_sha256),
        ("binaryFormat", index.binary_format),
        ("arch", index.arch),
        ("inputSize", index.input_size),
    ):
        if value is not None:
            body[wire] = value
    return body


# -- pacing ----------------------------------------------------------------------


def seed_of(sha256: str) -> int:
    """The jitter seed of a member: the first 8 bytes of its sha256, little-endian."""
    return int.from_bytes(bytes.fromhex(sha256)[:8], "little")


def jitter_unit(seed: int, round_: int) -> float:
    """A deterministic value in [0, 1) for (seed, round): SplitMix64's finalizer."""
    z = (seed + round_ * 0x9E3779B97F4A7C15) & _MASK64
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    z ^= z >> 31
    return (z >> 11) / float(1 << 53)


def retry_backoff(base: float, seed: int, attempt: int) -> float:
    """``base * 2^(attempt-1)``, plus up to as much again of jitter."""
    exponential = base * (1 << (attempt - 1))
    return exponential + exponential * jitter_unit(seed, attempt)


# -- posting ------------------------------------------------------------------------------


@dataclass
class BatchOutcome:
    """What the server answered for a member's batches."""

    batches_sent: int = 0
    results: int = 0
    codes: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class _Refusal:
    status: int
    snippet: str


def _status_text(status: int) -> str:
    """``401 Unauthorized``, as reqwest prints a status."""
    phrase = httpx.codes.get_reason_phrase(status)
    return f"{status} {phrase}" if phrase else str(status)


def _snippet(response: httpx.Response) -> str:
    text = response.text[:_SNIPPET_CHARS]
    location = response.headers.get("location") if response.is_redirect else None
    return f"Location: {location}; {text}" if location else text


def _fatal(url: str, error: Exception) -> PacksError:
    """A request that got no HTTP answer: an untrusted certificate, another TLS failure, or the network."""
    message = f"lumina push POST {url} failed: {error}"
    if tls_failure(error) is not None:
        return TLSError(message, tls_hint())
    if find_cause(error, ssl.SSLError) is not None:
        return TransientError(message, TLS_HINT)
    return TransientError(message, TRANSIENT_HINT)


def _results(url: str, response: httpx.Response) -> list[str | None]:
    """The per-function codes of a 2xx answer; anything but a Lumina response is a configuration error."""
    try:
        body = response.json()
        results = body.get("results", []) if isinstance(body, dict) else None
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            raise ValueError("not a Lumina metadata response")
        codes = [r.get("code") for r in results]
        if not all(code is None or isinstance(code, str) for code in codes):
            raise ValueError("not a Lumina metadata response")
    except ValueError as e:
        raise PushConfigError(f"lumina push POST {url}: decode response body: {e}", NOT_LUMINA_HINT) from e
    return codes


def _refused(status: int, snippet: str, server: LuminaServer) -> ServerRefusedError:
    """A refusal other than 413, with the hint its status calls for."""
    if status in (401, 403):
        hint = key_hint(server.url)
    elif 300 <= status < 400 or status == 404:
        hint = base_url_hint(server.url)
    else:
        hint = f"the server refused the batch (HTTP {status}); check its logs: re-pushing unchanged will not help"
    return ServerRefusedError(f"batch rejected with HTTP {_status_text(status)}; body: {snippet}", hint)


def _post(
    client: httpx.Client,
    server: LuminaServer,
    api_key: str,
    body: dict[str, Any],
    retry_base: float,
    seed: int,
    sleep: Callable[[float], None],
) -> list[str | None] | _Refusal:
    """The codes of an accepted batch, or the 413 refusal; raises a PacksError for the rest."""
    url = server.url_for(METADATA_PATH)
    attempt = 0
    while True:
        try:
            response = client.post(url, json=body, headers={"Authorization": f"Bearer {api_key}"})
        except httpx.TransportError as e:
            raise _fatal(url, e) from e
        status = response.status_code
        if response.is_success:
            return _results(url, response)
        snippet = _snippet(response)
        if status == 413:
            return _Refusal(status, snippet)
        if status != 429 and status < 500:
            raise _refused(status, snippet, server)
        if attempt >= MAX_BATCH_RETRIES:
            if status == 429:
                raise TransientError(f"batch exhausted 429 retries; body: {snippet}", TRANSIENT_HINT)
            raise TransientError(
                f"batch exhausted retries after HTTP {_status_text(status)}; body: {snippet}", TRANSIENT_HINT
            )
        attempt += 1
        sleep(retry_backoff(retry_base, seed, attempt))


def push_funcs(
    client: httpx.Client,
    server: LuminaServer,
    api_key: str,
    body_identity: dict[str, Any],
    arrow: LuminaArrow,
    funcs: Sequence[FuncRef],
    *,
    budget: int = DEFAULT_CHUNK_BYTES,
    seed: int = 0,
    retry_base: float = DEFAULT_RETRY_BASE_BACKOFF,
    sleep: Callable[[float], None] = time.sleep,
    on_warning: Callable[[str], None] | None = None,
) -> BatchOutcome:
    """Post ``funcs`` in budgeted batches, reading each blob just before its batch; returns the server's codes.

    No funcs still posts one empty batch, as the importer does.
    """
    budget = max(budget, 1)
    pending: deque[list[FuncRef]] = deque(chunk_by_budget(funcs, budget)) or deque([[]])
    outcome = BatchOutcome()
    while pending:
        refs = pending.popleft()
        try:
            batch = [
                {
                    "pattern": f.md5.hex(),
                    "name": f.name,
                    "size": f.size,
                    "metadata": arrow.metadata_b64(f),
                    "ea": str(f.ea),
                }
                for f in refs
            ]
        except LuminaArrowError as e:
            raise verification(f"materialize push batch: {e}") from e
        answer = _post(client, server, api_key, {**body_identity, "funcs": batch}, retry_base, seed, sleep)
        if isinstance(answer, _Refusal):
            if len(refs) <= 1:
                raise ServerRefusedError(
                    f"single-function batch rejected with HTTP 413; body: {answer.snippet}", BODY_CAP_HINT
                )
            budget = max(budget // 2, 1)
            if on_warning is not None:
                on_warning(
                    f"{body_identity['input']}: a batch of {len(refs)} functions got HTTP 413; "
                    f"halving the batch budget to {budget} bytes"
                )
            pending.extendleft(reversed(chunk_by_budget(refs, budget)))
            continue
        outcome.batches_sent += 1
        outcome.results += len(answer)
        outcome.codes.update(code if code is not None else "<none>" for code in answer)
    return outcome
