import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import click
import pytest

from hcli.commands.plugin.bundle import (
    _resolve_targets,
)
from hcli.lib.ida.plugin.bundle import (
    ALL_PLATFORMS,
    SUPPORTED_PYTHON_VERSIONS,
    PipTarget,
    bundle_dependency_source,
    resolve_platform_alias,
)
from hcli.lib.ida.plugin.repo.bundle import (
    PluginBundleManifest,
    PluginBundleRepo,
    _validate_bundle_path,
    is_plugin_bundle_zip,
)

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
                "id": "linux-x86_64-cp312",
                "idaPlatform": "linux-x86_64",
                "pythonVersion": "3.12",
                "implementation": "cp",
                "abis": ["cp312", "abi3", "none"],
                "pipPlatformTags": ["manylinux_2_28_x86_64"],
                "wheelhouse": "dependencies/python/linux-x86_64-cp312",
            }
        ],
    }
    base.update(overrides)
    return base


def _build_bundle_zip(
    manifest_dict: dict,
    plugin_zips: dict[str, bytes] | None = None,
    wheelhouse_files: dict[str, bytes] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin-bundle.json", json.dumps(manifest_dict))

        if plugin_zips:
            for name, data in plugin_zips.items():
                zf.writestr(f"plugins/{name}", data)

        if wheelhouse_files:
            for name, data in wheelhouse_files.items():
                zf.writestr(name, data)
        else:
            for target in manifest_dict.get("targetPlatformTags", []):
                wh = target["wheelhouse"]
                zf.writestr(f"{wh}/placeholder.whl", b"fake-wheel")

    return buf.getvalue()


def _write_bundle(tmp_path, manifest=None, plugin_zips=None, **kwargs):
    if manifest is None:
        manifest = _make_manifest()
    data = _build_bundle_zip(manifest, plugin_zips=plugin_zips, **kwargs)
    p = tmp_path / "bundle.zip"
    p.write_bytes(data)
    return p


def test_manifest_valid():
    m = PluginBundleManifest.model_validate(_make_manifest())
    assert m.version == 1
    assert m.kind == "hcli-plugin-bundle"
    assert len(m.target_platform_tags) == 1
    assert m.target_platform_tags[0].id == "linux-x86_64-cp312"


def test_manifest_wrong_version_rejected():
    with pytest.raises(ValueError):
        PluginBundleManifest.model_validate(_make_manifest(version=2))


def test_manifest_wrong_kind_rejected():
    with pytest.raises(ValueError):
        PluginBundleManifest.model_validate(_make_manifest(kind="something-else"))


def test_manifest_duplicate_target_ids_rejected():
    tags = _make_manifest()["targetPlatformTags"]
    with pytest.raises(ValueError, match="duplicate target IDs"):
        PluginBundleManifest.model_validate(_make_manifest(targetPlatformTags=[tags[0], tags[0]]))


def test_manifest_built_at_parsed():
    m = PluginBundleManifest.model_validate(_make_manifest())
    assert m.built_at == datetime(2026, 4, 28, 16, 0, 0, tzinfo=timezone.utc)


def test_bundle_path_relative_ok():
    _validate_bundle_path("plugins/foo.zip")


def test_bundle_path_absolute_rejected():
    with pytest.raises(ValueError, match="absolute"):
        _validate_bundle_path("/etc/passwd")


def test_bundle_path_traversal_rejected():
    with pytest.raises(ValueError, match="traversal"):
        _validate_bundle_path("plugins/../../../etc/passwd")


def test_bundle_path_backslash_rejected():
    with pytest.raises(ValueError, match="backslash"):
        _validate_bundle_path("plugins\\foo.zip")


def test_is_plugin_bundle_zip_valid(tmp_path):
    data = _build_bundle_zip(_make_manifest())
    p = tmp_path / "test.zip"
    p.write_bytes(data)
    assert is_plugin_bundle_zip(p)


def test_is_plugin_bundle_zip_regular_zip_not_bundle(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    p = tmp_path / "test.zip"
    p.write_bytes(buf.getvalue())
    assert not is_plugin_bundle_zip(p)


def test_is_plugin_bundle_zip_nonexistent_file(tmp_path):
    assert not is_plugin_bundle_zip(tmp_path / "nope.zip")


def test_is_plugin_bundle_zip_directory_not_bundle(tmp_path):
    assert not is_plugin_bundle_zip(tmp_path)


def test_plugin_bundle_repo_get_plugins_by_walking(tmp_path):
    plugin_data = PLUGIN1_V1.read_bytes()
    p = _write_bundle(tmp_path, plugin_zips={"plugin1-v1.0.0.zip": plugin_data})
    repo = PluginBundleRepo(p)
    try:
        plugins = repo.get_plugins()
        assert len(plugins) == 1
        assert plugins[0].name == "plugin1"
        assert "1.0.0" in plugins[0].versions
    finally:
        repo.close()


def test_plugin_bundle_repo_get_plugins_multiple_versions(tmp_path):
    p = _write_bundle(
        tmp_path,
        plugin_zips={
            "plugin1-v1.0.0.zip": PLUGIN1_V1.read_bytes(),
            "plugin1-v2.0.0.zip": PLUGIN1_V2.read_bytes(),
        },
    )
    repo = PluginBundleRepo(p)
    try:
        plugins = repo.get_plugins()
        assert len(plugins) == 1
        assert "1.0.0" in plugins[0].versions
        assert "2.0.0" in plugins[0].versions
    finally:
        repo.close()


def test_plugin_bundle_repo_fetch_plugin_from_bundle(tmp_path):
    plugin_data = PLUGIN1_V1.read_bytes()
    p = _write_bundle(tmp_path, plugin_zips={"plugin1-v1.0.0.zip": plugin_data})
    repo = PluginBundleRepo(p)
    try:
        name, buf = repo.fetch_compatible_plugin_from_spec("plugin1==1.0.0", "macos-aarch64", "9.1")
        assert name == "plugin1"
        assert buf == plugin_data
    finally:
        repo.close()


def test_plugin_bundle_repo_manifest_properties(tmp_path):
    p = _write_bundle(tmp_path)
    repo = PluginBundleRepo(p)
    try:
        assert repo.target_ids == ["linux-x86_64-cp312"]
        assert repo.built_at == datetime(2026, 4, 28, 16, 0, 0, tzinfo=timezone.utc)
    finally:
        repo.close()


def test_plugin_bundle_repo_find_target_for_platform(tmp_path):
    p = _write_bundle(tmp_path)
    repo = PluginBundleRepo(p)
    try:
        target = repo.find_target_for_platform("linux-x86_64", "3.12")
        assert target is not None
        assert target.id == "linux-x86_64-cp312"

        assert repo.find_target_for_platform("windows-x86_64", "3.12") is None
    finally:
        repo.close()


def test_plugin_bundle_repo_extract_wheelhouse(tmp_path):
    manifest = _make_manifest()
    wh_files = {
        "dependencies/python/linux-x86_64-cp312/some_pkg-1.0-py3-none-any.whl": b"wheel-bytes",
    }
    p = _write_bundle(tmp_path, manifest=manifest, wheelhouse_files=wh_files)
    repo = PluginBundleRepo(p)
    try:
        target = repo.find_target_for_platform("linux-x86_64", "3.12")
        assert target is not None
        dest = tmp_path / "extracted_wh"
        repo.extract_wheelhouse(target, dest)
        assert (dest / "some_pkg-1.0-py3-none-any.whl").read_bytes() == b"wheel-bytes"
    finally:
        repo.close()


def test_plugin_bundle_repo_empty_bundle_returns_no_plugins(tmp_path):
    p = _write_bundle(tmp_path)
    repo = PluginBundleRepo(p)
    try:
        assert repo.get_plugins() == []
    finally:
        repo.close()


def test_pip_target_parse_valid():
    target = PipTarget.parse("linux-x86_64-cp312")
    assert target.ida_platform == "linux-x86_64"
    assert target.python_version == "3.12"
    assert target.id == "linux-x86_64-cp312"


def test_pip_target_parse_unknown_platform_raises():
    with pytest.raises(ValueError, match="unknown platform"):
        PipTarget.parse("solaris-sparc-cp313")


def test_pip_target_parse_invalid_format_raises():
    with pytest.raises(ValueError, match="invalid target ID"):
        PipTarget.parse("not-a-valid-target")


def test_pip_target_parse_below_minimum_python_raises():
    with pytest.raises(ValueError, match="below minimum"):
        PipTarget.parse("linux-x86_64-cp39")


def test_pip_target_linux_includes_manylinux_ladder():
    target = PipTarget.parse("linux-x86_64-cp312")
    assert "manylinux_2_28_x86_64" in target.pip_platform_tags
    assert "manylinux_2_17_x86_64" in target.pip_platform_tags
    assert "manylinux2014_x86_64" in target.pip_platform_tags
    assert "manylinux1_x86_64" in target.pip_platform_tags


def test_pip_target_abis_include_abi3_and_none():
    for platform in ("linux-x86_64", "windows-x86_64", "macos-aarch64", "macos-x86_64"):
        target = PipTarget(ida_platform=platform, python_version="3.12")
        assert "abi3" in target.abis, f"{platform} missing abi3"
        assert "none" in target.abis, f"{platform} missing none"


def test_pip_target_pip_download_args_format():
    target = PipTarget.parse("linux-x86_64-cp312")
    args = target.pip_download_args()
    assert "--only-binary=:all:" in args
    assert "--implementation" in args
    assert args[args.index("--implementation") + 1] == "cp"
    assert "--python-version" in args
    assert args[args.index("--python-version") + 1] == "3.12"
    assert args.count("--abi") == 3
    assert args.count("--platform") >= 3


def test_pip_target_macos_includes_version_ladder():
    target = PipTarget.parse("macos-x86_64-cp312")
    tags = target.pip_platform_tags
    assert "macosx_10_13_x86_64" in tags
    assert "macosx_10_9_x86_64" in tags
    assert any("universal2" in t for t in tags)


def test_pip_target_macos_arm64_includes_universal2():
    target = PipTarget.parse("macos-aarch64-cp312")
    tags = target.pip_platform_tags
    assert "macosx_11_0_arm64" in tags
    assert any("universal2" in t for t in tags)


def test_pip_target_windows_tags():
    target = PipTarget.parse("windows-x86_64-cp312")
    assert target.pip_platform_tags == ("win_amd64",)


def test_pip_target_id_derivation():
    target = PipTarget(ida_platform="macos-aarch64", python_version="3.13")
    assert target.id == "macos-aarch64-cp313"


def test_pip_target_python_314_works():
    target = PipTarget(ida_platform="linux-x86_64", python_version="3.14")
    assert target.id == "linux-x86_64-cp314"
    assert "cp314" in target.abis


def test_bundle_dependency_source_matching_target_returns_pip_options(tmp_path):
    manifest = _make_manifest()
    wh_files = {
        "dependencies/python/linux-x86_64-cp312/pkg-1.0-py3-none-any.whl": b"wheel",
    }
    p = tmp_path / "bundle.zip"
    p.write_bytes(_build_bundle_zip(manifest, wheelhouse_files=wh_files))
    repo = PluginBundleRepo(p)
    try:
        with bundle_dependency_source(repo, "linux-x86_64", "3.12") as opts:
            assert opts is not None
            assert opts.offline is True
            assert opts.isolated is True
            assert opts.no_cache_dir is True
            assert opts.disable_pip_version_check is True
            assert len(opts.find_links) == 1
    finally:
        repo.close()


def test_bundle_dependency_source_no_matching_target_returns_none(tmp_path):
    manifest = _make_manifest()
    p = tmp_path / "bundle.zip"
    p.write_bytes(_build_bundle_zip(manifest))
    repo = PluginBundleRepo(p)
    try:
        with bundle_dependency_source(repo, "windows-x86_64", "3.12") as opts:
            assert opts is None
    finally:
        repo.close()


def test_resolve_platform_alias_canonical():
    assert resolve_platform_alias("linux-x86_64") == "linux-x86_64"
    assert resolve_platform_alias("windows-x86_64") == "windows-x86_64"


def test_resolve_platform_alias_short():
    assert resolve_platform_alias("linux") == "linux-x86_64"
    assert resolve_platform_alias("windows") == "windows-x86_64"
    assert resolve_platform_alias("win") == "windows-x86_64"
    assert resolve_platform_alias("macos-arm64") == "macos-aarch64"
    assert resolve_platform_alias("macos-intel") == "macos-x86_64"


def test_resolve_platform_alias_case_insensitive():
    assert resolve_platform_alias("Linux") == "linux-x86_64"
    assert resolve_platform_alias("WINDOWS") == "windows-x86_64"


def test_resolve_platform_alias_unknown_raises():
    with pytest.raises(ValueError, match="unknown platform"):
        resolve_platform_alias("solaris")


def test_resolve_platform_alias_error_lists_valid():
    with pytest.raises(ValueError, match="linux") as exc_info:
        resolve_platform_alias("solaris")
    msg = str(exc_info.value)
    assert "windows" in msg
    assert "macos-arm64" in msg


def test_pip_target_from_alias_platforms():
    target = PipTarget(ida_platform="linux-x86_64", python_version="3.12")
    assert target.id == "linux-x86_64-cp312"


def test_resolve_targets_platform_and_python_cross_product():
    targets = _resolve_targets(("linux", "windows"), ("3.12", "3.13"), ())
    ids = {t.id for t in targets}
    assert ids == {"linux-x86_64-cp312", "linux-x86_64-cp313", "windows-x86_64-cp312", "windows-x86_64-cp313"}


def test_resolve_targets_missing_python_raises():
    with pytest.raises(click.BadParameter, match="--python is required"):
        _resolve_targets(("linux",), (), ())


def test_resolve_targets_missing_platform_raises():
    with pytest.raises(click.BadParameter, match="--platform is required"):
        _resolve_targets((), ("3.12",), ())


def test_resolve_targets_neither_specified_raises():
    with pytest.raises(click.BadParameter, match="--platform is required"):
        _resolve_targets((), (), ())


def test_resolve_targets_platform_all():
    targets = _resolve_targets(("all",), ("3.12",), ())
    assert len(targets) == len(ALL_PLATFORMS)
    platforms = {t.ida_platform for t in targets}
    assert platforms == set(ALL_PLATFORMS)


def test_resolve_targets_python_all():
    targets = _resolve_targets(("linux",), ("all",), ())
    assert len(targets) == len(SUPPORTED_PYTHON_VERSIONS)
    versions = {t.python_version for t in targets}
    assert versions == set(SUPPORTED_PYTHON_VERSIONS)


def test_resolve_targets_all_and_all():
    targets = _resolve_targets(("all",), ("all",), ())
    assert len(targets) == len(ALL_PLATFORMS) * len(SUPPORTED_PYTHON_VERSIONS)


def test_resolve_targets_current_platform(monkeypatch):
    monkeypatch.setattr("hcli.lib.ida.find_current_ida_platform", lambda: "linux-x86_64")
    targets = _resolve_targets(("current",), ("3.12",), ())
    assert len(targets) == 1
    assert targets[0].ida_platform == "linux-x86_64"


def test_resolve_targets_current_python(monkeypatch):
    monkeypatch.setattr("hcli.lib.ida.python.detect_current_python_version", lambda: "3.13")
    targets = _resolve_targets(("linux",), ("current",), ())
    assert len(targets) == 1
    assert targets[0].python_version == "3.13"


def test_resolve_targets_deduplicates():
    targets = _resolve_targets(("linux", "linux"), ("3.12", "3.12"), ())
    assert len(targets) == 1
    assert targets[0].id == "linux-x86_64-cp312"


def test_resolve_targets_legacy_target_flag():
    targets = _resolve_targets((), (), ("linux-x86_64-cp312",))
    assert len(targets) == 1
    assert targets[0].id == "linux-x86_64-cp312"


def test_resolve_targets_target_and_platform_raises():
    with pytest.raises(click.BadParameter, match="cannot be combined"):
        _resolve_targets(("linux",), (), ("linux-x86_64-cp312",))


def test_resolve_targets_bad_platform_raises():
    with pytest.raises(click.BadParameter, match="unknown platform"):
        _resolve_targets(("solaris",), ("3.12",), ())
