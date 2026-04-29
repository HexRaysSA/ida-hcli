import io
import json
import zipfile
from pathlib import Path

import pytest
from fixtures import *

from hcli.lib.ida.plugin.exceptions import PluginVersionDowngradeError
from hcli.lib.ida.plugin.install import (
    get_installed_plugins,
    install_plugin_archive,
    is_plugin_installed,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo

TESTS_DIR = Path(__file__).parent.parent
PLUGIN1_V1 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v1.0.0.zip"
PLUGIN1_V2 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v2.0.0.zip"


def _make_manifest(**overrides) -> dict:
    base = {
        "version": 1,
        "kind": "hcli-plugin-bundle",
        "builtAt": "2026-04-28T16:00:00Z",
        "createdBy": {"tool": "hcli", "version": "0.0.0"},
        "targetPlatformTags": [
            {
                "id": "macos-aarch64-cp312",
                "idaPlatform": "macos-aarch64",
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["macosx_11_0_arm64"],
                "wheelhouse": "dependencies/python/macos-aarch64-cp312",
            }
        ],
    }
    base.update(overrides)
    return base


def _build_bundle_zip(
    manifest_dict: dict,
    plugin_zips: dict[str, bytes] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin-bundle.json", json.dumps(manifest_dict))
        if plugin_zips:
            for name, data in plugin_zips.items():
                zf.writestr(f"plugins/{name}", data)
        for target in manifest_dict.get("targetPlatformTags", []):
            wh = target["wheelhouse"]
            zf.writestr(f"{wh}/placeholder.whl", b"fake-wheel")
    return buf.getvalue()


def test_bundle_install_plugin_from_bundle(virtual_ida_environment, tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    bundle_path.write_bytes(
        _build_bundle_zip(
            _make_manifest(),
            plugin_zips={"plugin1-v1.0.0.zip": PLUGIN1_V1.read_bytes()},
        )
    )
    repo = PluginBundleRepo(bundle_path)
    try:
        name, buf = repo.fetch_compatible_plugin_from_spec("plugin1==1.0.0", "macos-aarch64", "9.1")
    finally:
        repo.close()

    install_plugin_archive(buf, name)

    assert is_plugin_installed("plugin1")
    assert ("plugin1", "1.0.0") in get_installed_plugins()


def test_bundle_install_upgrade_plugin_from_bundle(virtual_ida_environment, tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    bundle_path.write_bytes(
        _build_bundle_zip(
            _make_manifest(),
            plugin_zips={
                "plugin1-v1.0.0.zip": PLUGIN1_V1.read_bytes(),
                "plugin1-v2.0.0.zip": PLUGIN1_V2.read_bytes(),
            },
        )
    )
    repo = PluginBundleRepo(bundle_path)
    try:
        name_v1, buf_v1 = repo.fetch_compatible_plugin_from_spec("plugin1==1.0.0", "macos-aarch64", "9.1")
        name_v2, buf_v2 = repo.fetch_compatible_plugin_from_spec("plugin1==2.0.0", "macos-aarch64", "9.1")
    finally:
        repo.close()

    install_plugin_archive(buf_v1, name_v1)
    assert ("plugin1", "1.0.0") in get_installed_plugins()

    upgrade_plugin_archive(buf_v2, name_v2)
    assert ("plugin1", "2.0.0") in get_installed_plugins()
    assert ("plugin1", "1.0.0") not in get_installed_plugins()


def test_bundle_install_downgrade_raises(virtual_ida_environment, tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    bundle_path.write_bytes(
        _build_bundle_zip(
            _make_manifest(),
            plugin_zips={
                "plugin1-v1.0.0.zip": PLUGIN1_V1.read_bytes(),
                "plugin1-v2.0.0.zip": PLUGIN1_V2.read_bytes(),
            },
        )
    )
    repo = PluginBundleRepo(bundle_path)
    try:
        name_v1, buf_v1 = repo.fetch_compatible_plugin_from_spec("plugin1==1.0.0", "macos-aarch64", "9.1")
        name_v2, buf_v2 = repo.fetch_compatible_plugin_from_spec("plugin1==2.0.0", "macos-aarch64", "9.1")
    finally:
        repo.close()

    install_plugin_archive(buf_v2, name_v2)
    assert ("plugin1", "2.0.0") in get_installed_plugins()

    with pytest.raises(PluginVersionDowngradeError):
        upgrade_plugin_archive(buf_v1, name_v1)

    assert ("plugin1", "2.0.0") in get_installed_plugins()


def test_bundle_install_fetch_unsatisfiable_spec_raises(tmp_path):
    bundle_path = tmp_path / "bundle.zip"
    bundle_path.write_bytes(
        _build_bundle_zip(
            _make_manifest(),
            plugin_zips={"plugin1-v2.0.0.zip": PLUGIN1_V2.read_bytes()},
        )
    )
    repo = PluginBundleRepo(bundle_path)
    try:
        with pytest.raises((KeyError, ValueError)):
            repo.fetch_compatible_plugin_from_spec("plugin1==1.0.0", "macos-aarch64", "9.1")
    finally:
        repo.close()
