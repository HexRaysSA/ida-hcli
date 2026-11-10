"""Tests for what PluginArchiveIndex.index_plugin_archive accepts from an archive."""

import io
import json
import zipfile

import pytest
from fixtures import PLUGINS_DIR

from hcli.lib.ida.plugin.repo import PluginArchiveIndex

# plugin1 declares https://github.com/HexRaysSA/ida-hcli as its repository
PLUGIN1_ZIP = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
HOST = "https://github.com/HexRaysSA/ida-hcli"


def edit_metadata(zip_data: bytes, **fields: object) -> bytes:
    """Rewrite fields of the plugin section of every ida-plugin.json in an archive."""
    src = zipfile.ZipFile(io.BytesIO(zip_data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("ida-plugin.json"):
                metadata = json.loads(data)
                metadata["plugin"].update(fields)
                data = json.dumps(metadata).encode("utf-8")
            dst.writestr(name, data)
    return buf.getvalue()


def no_metadata_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("README.md", "not a plugin")
    return buf.getvalue()


@pytest.mark.parametrize(
    "zip_data, expected",
    [
        (PLUGIN1_ZIP, 1),
        (no_metadata_zip(), 0),
        (edit_metadata(PLUGIN1_ZIP, entryPoint="missing.py"), 0),
        (edit_metadata(PLUGIN1_ZIP, urls={"repository": "https://github.com/someone-else/ida-hcli"}), 0),
    ],
    ids=["valid", "no-metadata", "missing-entry-point", "host-mismatch"],
)
def test_index_plugin_archive_returns_number_of_indexed_plugins(zip_data: bytes, expected: int):
    index = PluginArchiveIndex()

    assert index.index_plugin_archive(zip_data, "https://example.com/plugin.zip", expected_host=HOST) == expected
    assert len(index.get_plugins()) == expected
