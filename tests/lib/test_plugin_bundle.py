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


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"version": 2}, "version"),
        ({"kind": "something-else"}, "kind"),
        ({"targetPlatformTags": [_make_manifest()["targetPlatformTags"][0]] * 2}, "duplicate target IDs"),
    ],
)
def test_manifest_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        PluginBundleManifest.model_validate(_make_manifest(**overrides))


def test_bundle_path_relative_ok():
    _validate_bundle_path("plugins/foo.zip")


@pytest.mark.parametrize(
    "member_path,message",
    [
        ("/etc/passwd", "absolute"),
        ("plugins/../../../etc/passwd", "traversal"),
        ("plugins\\foo.zip", "backslash"),
    ],
)
def test_bundle_path_rejected(member_path, message):
    with pytest.raises(ValueError, match=message):
        _validate_bundle_path(member_path)


def test_is_plugin_bundle_zip_valid(tmp_path):
    data = _build_bundle_zip(_make_manifest())
    p = tmp_path / "test.zip"
    p.write_bytes(data)
    assert is_plugin_bundle_zip(p)


def test_is_plugin_bundle_zip_rejects_non_bundles(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hello")
    regular = tmp_path / "regular.zip"
    regular.write_bytes(buf.getvalue())
    assert not is_plugin_bundle_zip(regular)

    wrong_suffix = tmp_path / "bundle.dat"
    wrong_suffix.write_bytes(_build_bundle_zip(_make_manifest()))
    assert not is_plugin_bundle_zip(wrong_suffix)

    assert not is_plugin_bundle_zip(tmp_path / "nope.zip")
    assert not is_plugin_bundle_zip(tmp_path)


def test_plugin_bundle_repo_get_plugins_by_walking(tmp_path):
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
        assert plugins[0].name == "plugin1"
        assert set(plugins[0].versions) == {"1.0.0", "2.0.0"}
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


def test_plugin_bundle_repo_find_target_for_platform(tmp_path):
    p = _write_bundle(tmp_path)
    repo = PluginBundleRepo(p)
    try:
        assert repo.target_ids == ["linux-x86_64-cp312"]
        assert repo.built_at == datetime(2026, 4, 28, 16, 0, 0, tzinfo=timezone.utc)

        target = repo.find_target_for_platform("linux-x86_64", "3.12")
        assert target is not None
        assert target.id == "linux-x86_64-cp312"

        assert repo.find_target_for_platform("windows-x86_64", "3.12") is None
        assert repo.find_target_for_platform("linux-x86_64", "3.13") is None
    finally:
        repo.close()


def test_plugin_bundle_repo_extract_wheelhouse(tmp_path):
    wh_files = {
        "dependencies/python/linux-x86_64-cp312/some_pkg-1.0-py3-none-any.whl": b"wheel-bytes",
    }
    p = _write_bundle(tmp_path, wheelhouse_files=wh_files)
    repo = PluginBundleRepo(p)
    try:
        target = repo.find_target_for_platform("linux-x86_64", "3.12")
        assert target is not None
        dest = tmp_path / "extracted_wh"
        repo.extract_wheelhouse(target, dest)
        assert (dest / "some_pkg-1.0-py3-none-any.whl").read_bytes() == b"wheel-bytes"
    finally:
        repo.close()


def test_plugin_bundle_repo_extract_wheelhouse_rejects_duplicate_names(tmp_path):
    wh_files = {
        "dependencies/python/linux-x86_64-cp312/pkg-1.0-py3-none-any.whl": b"a",
        "dependencies/python/linux-x86_64-cp312/nested/pkg-1.0-py3-none-any.whl": b"b",
    }
    p = _write_bundle(tmp_path, wheelhouse_files=wh_files)
    repo = PluginBundleRepo(p)
    try:
        target = repo.find_target_for_platform("linux-x86_64", "3.12")
        assert target is not None
        with pytest.raises(ValueError, match="duplicate filename"):
            repo.extract_wheelhouse(target, tmp_path / "extracted_wh")
    finally:
        repo.close()


def test_pip_target_parse_valid():
    target = PipTarget.parse("linux-x86_64-cp312")
    assert target.ida_platform == "linux-x86_64"
    assert target.python_version == "3.12"
    assert target.id == "linux-x86_64-cp312"


@pytest.mark.parametrize(
    "target_id,message",
    [
        ("solaris-sparc-cp313", "unknown platform"),
        ("not-a-valid-target", "invalid target ID"),
        ("linux-x86_64-cp39", "below minimum"),
    ],
)
def test_pip_target_parse_rejected(target_id, message):
    with pytest.raises(ValueError, match=message):
        PipTarget.parse(target_id)


@pytest.mark.parametrize("python_version", SUPPORTED_PYTHON_VERSIONS)
def test_pip_target_id_round_trips(python_version):
    target = PipTarget(ida_platform="macos-aarch64", python_version=python_version)
    assert PipTarget.parse(target.id) == target


@pytest.mark.parametrize(
    "target_id,expected_tags",
    [
        (
            "linux-x86_64-cp312",
            ("manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "manylinux1_x86_64"),
        ),
        ("windows-x86_64-cp312", ("win_amd64",)),
        ("macos-x86_64-cp312", ("macosx_10_13_x86_64", "macosx_10_9_x86_64", "macosx_10_13_universal2")),
        ("macos-aarch64-cp312", ("macosx_11_0_arm64", "macosx_11_0_universal2")),
    ],
)
def test_pip_target_pip_platform_tags(target_id, expected_tags):
    tags = PipTarget.parse(target_id).pip_platform_tags
    for tag in expected_tags:
        assert tag in tags, f"{target_id} missing {tag}"


def test_pip_target_pip_download_args_format():
    target = PipTarget.parse("linux-x86_64-cp312")
    args = target.pip_download_args()
    assert "--only-binary=:all:" in args
    assert args[args.index("--implementation") + 1] == "cp"
    assert args[args.index("--python-version") + 1] == "3.12"
    abis = [args[i + 1] for i, a in enumerate(args) if a == "--abi"]
    assert abis == ["cp312", "abi3", "none"]
    platforms = [args[i + 1] for i, a in enumerate(args) if a == "--platform"]
    assert platforms == list(target.pip_platform_tags)


def test_bundle_dependency_source_matching_target_yields_wheelhouse(tmp_path):
    wheel = "some_pkg-1.0-py3-none-any.whl"
    p = _write_bundle(
        tmp_path,
        wheelhouse_files={f"dependencies/python/linux-x86_64-cp312/{wheel}": b"wheel"},
    )
    repo = PluginBundleRepo(p)
    try:
        with bundle_dependency_source(repo, "linux-x86_64", "3.12") as opts:
            assert opts is not None
            assert opts.offline is False
            assert len(opts.find_links) == 1
            assert (Path(opts.find_links[0]) / wheel).read_bytes() == b"wheel"
    finally:
        repo.close()


def test_bundle_dependency_source_no_matching_target_returns_none(tmp_path):
    p = _write_bundle(tmp_path)
    repo = PluginBundleRepo(p)
    try:
        with bundle_dependency_source(repo, "windows-x86_64", "3.12") as opts:
            assert opts is None
    finally:
        repo.close()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("linux-x86_64", "linux-x86_64"),
        ("linux", "linux-x86_64"),
        ("win", "windows-x86_64"),
        ("macos-arm64", "macos-aarch64"),
        ("macos-intel", "macos-x86_64"),
        ("WINDOWS", "windows-x86_64"),
        ("  Linux  ", "linux-x86_64"),
    ],
)
def test_resolve_platform_alias(name, expected):
    assert resolve_platform_alias(name) == expected


def test_resolve_platform_alias_unknown_raises():
    with pytest.raises(ValueError, match="unknown platform"):
        resolve_platform_alias("solaris")


def test_resolve_targets_cross_product_deduplicated():
    targets = _resolve_targets(("linux", "windows", "linux"), ("3.12", "3.13", "3.12"), ())
    ids = [t.id for t in targets]
    assert ids == [
        "linux-x86_64-cp312",
        "linux-x86_64-cp313",
        "windows-x86_64-cp312",
        "windows-x86_64-cp313",
    ]


def test_resolve_targets_all():
    targets = _resolve_targets(("all",), ("all",), ())
    assert len(targets) == len(ALL_PLATFORMS) * len(SUPPORTED_PYTHON_VERSIONS)
    assert {t.ida_platform for t in targets} == set(ALL_PLATFORMS)
    assert {t.python_version for t in targets} == set(SUPPORTED_PYTHON_VERSIONS)


def test_resolve_targets_current(monkeypatch):
    monkeypatch.setattr("hcli.lib.ida.find_current_ida_platform", lambda: "linux-x86_64")
    monkeypatch.setattr("hcli.lib.ida.python.detect_current_python_version", lambda: "3.13")
    targets = _resolve_targets(("current",), ("current",), ())
    assert [t.id for t in targets] == ["linux-x86_64-cp313"]


def test_resolve_targets_legacy_target_flag():
    targets = _resolve_targets((), (), ("linux-x86_64-cp312",))
    assert [t.id for t in targets] == ["linux-x86_64-cp312"]


@pytest.mark.parametrize(
    "platforms,pythons,targets,message",
    [
        (("linux",), (), (), "--python is required"),
        ((), ("3.12",), (), "--platform is required"),
        (("linux",), (), ("linux-x86_64-cp312",), "cannot be combined"),
        (("solaris",), ("3.12",), (), "unknown platform"),
        (("linux",), ("3.9",), (), "below minimum"),
        (("linux",), ("bogus",), (), "invalid python version"),
    ],
)
def test_resolve_targets_rejected(platforms, pythons, targets, message):
    with pytest.raises(click.BadParameter, match=message):
        _resolve_targets(platforms, pythons, targets)
