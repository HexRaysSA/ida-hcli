"""The Lumina Arrow reader against pyarrow (the reference) and against hostile files.

The importer's reader cases and hostile members, plus nulls and a fuzz pass: whatever the bytes,
the reader returns the same rows as pyarrow or raises LuminaArrowError.
"""

from __future__ import annotations

import base64
import random
import struct
from typing import Any

import pyarrow as pa
import pytest
from conftest import LUMINA_FIXTURES, LUMINA_KV, lumina_arrow, lumina_rows, lumina_schema
from pyarrow import ipc

from hcli.lib.packs.lumina.arrow import KV_PREFIX, MAX_NAME_CHARS, LuminaArrow, LuminaArrowError, _Table

GOLDEN = (LUMINA_FIXTURES / "archive-bundle-sample.arrow").read_bytes()
SCHEMA = lumina_schema()
KV = LUMINA_KV
rows = lumina_rows
write = lumina_arrow


def read(data: bytes) -> LuminaArrow:
    return LuminaArrow(memoryview(data))


def reference(data: bytes) -> list[tuple[bytes, str, int, int, str]]:
    """The rows as pyarrow reads them, with the reader's null rules applied."""
    table = ipc.open_file(pa.BufferReader(data)).read_all()
    out = []
    for row in table.to_pylist():
        if row["md5"] is None:
            continue
        name = (row["name"] or "")[:MAX_NAME_CHARS]
        blob = base64.b64encode(row["metadata"] or b"").decode()
        out.append((row["md5"], name, row["size"], row["ea"], blob))
    return out


def ours(data: bytes) -> list[tuple[bytes, str, int, int, str]]:
    arrow = read(data)
    return [(f.md5, f.name, f.size, f.ea, arrow.metadata_b64(f)) for f in arrow.index().funcs]


# -- reading real and generated files ------------------------------------------------


def test_the_real_producer_golden_matches_pyarrow() -> None:
    assert GOLDEN.startswith(b"ARROW1")
    index = read(GOLDEN).index()
    assert index.funcs, "the bundle carries functions"
    assert all(len(f.md5) == 16 for f in index.funcs)  # FixedSizeBinary(16), not Binary
    assert index.binary_format == "ar"
    assert index.input_sha256 == "8cbdd963fb07341b06bd6a58dcd6f54dafc64af5c3823fe4cdb2702f2ea97c2d"
    assert ours(GOLDEN) == reference(GOLDEN)


def test_rows_and_key_values_round_trip_across_batches() -> None:
    data = write([rows(5), rows(0), rows(7, start=5)])
    index = read(data).index()
    assert [f.md5 for f in index.funcs] == [i.to_bytes(16, "big") for i in range(12)]
    assert ours(data) == reference(data)
    assert (index.input_md5, index.input_name, index.binary_format, index.arch, index.input_size) == (
        KV[f"{KV_PREFIX}input_md5"],
        KV[f"{KV_PREFIX}input_name"],
        "coff",
        "x86_64",
        "12345",
    )


def test_empty_key_values_read_as_absent() -> None:
    index = read(write([rows(1)], metadata=dict.fromkeys(KV, ""))).index()
    assert (index.input_md5, index.input_sha256, index.input_name, index.arch) == (None, None, None, None)


def test_meta_b64_len_is_the_base64_length_of_each_blob() -> None:
    arrow = read(write([rows(9)]))
    for func in arrow.index().funcs:
        assert func.meta_b64_len == len(arrow.metadata_b64(func))


def test_long_names_are_truncated_to_the_cap_in_characters_not_dropped() -> None:
    batch = rows(4)
    batch["name"] = ["a" * (MAX_NAME_CHARS + 50), "λ" * (MAX_NAME_CHARS + 1), "b" * MAX_NAME_CHARS, "short"]
    funcs = read(write([batch])).index().funcs
    assert [len(f.name) for f in funcs] == [MAX_NAME_CHARS, MAX_NAME_CHARS, MAX_NAME_CHARS, 5]
    assert funcs[1].name == "λ" * MAX_NAME_CHARS  # characters, not bytes
    assert funcs[3].name == "short"


def test_nulls_follow_the_rust_reader() -> None:
    batch = rows(6)
    batch["md5"][1] = None  # no md5: the row is skipped
    batch["name"][2] = None  # no name: an empty name (not push-eligible)
    batch["metadata"][3] = None  # no blob: empty metadata
    batch["md5"][5] = None
    data = write([batch, rows(2, start=6)])
    arrow = read(data)
    funcs = arrow.index().funcs
    assert [f.md5[-1] for f in funcs] == [0, 2, 3, 4, 6, 7]  # the rows without an md5 are skipped
    assert funcs[1].name == ""
    assert funcs[2].meta_b64_len == 0 and arrow.metadata_b64(funcs[2]) == ""
    assert ours(data) == reference(data)


def test_a_file_without_batches_has_no_rows() -> None:
    assert read(write([])).index().funcs == []


def test_a_blob_outside_the_file_is_an_error() -> None:
    arrow = read(write([rows(2)]))
    ghost = arrow.index().funcs[0]._replace(meta_end=1 << 40)
    with pytest.raises(LuminaArrowError, match="outside the file"):
        arrow.metadata_b64(ghost)


# -- the schema ----------------------------------------------------------------------


def _schema_with(index: int, field: pa.Field) -> pa.Schema:
    fields = list(SCHEMA)
    fields[index] = field
    return pa.schema(fields)


def _batch_for(schema: pa.Schema) -> dict[str, Any]:
    """Two rows: real values where the column is the Lumina one, nulls of the right type otherwise."""
    data = rows(2)
    return {
        field.name: data[field.name]
        if SCHEMA.get_field_index(field.name) >= 0 and SCHEMA.field(field.name).type == field.type
        else pa.nulls(2, field.type)
        for field in schema
    }


@pytest.mark.parametrize(
    ("schema", "words"),
    [
        (_schema_with(0, pa.field("ea", pa.int64())), "unsupported scalar type for 'ea'"),
        (_schema_with(0, pa.field("ea", pa.uint32())), "unsupported scalar type for 'ea'"),
        (_schema_with(1, pa.field("size", pa.uint64())), "unsupported scalar type for 'size'"),
        (_schema_with(3, pa.field("md5", pa.binary())), "expected 'md5' as FixedSizeBinary"),
        (_schema_with(3, pa.field("md5", pa.binary(15))), "unsupported scalar type for 'md5'"),
        (_schema_with(2, pa.field("name", pa.large_string())), "expected 'name' as Utf8"),
        (_schema_with(4, pa.field("metadata", pa.large_binary())), "expected 'metadata' as Binary"),
        (_schema_with(2, pa.field("label", pa.string())), "expected 'name'"),
        (pa.schema(list(SCHEMA)[:4]), "exactly five fields"),
        (pa.schema([*SCHEMA, pa.field("extra", pa.uint8())]), "exactly five fields"),
        (pa.schema([SCHEMA.field(1), SCHEMA.field(0), *list(SCHEMA)[2:]]), "expected 'ea'"),
    ],
)
def test_anything_but_the_lumina_schema_is_refused(schema: pa.Schema, words: str) -> None:
    with pytest.raises(LuminaArrowError, match=words):
        read(write([_batch_for(schema)], schema=schema))


def test_a_dictionary_column_is_refused() -> None:
    schema = _schema_with(2, pa.field("name", pa.dictionary(pa.int32(), pa.string())))
    batch = rows(2)
    batch["name"] = pa.array(batch["name"]).dictionary_encode()
    with pytest.raises(LuminaArrowError, match="dictionary"):
        read(write([batch], schema=schema))


@pytest.mark.parametrize("codec", ["zstd", "lz4"])
def test_compressed_batches_are_refused(codec: str) -> None:
    data = write([rows(3)], options=ipc.IpcWriteOptions(compression=codec))
    with pytest.raises(LuminaArrowError, match="compressed"):
        read(data)


def test_the_stream_format_is_not_the_file_format() -> None:
    sink = pa.BufferOutputStream()
    with ipc.new_stream(sink, SCHEMA) as writer:
        writer.write_batch(pa.record_batch(rows(2), schema=SCHEMA))
    with pytest.raises(LuminaArrowError, match="magic"):
        read(sink.getvalue().to_pybytes())


@pytest.mark.parametrize("data", [b"", b"ARROW1", b"ARROW1\0\0" + b"\0" * 8, b"PAR1" * 10])
def test_short_or_foreign_files_are_refused(data: bytes) -> None:
    with pytest.raises(LuminaArrowError):
        read(data)


# -- hostile footers and record batch messages ----------------------------


def _footer(data: bytes) -> tuple[_Table, int]:
    size = len(data)
    footer_len = struct.unpack_from("<i", data, size - 10)[0]
    start = size - 10 - footer_len
    return _Table.root(memoryview(data)[start : size - 10]), start


def _block_position(data: bytes, index: int = 0) -> int:
    """The absolute position of record batch block ``index`` in the footer."""
    footer, start = _footer(data)
    vector = footer.vector(3)
    assert vector is not None
    return start + vector[0] + 24 * index


def _patch(data: bytes, position: int, fmt: str, value: int) -> bytes:
    out = bytearray(data)
    struct.pack_into("<" + fmt, out, position, value)
    return bytes(out)


def _buffers_position(data: bytes, block: int) -> int:
    """The absolute position of the first Buffer struct of record batch ``block``."""
    offset, metadata_len, _ = struct.unpack_from("<qi4xq", data, _block_position(data, block))
    prefix = 8 if struct.unpack_from("<I", data, offset)[0] == 0xFFFFFFFF else 4
    message_start = offset + prefix
    message = _Table.root(memoryview(data)[message_start : offset + metadata_len])
    batch = message.table(2)
    assert batch is not None
    vector = batch.vector(2)
    assert vector is not None
    return message_start + vector[0]


@pytest.mark.parametrize(
    ("field", "fmt", "value", "words"),
    [
        (0, "q", -8, "negative Arrow block offset"),
        (0, "q", 4, "outside file data"),
        (0, "q", 1 << 40, "outside file data"),
        (8, "i", -1, "invalid Arrow block length"),
        (8, "i", 2, "truncated Arrow block metadata prefix"),
        (16, "q", -1, "invalid Arrow block length"),
        (16, "q", 1 << 40, "outside file data"),
    ],
)
def test_hostile_footer_blocks_are_refused(field: int, fmt: str, value: int, words: str) -> None:
    data = write([rows(3)])
    with pytest.raises(LuminaArrowError, match=words):
        read(_patch(data, _block_position(data) + field, fmt, value))


def test_a_message_length_that_disagrees_with_the_footer_is_refused() -> None:
    data = write([rows(3)])
    offset = struct.unpack_from("<q", data, _block_position(data))[0]
    length = struct.unpack_from("<i", data, offset + 4)[0]
    with pytest.raises(LuminaArrowError, match="disagrees with footer"):
        read(_patch(data, offset + 4, "i", length - 8))


def test_a_footer_length_beyond_the_file_is_refused() -> None:
    data = write([rows(3)])
    with pytest.raises(LuminaArrowError, match="footer exceeds"):
        read(_patch(data, len(data) - 10, "i", len(data)))


@pytest.mark.parametrize(
    ("which", "value", "words"),
    [("offset", -1, "negative Arrow buffer offset"), ("length", 1 << 40, "extends outside its")],
)
def test_a_later_block_with_a_buffer_outside_its_body_is_refused(which: str, value: int, words: str) -> None:
    data = write([rows(3), rows(3, start=3)])
    position = _buffers_position(data, 1) + 16 * 6 + (0 if which == "offset" else 8)
    with pytest.raises(LuminaArrowError, match=f"Arrow declared batch 1: .*{words}"):
        read(_patch(data, position, "q", value))


def test_producer_shaped_files_pass() -> None:
    # Many batches, like merged producer output.
    data = write([rows(50, start=50 * i) for i in range(8)])
    assert len(read(data).index().funcs) == 400


def test_damaged_bytes_never_raise_anything_but_a_lumina_arrow_error() -> None:
    rng = random.Random(7)
    samples = [GOLDEN, write([rows(20), rows(5, start=20)])]
    accepted = 0
    for _ in range(3000):
        data = bytearray(rng.choice(samples))
        kind = rng.randrange(3)
        if kind == 0:
            for _ in range(rng.randrange(1, 8)):
                data[rng.randrange(len(data))] = rng.randrange(256)
        elif kind == 1:
            del data[rng.randrange(len(data)) :]
        else:  # the footer and the tail hold every offset
            for _ in range(rng.randrange(1, 4)):
                data[len(data) - 1 - rng.randrange(min(400, len(data)))] = rng.randrange(256)
        try:
            arrow = read(bytes(data))
            for func in arrow.index().funcs:
                arrow.metadata_b64(func)
            accepted += 1
        except LuminaArrowError:
            pass
    # Damage inside the values is not detectable here (the pack's sha256 covers it).
    assert 0 < accepted < 3000


@pytest.mark.parametrize("bad", [b"\xff", b"\xc3"])
def test_a_name_that_is_not_utf8_is_refused(bad: bytes) -> None:
    data = write([rows(3)])
    at = data.index(b"func_1")
    damaged = data[:at] + bad + data[at + 1 :]
    with pytest.raises(LuminaArrowError, match="invalid Lumina Arrow batch"):
        read(damaged).index()
    with pytest.raises(LuminaArrowError):
        read(damaged).check()


def test_check_accepts_what_index_reads() -> None:
    arrow = read(GOLDEN)
    arrow.check()
    assert arrow.index().funcs
