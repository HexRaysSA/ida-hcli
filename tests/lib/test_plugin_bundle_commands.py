from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from hcli.commands.plugin.bundle import bundle
from hcli.lib.ida.python import PipOptions

TESTS_DIR = Path(__file__).parent.parent
PLUGIN1_V1 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v1.0.0.zip"

sys.path.insert(0, str(Path(__file__).parent))
from test_plugin_bundle import _build_bundle_zip, _make_manifest

VALID_WHEEL = "some_pkg-1.0-py3-none-any.whl"
VALID_WH_FILES = {
    f"dependencies/python/linux-x86_64-cp312/{VALID_WHEEL}": b"fake-wheel",
}


def test_bundle_info_valid_bundle(tmp_path):
    plugin_data = PLUGIN1_V1.read_bytes()
    data = _build_bundle_zip(
        _make_manifest(),
        plugin_zips={"plugin1-v1.0.0.zip": plugin_data},
        wheelhouse_files=VALID_WH_FILES,
    )
    p = tmp_path / "bundle.zip"
    p.write_bytes(data)

    runner = CliRunner()
    result = runner.invoke(bundle, ["info", str(p)])

    assert result.exit_code == 0, result.output
    assert "2026-04-28T16:00:00+00:00" in result.output
    assert "linux-x86_64-cp312" in result.output
    assert "plugin1: 1.0.0" in result.output


def test_bundle_info_not_a_bundle(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    p = tmp_path / "regular.zip"
    p.write_bytes(buf.getvalue())

    runner = CliRunner()
    result = runner.invoke(bundle, ["info", str(p)])

    assert result.exit_code != 0


def test_bundle_info_empty_bundle(tmp_path):
    data = _build_bundle_zip(_make_manifest(), wheelhouse_files=VALID_WH_FILES)
    p = tmp_path / "bundle.zip"
    p.write_bytes(data)

    runner = CliRunner()
    result = runner.invoke(bundle, ["info", str(p)])

    assert result.exit_code == 0, result.output


def test_bundle_create_from_local_zip_no_deps(tmp_path):
    out = tmp_path / "output.zip"

    runner = CliRunner()
    result = runner.invoke(
        bundle,
        ["create", "--path", str(out), "--target", "linux-x86_64-cp312", str(PLUGIN1_V1)],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code == 0, result.output
    assert out.exists()

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
        assert "plugin-bundle.json" in names
        plugin_members = [n for n in names if n.startswith("plugins/") and n.endswith(".zip")]
        assert plugin_members == ["plugins/plugin1-1.0.0.zip"]

    from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo

    repo = PluginBundleRepo(out)
    try:
        assert repo.target_ids == ["linux-x86_64-cp312"]
        plugins = repo.get_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "plugin1"
    finally:
        repo.close()


@pytest.mark.parametrize(
    "argv",
    [
        ["--target", "nonexistent-target", str(PLUGIN1_V1)],
        ["--target", "linux-x86_64-cp312", "someplugin"],
    ],
)
def test_bundle_create_rejects_bad_input(tmp_path, argv):
    out = tmp_path / "output.zip"

    runner = CliRunner()
    result = runner.invoke(
        bundle,
        ["create", "--path", str(out), *argv],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code != 0
    assert not out.exists()
