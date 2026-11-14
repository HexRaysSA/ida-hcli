"""Filtering, batching and posting."""

from __future__ import annotations

import base64
import json
import ssl
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import lumina_arrow, lumina_rows

from hcli.lib.packs.errors import (
    IntegrityError,
    PushConfigError,
    ServerRefusedError,
    TLSError,
    TransientError,
)
from hcli.lib.packs.lumina.arrow import MAX_NAME_CHARS, FuncRef, LuminaArrow, LuminaIndex
from hcli.lib.packs.lumina.push import (
    BODY_CAP_HINT,
    MAX_BATCH_RETRIES,
    NOT_LUMINA_HINT,
    TLS_HINT,
    TRANSIENT_HINT,
    chunk_by_budget,
    drop_collision_groups,
    estimated_size,
    identity,
    is_push_eligible,
    jitter_unit,
    push_funcs,
    retry_backoff,
    seed_of,
)
from hcli.lib.packs.server import parse_server, tls_hint

SERVER = parse_server("http://lumina.test")
URL = "http://lumina.test/api/metadata"
KEY = "hrlu_test_key"


def func(md5: int = 1, name: str = "f", size: int = 64, ea: int = 0x4000, meta: int = 8, row: int = 0) -> FuncRef:
    """A row whose metadata blob is ``meta`` bytes long (at ``row``, so equal rows stay distinguishable)."""
    return FuncRef(md5=bytes([md5]) * 16, name=name, size=size, ea=ea, meta_start=row, meta_end=row + meta)


# -- eligibility and collision groups -------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "size", "eligible"),
    [("", 64, False), ("named", 31, False), ("named", 32, True), (" ", 32, True), ("λ", 2**32 - 1, True)],
)
def test_eligibility_matches_the_ida_client_gate(name: str, size: int, eligible: bool) -> None:
    assert is_push_eligible(func(name=name, size=size)) is eligible


def test_a_unanimous_md5_group_collapses_to_its_first_row() -> None:
    rows = [func(1, "same", row=0), func(1, "same", row=1), func(1, "same", row=2)]
    assert drop_collision_groups(rows) == [rows[0]]


def test_a_group_whose_names_disagree_is_dropped_whole() -> None:
    assert drop_collision_groups([func(1, "a"), func(1, "b"), func(1, "a")]) == []


def test_distinct_md5s_are_unchanged() -> None:
    rows = [func(i, f"n{i}") for i in range(5)]
    assert drop_collision_groups(rows) == rows


def test_rows_are_classified_in_observed_order() -> None:
    rows = [
        func(1, "twin", row=0),
        func(2, "left", row=1),
        func(1, "twin", row=2),
        func(2, "right", row=3),
        func(3, "solo", row=4),
    ]
    assert drop_collision_groups(rows) == [rows[0], rows[4]]


def test_names_compare_on_the_pushed_prefix() -> None:
    shared = "n" * MAX_NAME_CHARS
    rows = [func(1, shared + "a"), func(1, shared + "b")]
    assert drop_collision_groups(rows) == [rows[0]]


# -- batching ---------------------------------------------------------------------------


def test_no_funcs_make_no_batches() -> None:
    assert chunk_by_budget([], 1024) == []


def test_a_func_over_the_budget_goes_alone_and_is_never_dropped() -> None:
    big = func(meta=10_000)
    assert chunk_by_budget([big], 100) == [[big]]
    assert chunk_by_budget([big, big, big], 100) == [[big], [big], [big]]


def test_batches_flush_at_the_budget_boundary() -> None:
    f = func()
    size = estimated_size(f)
    assert chunk_by_budget([f] * 5, 2 * size) == [[f, f], [f, f], [f]]
    assert chunk_by_budget([f] * 5, 2 * size - 1) == [[f]] * 5
    assert chunk_by_budget([f] * 3, 100 * size) == [[f, f, f]]


@pytest.mark.parametrize("raw_len", [0, 1, 2, 3, 4, 8, 500, 1001])
def test_the_estimate_matches_the_materialized_fields(raw_len: int) -> None:
    encoded = base64.b64encode(b"a" * raw_len).decode()
    f = func(name="some_function", ea=0x4000, meta=raw_len)
    pushed = {"pattern": f.md5.hex(), "name": f.name, "metadata": encoded, "ea": str(f.ea)}
    assert estimated_size(f) == sum(len(v) for v in pushed.values()) + 100


def test_the_estimate_counts_name_bytes_not_characters() -> None:
    assert estimated_size(func(name="λ")) == estimated_size(func(name="ab"))


# -- identity ---------------------------------------------------------------------------


def _index(**kv: str | None) -> LuminaIndex:
    values: dict[str, Any] = dict.fromkeys(
        ("input_md5", "input_sha256", "input_name", "binary_format", "arch", "input_size")
    )
    values.update(kv)
    return LuminaIndex(funcs=[], **values)


def test_identity_omits_absent_fields_and_never_sends_provenance() -> None:
    body = identity("ab" * 32, _index(input_name="lib.a", arch="x86_64"))
    assert body == {"input": "lib.a", "idb": "lib.a.i64", "hostname": "hcli", "arch": "x86_64"}


def test_identity_falls_back_to_the_member_sha256() -> None:
    body = identity("ab" * 32, _index(input_md5="m", input_sha256="s", binary_format="pe", input_size="9"))
    assert body["input"] == "ab" * 32 and body["idb"] == "ab" * 32 + ".i64"
    assert (body["inputHash"], body["inputSha256"], body["binaryFormat"], body["inputSize"]) == ("m", "s", "pe", "9")


# -- pacing -----------------------------------------------------------------------------


def test_jitter_is_deterministic_bounded_and_spread() -> None:
    for seed in (0, 1, 42, 2**64 - 1, 0x7A, 123_456_789):
        for round_ in (0, 1, 2, 5, 17):
            value = jitter_unit(seed, round_)
            assert value == jitter_unit(seed, round_)
            assert 0.0 <= value < 1.0
    assert jitter_unit(0xDEADBEEF, 0) != jitter_unit(0xDEADBEEF, 1) != jitter_unit(0xDEADBEEF, 2)
    assert len({int(jitter_unit(seed, 0) * 10) for seed in range(20)}) >= 5


def test_retry_backoff_doubles_and_jitters_within_bounds() -> None:
    for attempt in range(1, 6):
        exponential = 2.0 * (1 << (attempt - 1))
        value = retry_backoff(2.0, 99, attempt)
        assert exponential <= value < 2 * exponential
        assert value == retry_backoff(2.0, 99, attempt)


def test_the_seed_is_the_first_eight_sha256_bytes_little_endian() -> None:
    assert seed_of("0102030405060708" + "00" * 24) == 0x0807060504030201


# -- posting ----------------------------------------------------------------------------


ARROW = LuminaArrow(memoryview(lumina_arrow([lumina_rows(6)])))
FUNCS = ARROW.index().funcs
IDENTITY = {"input": "lib", "idb": "lib.i64", "hostname": "hcli"}


def server(answer: Callable[[httpx.Request, list[dict[str, Any]], int], httpx.Response]) -> tuple[httpx.Client, list]:
    """A mock Lumina server; ``answer(request, funcs, call_number)``. Records (funcs, status) per call."""
    calls: list[tuple[list[dict[str, Any]], int, httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        funcs = json.loads(request.content)["funcs"]
        response = answer(request, funcs, len(calls))
        calls.append((funcs, response.status_code, request))
        return response

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False), calls


def added(_request: httpx.Request, funcs: list[dict[str, Any]], _call: int) -> httpx.Response:
    return httpx.Response(200, json={"results": [{"code": "ADDED"} for _ in funcs]})


def push(client: httpx.Client, funcs: list[FuncRef] = FUNCS, **kwargs: Any):
    sleeps: list[float] = []
    kwargs.setdefault("sleep", sleeps.append)
    outcome = push_funcs(client, SERVER, KEY, IDENTITY, ARROW, funcs, **kwargs)
    return outcome, sleeps


def test_a_batch_carries_the_identity_the_rows_and_the_bearer_key() -> None:
    client, calls = server(added)
    outcome, _ = push(client)
    ((funcs, _, request),) = calls
    body = json.loads(request.content)
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert {k: body[k] for k in IDENTITY} == IDENTITY
    assert [f["pattern"] for f in funcs] == [f.md5.hex() for f in FUNCS]
    assert [f["metadata"] for f in funcs] == [ARROW.metadata_b64(f) for f in FUNCS]
    assert all(isinstance(f["ea"], str) for f in funcs)
    assert (outcome.batches_sent, outcome.results, outcome.codes["ADDED"]) == (1, 6, 6)


def test_no_funcs_still_post_one_empty_batch() -> None:
    client, calls = server(added)
    outcome, _ = push(client, [])
    assert [len(funcs) for funcs, _, _ in calls] == [0] and outcome.batches_sent == 1


def test_codes_are_summed_across_batches_and_a_missing_code_is_counted() -> None:
    def answer(_r: httpx.Request, funcs: list, call: int) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"code": "OK"} if call else {} for _ in funcs], "extra": 1})

    budget = estimated_size(FUNCS[0]) + estimated_size(FUNCS[1])
    batches = chunk_by_budget(FUNCS, budget)
    assert len(batches) > 1
    client, calls = server(answer)
    outcome, _ = push(client, budget=budget)
    assert len(calls) == outcome.batches_sent == len(batches)
    first = len(batches[0])
    assert outcome.codes == {"<none>": first, "OK": 6 - first} and outcome.results == 6


def test_a_413_halves_the_budget_and_resplits_until_it_fits() -> None:
    def answer(_r: httpx.Request, funcs: list, _call: int) -> httpx.Response:
        return httpx.Response(413) if len(funcs) > 2 else added(_r, funcs, _call)

    warnings: list[str] = []
    client, calls = server(answer)
    outcome, _ = push(client, budget=sum(estimated_size(f) for f in FUNCS), on_warning=warnings.append)
    assert calls[0][1] == 413 and len(calls[0][0]) == 6
    assert all(status == 413 for funcs, status, _ in calls if len(funcs) > 2)
    pushed = [f["pattern"] for funcs, status, _ in calls if status == 200 for f in funcs]
    assert pushed == [f.md5.hex() for f in FUNCS]  # every row once, in order
    assert outcome.batches_sent == sum(s == 200 for _, s, _ in calls)
    assert len(warnings) == sum(s == 413 for _, s, _ in calls)
    assert "HTTP 413" in warnings[0]


def test_a_413_on_one_function_is_a_refusal() -> None:
    client, _ = server(lambda *_: httpx.Response(413, text="too big"))
    with pytest.raises(ServerRefusedError) as info:
        push(client, FUNCS[:1])
    assert info.value.message == "single-function batch rejected with HTTP 413; body: too big"
    assert info.value.hint == BODY_CAP_HINT


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (429, "batch exhausted 429 retries; body: busy"),
        (503, "batch exhausted retries after HTTP 503 Service Unavailable; body: busy"),
    ],
)
def test_429_and_5xx_retry_five_times_then_are_transient(status: int, message: str) -> None:
    client, calls = server(lambda *_: httpx.Response(status, text="busy"))
    sleeps: list[float] = []
    with pytest.raises(TransientError) as info:
        push(client, sleep=sleeps.append, retry_base=1.0, seed=7)
    assert len(calls) == 1 + MAX_BATCH_RETRIES
    assert sleeps == [retry_backoff(1.0, 7, attempt) for attempt in range(1, 6)]
    assert (info.value.message, info.value.hint) == (message, TRANSIENT_HINT)


def test_a_server_error_then_success_is_retried() -> None:
    client, calls = server(lambda r, f, call: httpx.Response(500) if call < 2 else added(r, f, call))
    outcome, sleeps = push(client)
    assert [s for _, s, _ in calls] == [500, 500, 200] and len(sleeps) == 2 and outcome.results == 6


def test_an_auth_refusal_is_rejected_with_its_status() -> None:
    client, calls = server(lambda *_: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(ServerRefusedError) as info:
        push(client)
    assert len(calls) == 1
    assert info.value.message == 'batch rejected with HTTP 401 Unauthorized; body: {"error":"unauthorized"}'
    assert info.value.hint is not None and "is a valid importer key" in info.value.hint


def test_a_redirect_is_never_followed_and_names_its_location() -> None:
    client, calls = server(
        lambda *_: httpx.Response(308, headers={"Location": "https://elsewhere.test/api"}, text="moved")
    )
    with pytest.raises(ServerRefusedError) as info:
        push(client)
    assert len(calls) == 1
    assert "HTTP 308 Permanent Redirect; body: Location: https://elsewhere.test/api; moved" in info.value.message
    assert info.value.hint is not None and "http->https redirects are not followed" in info.value.hint


def test_the_body_snippet_is_capped() -> None:
    client, _ = server(lambda *_: httpx.Response(400, text="x" * 5000))
    with pytest.raises(ServerRefusedError) as info:
        push(client)
    assert info.value.message.endswith("body: " + "x" * 2000)


def _failing(make: Callable[[httpx.Request], Exception]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        raise make(request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _chained(cause: BaseException) -> Callable[[httpx.Request], Exception]:
    """httpx's ConnectError over an ssl error, linked as httpcore links it (``__context__`` only)."""

    def make(request: httpx.Request) -> Exception:
        error = httpx.ConnectError("handshake failed", request=request)
        error.__context__ = cause
        return error

    return make


def test_a_transport_failure_is_transient() -> None:
    with pytest.raises(TransientError) as info:
        push(_failing(lambda r: httpx.ConnectError("connection refused", request=r)))
    assert info.value.hint == TRANSIENT_HINT
    assert info.value.message == f"lumina push POST {URL} failed: connection refused"


def test_an_untrusted_certificate_is_a_tls_error_with_the_ca_bundle_hint() -> None:
    # The same kind and hint as the key check push makes first.
    error = ssl.SSLCertVerificationError(1, "certificate verify failed")
    with pytest.raises(TLSError) as info:
        push(_failing(_chained(error)))
    assert info.value.hint == tls_hint()


def test_another_tls_failure_is_transient_with_the_scheme_hint() -> None:
    with pytest.raises(TransientError) as info:
        push(_failing(_chained(ssl.SSLError(1, "wrong version number"))))
    assert info.value.hint == TLS_HINT


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, text="<html>a web page</html>"),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"results": "nope"}),
        httpx.Response(200, json={"results": [{"code": 3}]}),
    ],
)
def test_a_200_that_is_not_a_lumina_answer_is_a_configuration_error(answer: httpx.Response) -> None:
    client, _ = server(lambda *_: answer)
    with pytest.raises(PushConfigError) as info:
        push(client)
    assert info.value.hint == NOT_LUMINA_HINT
    assert info.value.message.startswith(f"lumina push POST {URL}: decode response body: ")


def test_an_empty_object_is_an_answer_with_no_results() -> None:
    client, _ = server(lambda *_: httpx.Response(200, json={}))
    outcome, _ = push(client)
    assert (outcome.batches_sent, outcome.results) == (1, 0)


def test_a_row_that_cannot_be_read_back_is_a_verification_failure() -> None:
    client, calls = server(added)
    ghost = FuncRef(md5=b"\x01" * 16, name="ghost", size=64, ea=0, meta_start=1 << 40, meta_end=1 << 40)
    with pytest.raises(IntegrityError):
        push(client, [ghost])
    assert calls == []
