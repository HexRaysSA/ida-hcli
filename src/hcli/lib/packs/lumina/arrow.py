"""Strict reading of a Lumina Arrow IPC file (a pack member), without an Arrow library.

Accepts exactly what the pack builder's importer accepts: the Arrow IPC *file* format
with the schema ``ea: uint64, size: uint32, name: utf8,
md5: fixed_size_binary(16), metadata: binary``, no dictionaries, no
compression, no variadic buffers, and every footer block and buffer inside
the file. Anything else raises LuminaArrowError, never another exception.

Format: https://arrow.apache.org/docs/format/Columnar.html#ipc-file-format::

    "ARROW1" + 2 pad bytes
    messages: [0xFFFFFFFF] int32 length, flatbuffer Message, padding, body
    footer flatbuffer (schema, record batch blocks)
    int32 footer length, "ARROW1"

The file is read through a memoryview (an mmap for large members), so the
metadata blobs are never copied whole: ``index`` keeps one small FuncRef per
row, with where its blob is, and ``metadata_b64`` reads it when its batch is
about to be posted.
Values are little-endian, as Arrow writes them on every platform the pack builder uses.
"""

from __future__ import annotations

import base64
import contextlib
import operator
import struct
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Final, NamedTuple

MAX_NAME_CHARS: Final = 4096
# The prefix of the schema key-values the pack builder writes (``<prefix>input_sha256``, ...): a wire name, not prose.
KV_PREFIX: Final = "marghera."
MAX_MESSAGE_BYTES: Final = 1 << 20
_MAGIC: Final = b"ARROW1"
_MIN_FILE_BYTES: Final = 16
_TRAILER_BYTES: Final = 10
# Arrow Type union ids (Schema.fbs) and the MessageHeader id of a record batch (Message.fbs).
_INT, _BINARY, _UTF8, _FIXED_SIZE_BINARY = 2, 4, 5, 15
_RECORD_BATCH: Final = 3
# The Lumina schema, in order: name, Arrow type id, bit/byte width (0: none),
# and the buffers one batch carries for it (validity, values, plus offsets).
_FIELDS: Final = (
    ("ea", _INT, 64, 2),
    ("size", _INT, 32, 2),
    ("name", _UTF8, 0, 3),
    ("md5", _FIXED_SIZE_BINARY, 16, 2),
    ("metadata", _BINARY, 0, 3),
)
_BUFFERS: Final = sum(field[3] for field in _FIELDS)
# Buffer indexes in a batch, in schema order.
_EA, _SIZE = 1, 3
_NAME_VALID, _NAME_OFFSETS, _NAME_DATA = 4, 5, 6
_MD5_VALID, _MD5 = 7, 8
_META_VALID, _META_OFFSETS, _META_DATA = 9, 10, 11
# Footer Block struct: offset int64, metaDataLength int32 (+4 padding), bodyLength int64.
_BLOCK: Final = "qi4xq"


class LuminaArrowError(ValueError):
    """The bytes are not a valid Lumina Arrow file."""


@contextlib.contextmanager
def _damage(what: str) -> Generator[None, None, None]:
    """Report any parsing failure on damaged bytes as a LuminaArrowError."""
    try:
        yield
    except LuminaArrowError:
        raise
    except (struct.error, IndexError, UnicodeDecodeError, ValueError, TypeError, OverflowError) as e:
        raise LuminaArrowError(f"{what}: {e}") from e


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise LuminaArrowError(message)


class _Table:
    """A flatbuffer table, read field by field through its vtable."""

    __slots__ = ("buf", "pos", "vtable", "vtable_len")

    def __init__(self, buf: memoryview, pos: int) -> None:
        _ensure(0 <= pos <= len(buf) - 4, "flatbuffer table out of bounds")
        self.buf = buf
        self.pos = pos
        self.vtable = pos - struct.unpack_from("<i", buf, pos)[0]
        _ensure(0 <= self.vtable <= len(buf) - 4, "flatbuffer vtable out of bounds")
        self.vtable_len = struct.unpack_from("<H", buf, self.vtable)[0]

    @classmethod
    def root(cls, buf: memoryview) -> _Table:
        _ensure(len(buf) >= 4, "flatbuffer too short")
        return cls(buf, struct.unpack_from("<I", buf, 0)[0])

    def field(self, index: int) -> int:
        """The absolute position of field ``index``, or 0 when it is absent."""
        entry = 4 + 2 * index
        if entry + 2 > self.vtable_len:
            return 0
        offset = struct.unpack_from("<H", self.buf, self.vtable + entry)[0]
        return self.pos + offset if offset else 0

    def scalar(self, index: int, fmt: str, default: Any = 0) -> Any:
        at = self.field(index)
        return struct.unpack_from("<" + fmt, self.buf, at)[0] if at else default

    def _target(self, at: int) -> int:
        target = at + struct.unpack_from("<I", self.buf, at)[0]
        _ensure(target < len(self.buf), "flatbuffer offset out of bounds")
        return target

    def table(self, index: int) -> _Table | None:
        at = self.field(index)
        return _Table(self.buf, self._target(at)) if at else None

    def string(self, index: int) -> str | None:
        at = self.field(index)
        if not at:
            return None
        start = self._target(at)
        length = struct.unpack_from("<I", self.buf, start)[0]
        _ensure(start + 4 + length <= len(self.buf), "flatbuffer string out of bounds")
        return bytes(self.buf[start + 4 : start + 4 + length]).decode("utf-8")

    def vector(self, index: int) -> tuple[int, int] | None:
        """``(first element position, count)``, or None when the field is absent."""
        at = self.field(index)
        if not at:
            return None
        start = self._target(at)
        return start + 4, struct.unpack_from("<I", self.buf, start)[0]

    def tables(self, index: int) -> list[_Table]:
        start, count = self.vector(index) or (0, 0)
        _ensure(start + 4 * count <= len(self.buf), "flatbuffer vector out of bounds")
        return [_Table(self.buf, self._target(start + 4 * i)) for i in range(count)]

    def structs(self, index: int, fmt: str) -> list[tuple[Any, ...]]:
        start, count = self.vector(index) or (0, 0)
        size = struct.calcsize("<" + fmt)
        _ensure(start + count * size <= len(self.buf), "flatbuffer vector out of bounds")
        return [struct.unpack_from("<" + fmt, self.buf, start + i * size) for i in range(count)]


class FuncRef(NamedTuple):
    """One row to push; its metadata blob stays in the file (``meta_start:meta_end``) until its batch is posted."""

    md5: bytes
    name: str
    size: int
    ea: int
    meta_start: int
    meta_end: int

    @property
    def meta_b64_len(self) -> int:
        return 4 * ((self.meta_end - self.meta_start + 2) // 3)


@dataclass(frozen=True)
class LuminaIndex:
    """A member's ``KV_PREFIX`` schema key-values (empty counts as absent) and its rows."""

    input_md5: str | None
    input_sha256: str | None
    input_name: str | None
    binary_format: str | None
    arch: str | None
    input_size: str | None
    funcs: list[FuncRef]


@dataclass(frozen=True)
class _Batch:
    rows: int
    # (absolute offset, length) of each of its 13 buffers
    buffers: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Columns:
    """One batch's columns as Python values, checked: offsets in range and in order, names valid UTF-8."""

    eas: list[int]
    sizes: list[int]
    md5s: bytes
    names: bytes
    name_offsets: list[int]
    meta_offsets: list[int]
    # Absolute position of the metadata data buffer, so a row's blob is read straight from the file.
    meta_base: int
    # Validity bitmaps; None when the column has no nulls.
    md5_valid: bytes | None
    name_valid: bytes | None
    meta_valid: bytes | None


def _check_metadata(table: _Table, index: int) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for entry in table.tables(index):
        key, value = entry.string(0), entry.string(1)
        if key is None or value is None:
            raise LuminaArrowError("incomplete Arrow metadata entry")
        metadata[key] = value
    return metadata


class LuminaArrow:
    """A validated Lumina Arrow file over ``data``; raises LuminaArrowError on anything else."""

    def __init__(self, data: memoryview) -> None:
        self._buf = data
        with _damage("invalid Lumina Arrow file"):
            self.metadata, self._batches = self._validate()

    # -- the envelope ------------------------------------------------

    def _validate(self) -> tuple[dict[str, str], list[_Batch]]:
        buf, size = self._buf, len(self._buf)
        _ensure(size >= 6 and bytes(buf[:6]) == _MAGIC, "invalid Arrow file magic")
        _ensure(size >= _MIN_FILE_BYTES, "truncated Arrow file envelope")
        _ensure(bytes(buf[size - 6 :]) == _MAGIC, "invalid Arrow file magic")
        footer_len = struct.unpack_from("<i", buf, size - _TRAILER_BYTES)[0]
        _ensure(0 <= footer_len <= size - _MIN_FILE_BYTES, "Arrow footer exceeds file length")
        footer_start = size - _TRAILER_BYTES - footer_len
        footer = _Table.root(buf[footer_start : size - _TRAILER_BYTES])
        schema = footer.table(1)
        _ensure(schema is not None, "Arrow footer has no schema")
        assert schema is not None
        self._check_schema(schema)
        metadata = _check_metadata(schema, 2)
        _check_metadata(footer, 4)
        _ensure(not (footer.vector(2) or (0, 0))[1], "Lumina Arrow cannot contain dictionary blocks")
        _ensure(footer.vector(3) is not None, "Arrow footer has no record batches")
        batches = [
            self._check_block(block, footer_start, f"Arrow declared batch {index}")
            for index, block in enumerate(footer.structs(3, _BLOCK))
        ]
        return metadata, batches

    @staticmethod
    def _check_schema(schema: _Table) -> None:
        _ensure(schema.vector(1) is not None, "Arrow schema has no fields")
        fields = schema.tables(1)
        _ensure(len(fields) == len(_FIELDS), "Lumina Arrow schema must contain exactly five fields")
        for field, (name, type_id, width, _) in zip(fields, _FIELDS, strict=True):
            _ensure(
                field.string(0) == name and field.scalar(2, "B") == type_id,
                f"Lumina Arrow schema: expected '{name}' as {_TYPE_NAMES[type_id]}",
            )
            _ensure(field.table(4) is None, f"Lumina Arrow schema: '{name}' cannot use a dictionary")
            _ensure(not field.tables(5), f"Lumina Arrow schema: '{name}' cannot have children")
            arrow_type = field.table(3)
            if type_id == _INT:
                supported = (
                    arrow_type is not None
                    and arrow_type.scalar(0, "i") == width
                    and not arrow_type.scalar(1, "?", False)
                )
            elif type_id == _FIXED_SIZE_BINARY:
                supported = arrow_type is not None and arrow_type.scalar(0, "i") == width
            else:
                supported = True
            _ensure(supported, f"Lumina Arrow schema: unsupported scalar type for '{name}'")
            _check_metadata(field, 6)

    def _check_block(self, block: tuple[int, int, int], footer_start: int, where: str) -> _Batch:
        offset, metadata_len, body_len = block
        _ensure(offset >= 0, f"{where}: negative Arrow block offset")
        _ensure(metadata_len >= 0 and body_len >= 0, f"{where}: invalid Arrow block length")
        _ensure(
            offset >= 8 and offset + metadata_len + body_len <= footer_start,
            f"{where}: Arrow block extends outside file data",
        )
        _ensure(metadata_len >= 4, f"{where}: truncated Arrow block metadata prefix")
        if struct.unpack_from("<I", self._buf, offset)[0] == 0xFFFFFFFF:
            _ensure(metadata_len >= 8, f"{where}: truncated Arrow continuation prefix")
            message_len, prefix_len = struct.unpack_from("<i", self._buf, offset + 4)[0], 8
        else:  # pre-0.15 IPC: a length prefix without the continuation marker
            message_len, prefix_len = struct.unpack_from("<i", self._buf, offset)[0], 4
        _ensure(message_len >= 0, f"{where}: negative Arrow block message length")
        _ensure(
            message_len > 0 and message_len == metadata_len - prefix_len,
            f"{where}: Arrow block message length disagrees with footer",
        )
        _ensure(
            message_len <= MAX_MESSAGE_BYTES,
            f"{where}: Arrow block message of {message_len} bytes exceeds {MAX_MESSAGE_BYTES}",
        )
        message = _Table.root(self._buf[offset + prefix_len : offset + prefix_len + message_len])
        batch = message.table(2) if message.scalar(1, "B") == _RECORD_BATCH else None
        _ensure(batch is not None, f"{where}: Arrow block is not a record batch message")
        assert batch is not None
        _ensure(batch.table(3) is None, f"{where}: compressed Arrow record batches are not supported")
        _ensure(not (batch.vector(4) or (0, 0))[1], f"{where}: Lumina Arrow record batch cannot have variadic buffers")
        rows = batch.scalar(0, "q")
        _ensure(rows >= 0, f"{where}: negative Arrow record batch length")
        _ensure(batch.vector(1) is not None, f"{where}: Arrow record batch has no nodes")
        nodes = batch.structs(1, "qq")
        _ensure(len(nodes) == len(_FIELDS), f"{where}: Arrow record batch has {len(nodes)} nodes, expected 5")
        for length, null_count in nodes:
            _ensure(
                length == rows and 0 <= null_count <= rows,
                f"{where}: Arrow record batch node disagrees with its {rows} rows",
            )
        _ensure(batch.vector(2) is not None, f"{where}: Arrow record batch has no buffers")
        buffers = batch.structs(2, "qq")
        _ensure(
            len(buffers) == _BUFFERS, f"{where}: Arrow record batch has {len(buffers)} buffers, expected {_BUFFERS}"
        )
        body = offset + metadata_len
        for start, length in buffers:
            _ensure(start >= 0, f"{where}: negative Arrow buffer offset")
            _ensure(length >= 0, f"{where}: negative Arrow buffer length")
            _ensure(
                start + length <= body_len,
                f"{where}: Arrow buffer {start}..{start + length} extends outside its {body_len}-byte block body",
            )
        return _Batch(rows, tuple((body + start, length) for start, length in buffers))

    # -- the rows -------------------------------------------------------

    def _view(self, batch: _Batch, index: int) -> memoryview:
        start, length = batch.buffers[index]
        return self._buf[start : start + length]

    def _values(self, batch: _Batch, index: int, width: int, name: str) -> memoryview:
        view = self._view(batch, index)
        _ensure(len(view) >= width * batch.rows, f"Arrow column '{name}' is shorter than its {batch.rows} rows")
        return view[: width * batch.rows]

    def _validity(self, batch: _Batch, index: int, name: str) -> bytes | None:
        view = self._view(batch, index)
        if not len(view):
            return None
        _ensure(len(view) >= (batch.rows + 7) // 8, f"Arrow column '{name}' validity is shorter than its rows")
        return view.tobytes()

    def _offsets(self, batch: _Batch, index: int, data_len: int, name: str) -> list[int]:
        if not batch.rows:
            return [0]
        view = self._view(batch, index)
        _ensure(len(view) >= 4 * (batch.rows + 1), f"Arrow column '{name}' offsets are too short")
        offsets = view[: 4 * (batch.rows + 1)].cast("i").tolist()
        _ensure(
            0 <= offsets[0] and offsets[-1] <= data_len and all(map(operator.le, offsets, offsets[1:])),
            f"Arrow column '{name}' has offsets outside its data",
        )
        return offsets

    def _columns(self, batch: _Batch) -> _Columns:
        names = self._view(batch, _NAME_DATA).tobytes()
        name_offsets = self._offsets(batch, _NAME_OFFSETS, len(names), "name")
        # The values' bytes are valid UTF-8 and no value starts inside a character: every value is valid UTF-8.
        names[name_offsets[0] : name_offsets[-1]].decode("utf-8")
        _ensure(
            all(o == len(names) or not 0x80 <= names[o] < 0xC0 for o in name_offsets),
            "Arrow column 'name' splits a UTF-8 character",
        )
        meta_start, meta_len = batch.buffers[_META_DATA]
        return _Columns(
            eas=self._values(batch, _EA, 8, "ea").cast("Q").tolist(),
            sizes=self._values(batch, _SIZE, 4, "size").cast("I").tolist(),
            md5s=self._values(batch, _MD5, 16, "md5").tobytes(),
            names=names,
            name_offsets=name_offsets,
            meta_offsets=self._offsets(batch, _META_OFFSETS, meta_len, "metadata"),
            meta_base=meta_start,
            md5_valid=self._validity(batch, _MD5_VALID, "md5"),
            name_valid=self._validity(batch, _NAME_VALID, "name"),
            meta_valid=self._validity(batch, _META_VALID, "metadata"),
        )

    def check(self) -> None:
        """Check every batch's columns, as ``index`` would, without building the rows."""
        with _damage("invalid Lumina Arrow batch"):
            for batch in self._batches:
                self._columns(batch)

    def index(self) -> LuminaIndex:
        """Every row with a non-null md5 (a null name reads as empty, a null blob as no bytes)."""
        funcs: list[FuncRef] = []
        with _damage("invalid Lumina Arrow batch"):
            for batch in self._batches:
                c = self._columns(batch)
                for row in range(batch.rows):
                    if c.md5_valid is not None and not c.md5_valid[row >> 3] >> (row & 7) & 1:
                        continue
                    name = ""
                    if c.name_valid is None or c.name_valid[row >> 3] >> (row & 7) & 1:
                        name = c.names[c.name_offsets[row] : c.name_offsets[row + 1]].decode("utf-8")[:MAX_NAME_CHARS]
                    start = end = c.meta_base
                    if c.meta_valid is None or c.meta_valid[row >> 3] >> (row & 7) & 1:
                        start, end = c.meta_base + c.meta_offsets[row], c.meta_base + c.meta_offsets[row + 1]
                    funcs.append(FuncRef(c.md5s[16 * row : 16 * row + 16], name, c.sizes[row], c.eas[row], start, end))

        def kv(key: str) -> str | None:
            return self.metadata.get(f"{KV_PREFIX}{key}") or None

        return LuminaIndex(
            input_md5=kv("input_md5"),
            input_sha256=kv("input_sha256"),
            input_name=kv("input_name"),
            binary_format=kv("binary_format"),
            arch=kv("arch"),
            input_size=kv("input_size"),
            funcs=funcs,
        )

    def metadata_b64(self, func: FuncRef) -> str:
        """A row's metadata blob, base64 (empty for a null blob)."""
        _ensure(
            0 <= func.meta_start <= func.meta_end <= len(self._buf),
            "lumina.arrow: a row's metadata is outside the file during materialize",
        )
        return base64.b64encode(self._buf[func.meta_start : func.meta_end]).decode("ascii")


_TYPE_NAMES: Final = {_INT: "Int", _BINARY: "Binary", _UTF8: "Utf8", _FIXED_SIZE_BINARY: "FixedSizeBinary"}
