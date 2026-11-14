"""Pushing one Lumina pack in-process, as the pack builder's importer does.

Every member is verified before the first request (the archive's shape and
a full read of every Lumina Arrow member), and the bytes read must be the
ones the Release lists, so a verification failure pushes nothing. The push
then re-reads the pack, pushes a member only when its bytes equal the
verified ones, and checks the whole file's checksum at the end: a pack
changed on disk mid-run fails, and only verified bytes are ever pushed.

Failures are PacksError (see ``push``); the messages keep the importer's
wording, with control characters escaped.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import httpx
from pydantic import BaseModel, ConfigDict

from hcli.lib.packs.errors import LocalIOError, PacksError
from hcli.lib.packs.lumina.arrow import KV_PREFIX, LuminaArrow, LuminaArrowError, LuminaIndex
from hcli.lib.packs.lumina.pack import Digest, for_each_member, rust_debug, spill_member, verification
from hcli.lib.packs.lumina.push import (
    DEFAULT_CHUNK_BYTES,
    DEFAULT_RETRY_BASE_BACKOFF,
    METADATA_PATH,
    drop_collision_groups,
    identity,
    is_push_eligible,
    push_funcs,
    seed_of,
)
from hcli.lib.packs.server import ApiKey, LuminaServer, lumina_client

logger = logging.getLogger(__name__)
# Bounded connect; a generous per-read budget, as the server answers a large batch only after storing it.
PUSH_TIMEOUT = httpx.Timeout(240, connect=10, write=240, pool=10)


# -- the report ----------------------------------------------------------------


class _ReportModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ImportTotals(_ReportModel):
    """``pushed`` = ``added`` + ``ok`` + ``other``."""

    packs: int = 0
    members: int = 0
    rows: int = 0
    pushed: int = 0
    added: int = 0
    ok: int = 0
    other: int = 0

    def __add__(self, other: ImportTotals) -> ImportTotals:
        return ImportTotals(**{name: getattr(self, name) + getattr(other, name) for name in ImportTotals.model_fields})


class ImportMember(_ReportModel):
    """One member's push: its rows, those left after filtering (pushed) and the server's codes."""

    pack: str
    sha256: str
    input: str = ""
    rows: int = 0
    pushed: int = 0
    batches: int = 0
    added: int = 0
    ok: int = 0
    other: int = 0

    def line(self) -> str:
        """``<sha256> <input> rows=<n> ADDED=<a> OK=<o> other=<x>``, the input's control characters escaped."""
        return (
            f"{self.sha256} {escape_controls(self.input)} rows={self.rows} "
            f"ADDED={self.added} OK={self.ok} other={self.other}"
        )


class ImportReport(_ReportModel):
    total: ImportTotals = ImportTotals()
    members: list[ImportMember] = []
    warnings: list[str] = []


def escape_controls(text: str) -> str:
    """Escape control characters as Rust's ``char::escape_default`` does, so untrusted text cannot forge lines."""
    out = []
    for char in text:
        code = ord(char)
        if code < 0x20 or 0x7F <= code < 0xA0:
            out.append({"\t": "\\t", "\r": "\\r", "\n": "\\n"}.get(char) or f"\\u{{{code:x}}}")
        else:
            out.append(char)
    return "".join(out)


# -- the import ----------------------------------------------------------------------------


def _open_member(sha256: str, data: memoryview, rows: bool = True) -> tuple[LuminaArrow, LuminaIndex | None]:
    """The member, validated; its rows only when ``rows`` (the verify pass checks them without building them)."""
    try:
        arrow = LuminaArrow(data)
    except LuminaArrowError as e:
        raise verification(f"pack member {sha256} is not a valid Lumina Arrow file: {e}") from e
    try:
        if not rows:
            arrow.check()
            return arrow, None
        return arrow, arrow.index()
    except LuminaArrowError as e:
        raise verification(f"pack member {sha256}: {e}") from e


def _mismatch(path: Path, actual: Digest, expected: Digest) -> str:
    if actual.size != expected.size:
        return f"pack {path} does not match the Release: size {actual.size} bytes, Release says {expected.size}"
    return f"pack {path} does not match the Release: sha256 {actual.sha256}, Release says {expected.sha256}"


class _Import:
    """One pack's import: the verify pass, then the push pass."""

    def __init__(
        self, path: Path, handle: BinaryIO, expected: Digest, server: LuminaServer, report: ImportReport
    ) -> None:
        self.path = path
        self.handle = handle
        # The pack's bytes, as the Release lists them; both passes must read exactly these.
        self.expected = expected
        self.server = server
        self.report = report
        self.members: list[tuple[str, Digest]] = []

    def verify(self) -> None:
        def visit(sha256: str, size: int, read: Callable[[int], bytes]) -> None:
            with spill_member(sha256, size, read) as member:
                arrow, _ = _open_member(sha256, member.data, rows=False)
                recorded = arrow.metadata.get(f"{KV_PREFIX}input_sha256") or None
                if recorded is not None and recorded.lower() != sha256:
                    self.report.warnings.append(
                        f"pack member {sha256} records input_sha256 {escape_controls(rust_debug(recorded))}; "
                        "it is pushed as recorded"
                    )
                self.members.append((sha256, member.digest))

        count, digest = for_each_member(self.path, self.handle, visit)
        if digest != self.expected:
            raise verification(_mismatch(self.path, digest, self.expected))
        logger.debug("checked %s: %d members", self.path, count)

    def push(
        self, client: httpx.Client, key: str, *, budget: int, retry_base: float, sleep: Callable[[float], None]
    ) -> None:
        expected = iter(self.members)

        def visit(sha256: str, size: int, read: Callable[[int], bytes]) -> None:
            with spill_member(sha256, size, read) as member:
                if next(expected, None) != (sha256, member.digest):
                    raise verification(f"pack {self.path} changed since it was verified: member {sha256} differs")
                arrow, index = _open_member(sha256, member.data)
                assert index is not None
                result = self._push_member(client, key, sha256, arrow, index, budget, retry_base, sleep)
            self.report.members.append(result)
            self.report.total += ImportTotals(
                members=1, rows=result.rows, pushed=result.pushed, added=result.added, ok=result.ok, other=result.other
            )

        _, digest = for_each_member(self.path, self.handle, visit)
        verified = self.expected
        if digest != verified:
            raise verification(
                f"pack {self.path} changed since it was verified: sha256 {digest.sha256} ({digest.size} bytes), "
                f"verified as {verified.sha256} ({verified.size} bytes)"
            )

    def _push_member(
        self,
        client: httpx.Client,
        key: str,
        sha256: str,
        arrow: LuminaArrow,
        index: LuminaIndex,
        budget: int,
        retry_base: float,
        sleep: Callable[[float], None],
    ) -> ImportMember:
        rows = len(index.funcs)
        eligible = [f for f in index.funcs if is_push_eligible(f)]
        survivors = drop_collision_groups(eligible)
        if len(survivors) != rows:
            logger.debug(
                "%s: %d rows, %d push-eligible, %d left after dropping conflicting MD5 groups",
                sha256, rows, len(eligible), len(survivors),
            )  # fmt: skip
        body = identity(sha256, index)
        try:
            outcome = push_funcs(
                client, self.server, key, body, arrow, survivors,
                budget=budget, seed=seed_of(sha256), retry_base=retry_base, sleep=sleep,
                on_warning=self.report.warnings.append,
            )  # fmt: skip
        except PacksError as e:
            e.add_context(f"push {sha256} {body['input']}: ")
            e.message = escape_controls(e.message)
            raise
        if outcome.results != len(survivors):
            self.report.warnings.append(
                f"{sha256}: server returned {outcome.results} results for {len(survivors)} functions"
            )
        added, ok = outcome.codes["ADDED"], outcome.codes["OK"]
        return ImportMember(
            pack=str(self.path),
            sha256=sha256,
            input=body["input"],
            rows=rows,
            pushed=len(survivors),
            batches=outcome.batches_sent,
            added=added,
            ok=ok,
            other=max(outcome.results - added - ok, 0),
        )


def _key_file_warning(api_key: ApiKey) -> str | None:
    """A warning when the key file can be read by its group or by others (POSIX only)."""
    if api_key.path is None or os.name != "posix":
        return None
    try:
        mode = api_key.path.stat().st_mode & 0o777
    except OSError as e:
        raise LocalIOError("read", api_key.path, e) from e
    if mode & 0o077:
        return f"API key file {api_key.path} has mode {mode:o}; other users can read it (chmod 600 it)"
    return None


def import_pack(
    path: Path,
    *,
    expected: Digest,
    server: LuminaServer,
    api_key: ApiKey,
    ca_cert: Path | None = None,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    retry_base: float = DEFAULT_RETRY_BASE_BACKOFF,
    sleep: Callable[[float], None] | None = None,
) -> ImportReport:
    """Verify the pack at ``path`` (its bytes must be ``expected``, the Release's), then push every member.

    Raises a PacksError; members pushed before a failure stay pushed.
    """
    report = ImportReport()
    if (warning := _key_file_warning(api_key)) is not None:
        report.warnings.append(warning)
    key = api_key.read()
    if server.scheme == "http":
        report.warnings.append(
            f"{server.url_for(METADATA_PATH)} is plain http; the API key and functions travel unencrypted"
        )
    try:
        handle = path.open("rb")
    except OSError as e:
        raise LocalIOError("open the pack", path, e) from e
    with handle, lumina_client(None, ca_cert, timeout=PUSH_TIMEOUT) as client:
        run = _Import(path, handle, expected, server, report)
        run.verify()
        run.push(client, key, budget=chunk_bytes, retry_base=retry_base, sleep=sleep or time.sleep)
    report.total.packs = 1
    return report
