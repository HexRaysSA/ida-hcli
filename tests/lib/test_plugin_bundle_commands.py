from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from hcli.commands.plugin.bundle import (
    _collect_all_python_deps,
    _get_cell_closures,
    _get_python_deps,
    _render_versions_by_target,
    bundle,
)
from hcli.lib.ida.plugin.bundle import PipTarget
from hcli.lib.ida.plugin.repo.bundle import PluginBundleRepo
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo
from hcli.lib.ida.plugin.resolve import Cell, Requirement, resolve
from hcli.lib.ida.python import PipOptions

TESTS_DIR = Path(__file__).parent.parent
PLUGIN1_V1 = TESTS_DIR / "data" / "plugins" / "plugin1" / "plugin1-v1.0.0.zip"

sys.path.insert(0, str(Path(__file__).parent))
from test_plugin_bundle import _build_bundle_zip, _make_manifest

VALID_WHEEL = "some_pkg-1.0-py3-none-any.whl"
VALID_WH_FILES = {
    f"dependencies/python/linux-x86_64-cp312/{VALID_WHEEL}": b"fake-wheel",
}

HOST = "https://github.com/test/test"


def _make_plugin_metadata(
    name: str,
    version: str,
    *,
    deps: list[str | dict] | None = None,
    python_deps: list[str] | str | None = None,
    components: list[str] | None = None,
    platforms: list[str] | None = None,
    requires_python: str | None = None,
    host: str = HOST,
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": host},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if deps is not None:
        plugin["dependencies"] = deps
    if python_deps is not None:
        plugin["pythonDependencies"] = python_deps
    if components is not None:
        plugin["components"] = components
    if platforms is not None:
        plugin["platforms"] = platforms
    if requires_python is not None:
        plugin["requiresPython"] = requires_python
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_plugin_zip(
    name: str,
    version: str,
    **kwargs,
) -> bytes:
    buf = io.BytesIO()
    metadata = _make_plugin_metadata(name, version, **kwargs)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(metadata))
        zf.writestr(f"{name}/{name}.py", "# plugin")
    return buf.getvalue()


def _make_pep723_content(deps: list[str]) -> str:
    lines = ["# /// script", "# dependencies = ["]
    for d in deps:
        lines.append(f'#   "{d}",')
    lines.extend(["# ]", "# ///", "", 'print("hello")'])
    return "\n".join(lines)


def _make_plugin_zip_with_inline_deps(
    name: str,
    version: str,
    inline_deps: list[str],
) -> bytes:
    buf = io.BytesIO()
    metadata = _make_plugin_metadata(name, version, python_deps="inline")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(metadata))
        zf.writestr(f"{name}/{name}.py", _make_pep723_content(inline_deps))
    return buf.getvalue()


def _make_suite_zip(
    suite_name: str,
    suite_version: str,
    components: list[tuple[str, str, dict]],
    **suite_kwargs,
) -> bytes:
    buf = io.BytesIO()
    comp_names = [c[0] for c in components]
    suite_meta = _make_plugin_metadata(suite_name, suite_version, components=comp_names, **suite_kwargs)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite")
        for comp_name, comp_version, comp_kwargs in components:
            comp_meta = _make_plugin_metadata(comp_name, comp_version, **comp_kwargs)
            zf.writestr(
                f"{suite_name}/{comp_name}/ida-plugin.json",
                json.dumps(comp_meta),
            )
            zf.writestr(f"{suite_name}/{comp_name}/{comp_name}.py", "# component")
    return buf.getvalue()


def _make_suite_zip_with_inline_component(
    suite_name: str,
    suite_version: str,
    comp_name: str,
    comp_inline_deps: list[str],
    *,
    suite_python_deps: list[str] | None = None,
) -> bytes:
    buf = io.BytesIO()
    suite_meta = _make_plugin_metadata(suite_name, suite_version, components=[comp_name], python_deps=suite_python_deps)
    comp_meta = _make_plugin_metadata(comp_name, "1.0.0", python_deps="inline")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{suite_name}/ida-plugin.json", json.dumps(suite_meta))
        zf.writestr(f"{suite_name}/{suite_name}.py", "# suite")
        zf.writestr(f"{suite_name}/{comp_name}/ida-plugin.json", json.dumps(comp_meta))
        zf.writestr(
            f"{suite_name}/{comp_name}/{comp_name}.py",
            _make_pep723_content(comp_inline_deps),
        )
    return buf.getvalue()


@contextlib.contextmanager
def _make_fs_repo(archives: dict[str, bytes]) -> Iterator[FileSystemPluginRepo]:
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp)
        for filename, data in archives.items():
            (repo_dir / filename).write_bytes(data)
        yield FileSystemPluginRepo(repo_dir)


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


# ---------------------------------------------------------------------------
# _collect_all_python_deps: unit tests
# ---------------------------------------------------------------------------


def test_collect_python_deps_from_standalone_plugin():
    buf = _make_plugin_zip("my-plugin", "1.0.0", python_deps=["requests", "httpx"])
    deps = _collect_all_python_deps(buf)
    assert sorted(deps) == ["httpx", "requests"]


def test_collect_python_deps_from_suite_includes_component_deps():
    buf = _make_suite_zip(
        "my-suite",
        "1.0.0",
        [("comp-a", "1.0.0", {"python_deps": ["pyyaml", "toml"]})],
        python_deps=["requests"],
    )
    deps = _collect_all_python_deps(buf)
    assert "requests" in deps
    assert "pyyaml" in deps
    assert "toml" in deps


def test_collect_python_deps_handles_inline_pep723():
    buf = _make_plugin_zip_with_inline_deps("my-plugin", "1.0.0", ["requests", "httpx"])
    deps = _collect_all_python_deps(buf)
    assert sorted(deps) == ["httpx", "requests"]


def test_collect_python_deps_handles_inline_pep723_on_component():
    buf = _make_suite_zip_with_inline_component(
        "my-suite",
        "1.0.0",
        "comp-a",
        ["pyyaml", "toml"],
        suite_python_deps=["requests"],
    )
    deps = _collect_all_python_deps(buf)
    assert "requests" in deps
    assert "pyyaml" in deps
    assert "toml" in deps


def test_collect_python_deps_empty_when_no_deps():
    buf = _make_plugin_zip("my-plugin", "1.0.0")
    deps = _collect_all_python_deps(buf)
    assert deps == []


# ---------------------------------------------------------------------------
# _get_cell_closures: loose dependencies
# ---------------------------------------------------------------------------

LINUX_312 = PipTarget.parse("linux-x86_64-cp312")
LINUX_311 = PipTarget.parse("linux-x86_64-cp311")
LINUX_310 = PipTarget.parse("linux-x86_64-cp310")
WINDOWS_312 = PipTarget.parse("windows-x86_64-cp312")


@contextlib.contextmanager
def _local_zip(buf: bytes) -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "local.zip"
        path.write_bytes(buf)
        yield str(path)


def test_get_cell_closures_fetches_transitive_dependencies():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", deps=["plugin-c"])
    plugin_c = _make_plugin_zip("plugin-c", "1.0.0")

    with _make_fs_repo({"a.zip": plugin_a, "b.zip": plugin_b, "c.zip": plugin_c}) as repo:
        closures = _get_cell_closures(("plugin-a",), [LINUX_312], repo).closures

    assert closures[LINUX_312] == {"plugin-a": plugin_a, "plugin-b": plugin_b, "plugin-c": plugin_c}


def test_get_cell_closures_includes_local_root_and_its_dependencies():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "2.0.0")

    with _make_fs_repo({"b.zip": plugin_b}) as repo, _local_zip(plugin_a) as local:
        closures = _get_cell_closures((local,), [LINUX_312], repo).closures

    assert closures[LINUX_312] == {"plugin-a": plugin_a, "plugin-b": plugin_b}


def test_get_cell_closures_resolves_a_dependency_cycle():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", deps=["plugin-a"])

    with _make_fs_repo({"a.zip": plugin_a, "b.zip": plugin_b}) as repo:
        closures = _get_cell_closures(("plugin-a",), [LINUX_312], repo).closures

    assert closures[LINUX_312] == {"plugin-a": plugin_a, "plugin-b": plugin_b}


def test_get_cell_closures_honors_dependency_host():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=[f"plugin-b@{HOST}"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0")

    with _make_fs_repo({"a.zip": plugin_a, "b.zip": plugin_b}) as repo:
        closures = _get_cell_closures(("plugin-a",), [LINUX_312], repo).closures

    assert closures[LINUX_312]["plugin-b"] == plugin_b


def test_get_cell_closures_keys_by_repository_plugin_name():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["Plugin-B"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0")

    with _make_fs_repo({"a.zip": plugin_a, "b.zip": plugin_b}) as repo:
        closures = _get_cell_closures(("plugin-a",), [LINUX_312], repo).closures

    assert set(closures[LINUX_312]) == {"plugin-a", "plugin-b"}


def test_get_cell_closures_skips_missing_optional_dependency():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=[{"plugin": "missing-dep", "required": False}])

    with _make_fs_repo({"a.zip": plugin_a}) as repo:
        resolution = _get_cell_closures(("plugin-a",), [LINUX_312], repo)

    assert resolution.closures[LINUX_312] == {"plugin-a": plugin_a}
    [skipped] = resolution.skipped[LINUX_312]
    assert skipped.requirement.name == "missing-dep"
    assert skipped.reason == "missing-dep is not in the repository"


def test_get_cell_closures_fails_for_required_dependency_without_repository():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])

    with (
        _local_zip(plugin_a) as local,
        pytest.raises(
            RuntimeError,
            match=re.escape(
                "cannot resolve plugin-a 1.0.0 -> plugin-b for linux-x86_64-cp312: plugin-b is not in the repository"
            ),
        ),
    ):
        _get_cell_closures((local,), [LINUX_312], None)


# ---------------------------------------------------------------------------
# bundle create: integration tests for loose deps
# ---------------------------------------------------------------------------


def test_bundle_create_includes_loose_dependencies(tmp_path):
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "2.0.0")

    plugin_a_path = tmp_path / "plugin-a.zip"
    plugin_a_path.write_bytes(plugin_a)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plugin-b.zip").write_bytes(plugin_b)

    out = tmp_path / "output.zip"
    runner = CliRunner()
    result = runner.invoke(
        bundle,
        [
            "create",
            "--path",
            str(out),
            "--target",
            "linux-x86_64-cp312",
            "--repo",
            str(repo_dir),
            str(plugin_a_path),
        ],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code == 0, result.output
    assert out.exists()

    with zipfile.ZipFile(out, "r") as zf:
        plugin_members = sorted(n for n in zf.namelist() if n.startswith("plugins/") and n.endswith(".zip"))
    assert len(plugin_members) == 2
    assert any("plugin-a" in m for m in plugin_members)
    assert any("plugin-b" in m for m in plugin_members)


# ---------------------------------------------------------------------------
# bundle create: version resolution for repository specs
# ---------------------------------------------------------------------------


def _invoke_create(repo_dir: Path, out: Path, targets: list[str], specs: list[str]):
    argv = ["create", "--path", str(out), "--repo", str(repo_dir)]
    for target in targets:
        argv.extend(["--target", target])
    argv.extend(specs)
    runner = CliRunner(mix_stderr=False)
    return runner.invoke(bundle, argv, obj={"pip_options": PipOptions()})


def _get_bundled_plugin_members(out: Path) -> list[str]:
    with zipfile.ZipFile(out, "r") as zf:
        return sorted(n for n in zf.namelist() if n.startswith("plugins/") and n.endswith(".zip"))


@pytest.fixture
def versioned_repo_dir(tmp_path) -> Path:
    """Repository with plugin-a 1.0.0 (all platforms) and 2.0.0 (Linux only)."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plugin-a-v1.zip").write_bytes(_make_plugin_zip("plugin-a", "1.0.0"))
    (repo_dir / "plugin-a-v2.zip").write_bytes(_make_plugin_zip("plugin-a", "2.0.0", platforms=["linux-x86_64"]))
    return repo_dir


@pytest.mark.parametrize("spec", ["plugin-a", f"plugin-a@{HOST}"])
def test_bundle_create_resolves_bare_spec_to_latest_version(tmp_path, versioned_repo_dir, spec):
    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312"], [spec])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/plugin-a-2.0.0.zip"]
    assert f"resolved {spec}: 2.0.0" in result.stderr


def test_bundle_create_resolves_range_spec(tmp_path, versioned_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312"], ["plugin-a<=1.5.0"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/plugin-a-1.0.0.zip"]
    assert "resolved plugin-a<=1.5.0: 1.0.0" in result.stderr


def test_bundle_create_resolves_bare_spec_per_platform(tmp_path, versioned_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312", "windows-x86_64-cp312"], ["plugin-a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/plugin-a-1.0.0-windows-x86_64.zip",
        "plugins/plugin-a-2.0.0-linux-x86_64.zip",
    ]
    assert "resolved plugin-a: 2.0.0 (linux-x86_64), 1.0.0 (windows-x86_64)" in result.stderr


def test_bundle_create_exact_spec_is_not_reported_as_resolved(tmp_path, versioned_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312"], ["plugin-a==1.0.0"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/plugin-a-1.0.0.zip"]
    assert "resolved" not in result.stderr


def test_bundle_create_resolves_bare_loose_dependency_per_platform(tmp_path, versioned_repo_dir):
    root = tmp_path / "root.zip"
    root.write_bytes(_make_plugin_zip("root", "1.0.0", deps=["plugin-a"]))

    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312", "windows-x86_64-cp312"], [str(root)])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/plugin-a-1.0.0-windows-x86_64.zip",
        "plugins/plugin-a-2.0.0-linux-x86_64.zip",
        "plugins/root-1.0.0.zip",
    ]
    assert "included dependency: plugin-a 2.0.0 (linux-x86_64), 1.0.0 (windows-x86_64)" in result.stderr


# ---------------------------------------------------------------------------
# per-cell resolution
# ---------------------------------------------------------------------------


def test_get_cell_closures_collects_python_deps_per_cell():
    plugin_a_v1 = _make_plugin_zip("plugin-a", "1.0.0", python_deps=["shared-pkg"])
    plugin_a_v2 = _make_plugin_zip(
        "plugin-a", "2.0.0", platforms=["linux-x86_64"], deps=["plugin-b"], python_deps=["linux-pkg"]
    )
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", python_deps=["dep-pkg"])

    with _make_fs_repo({"a1.zip": plugin_a_v1, "a2.zip": plugin_a_v2, "b1.zip": plugin_b}) as repo:
        closures = _get_cell_closures(("plugin-a",), [LINUX_312, WINDOWS_312], repo).closures

    assert closures[LINUX_312] == {"plugin-a": plugin_a_v2, "plugin-b": plugin_b}
    assert closures[WINDOWS_312] == {"plugin-a": plugin_a_v1}
    assert sorted(_get_python_deps(closures[LINUX_312])) == ["dep-pkg", "linux-pkg"]
    assert _get_python_deps(closures[WINDOWS_312]) == ["shared-pkg"]


def test_get_cell_closures_names_cell_and_dependent_for_unresolvable_dependency():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", platforms=["linux-x86_64"])

    with (
        _make_fs_repo({"a1.zip": plugin_a, "b1.zip": plugin_b}) as repo,
        pytest.raises(
            RuntimeError,
            match=re.escape(
                "cannot resolve plugin-a for windows-x86_64-cp312: "
                "plugin-a 1.0.0 needs plugin-b: plugin-b 1.0.0 does not support windows-x86_64"
            ),
        ),
    ):
        _get_cell_closures(("plugin-a",), [LINUX_312, WINDOWS_312], repo)


def test_render_versions_by_target_collapses_single_version():
    buf = _make_plugin_zip("plugin-a", "1.0.0")
    bufs = dict.fromkeys((LINUX_311, LINUX_312, WINDOWS_312), buf)
    assert _render_versions_by_target("plugin-a", bufs, [LINUX_311, LINUX_312, WINDOWS_312]) == "1.0.0"


def test_render_versions_by_target_groups_by_platform_when_possible():
    targets = [LINUX_311, LINUX_312, WINDOWS_312]
    v2 = _make_plugin_zip("plugin-a", "2.0.0")
    bufs = {
        LINUX_311: _make_plugin_zip("plugin-a", "1.0.0"),
        LINUX_312: v2,
        WINDOWS_312: v2,
    }
    assert (
        _render_versions_by_target("plugin-a", bufs, targets)
        == "1.0.0 (linux-x86_64-cp311), 2.0.0 (linux-x86_64-cp312, windows-x86_64)"
    )


def test_render_versions_by_target_lists_partial_coverage():
    bufs = {LINUX_312: _make_plugin_zip("plugin-a", "1.0.0")}
    assert _render_versions_by_target("plugin-a", bufs, [LINUX_312, WINDOWS_312]) == "1.0.0 (linux-x86_64)"


# ---------------------------------------------------------------------------
# requiresPython
# ---------------------------------------------------------------------------


@pytest.fixture
def requires_python_repo_dir(tmp_path) -> Path:
    """Repository with plugin-a 1.9.0 (no requiresPython) and 2.0.0 (requiresPython >=3.12)."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "plugin-a-v1.9.zip").write_bytes(_make_plugin_zip("plugin-a", "1.9.0"))
    (repo_dir / "plugin-a-v2.zip").write_bytes(_make_plugin_zip("plugin-a", "2.0.0", requires_python=">=3.12"))
    return repo_dir


def test_bundle_create_selects_version_per_python_version(tmp_path, requires_python_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(requires_python_repo_dir, out, ["linux-x86_64-cp310", "linux-x86_64-cp312"], ["plugin-a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/plugin-a-1.9.0-linux-x86_64.zip",
        "plugins/plugin-a-2.0.0-linux-x86_64.zip",
    ]
    assert "resolved plugin-a: 1.9.0 (linux-x86_64-cp310), 2.0.0 (linux-x86_64-cp312)" in " ".join(
        result.stderr.split()
    )

    repo = PluginBundleRepo(out)
    try:
        assert repo.target_ids == ["linux-x86_64-cp310", "linux-x86_64-cp312"]
        cp310 = resolve([Requirement.from_spec("plugin-a")], repo, Cell("linux-x86_64", python_version="3.10"))
        assert cp310.selected["plugin-a"].metadata.plugin.version == "1.9.0"
        cp312 = resolve([Requirement.from_spec("plugin-a")], repo, Cell("linux-x86_64", python_version="3.12"))
        assert cp312.selected["plugin-a"].metadata.plugin.version == "2.0.0"
    finally:
        repo.close()


def test_bundle_create_selects_dependency_version_per_python_version(tmp_path, requires_python_repo_dir):
    root = tmp_path / "root.zip"
    root.write_bytes(_make_plugin_zip("root", "1.0.0", deps=["plugin-a"]))

    out = tmp_path / "output.zip"
    result = _invoke_create(requires_python_repo_dir, out, ["linux-x86_64-cp310", "linux-x86_64-cp312"], [str(root)])

    assert result.exit_code == 0, result.output + result.stderr
    stderr = " ".join(result.stderr.split())
    assert "included dependency: plugin-a 1.9.0 (linux-x86_64-cp310), 2.0.0 (linux-x86_64-cp312)" in stderr


def test_bundle_create_uses_target_ids_when_platform_names_collide(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "old-python.zip").write_bytes(_make_plugin_zip("plugin-a", "1.0.0", requires_python="<3.12"))
    (repo_dir / "new-python.zip").write_bytes(_make_plugin_zip("plugin-a", "1.0.0", requires_python=">=3.12"))

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp311", "linux-x86_64-cp312"], ["plugin-a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/plugin-a-1.0.0-linux-x86_64-cp311.zip",
        "plugins/plugin-a-1.0.0-linux-x86_64-cp312.zip",
    ]


def test_bundle_create_fails_when_local_spec_excludes_cell_python(tmp_path):
    local = tmp_path / "plugin-a.zip"
    local.write_bytes(_make_plugin_zip("plugin-a", "2.0.0", requires_python=">=3.12"))

    out = tmp_path / "output.zip"
    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(
        bundle,
        ["create", "--path", str(out), "--target", "linux-x86_64-cp312", "--target", "linux-x86_64-cp310", str(local)],
        obj={"pip_options": PipOptions()},
    )

    assert result.exit_code != 0
    assert str(result.exception) == "plugin-a 2.0.0 requires Python >=3.12, which excludes target linux-x86_64-cp310"
    assert not out.exists()


def test_render_versions_by_target_lists_different_archives_of_one_version_separately():
    targets = [LINUX_310, LINUX_311, LINUX_312, WINDOWS_312]
    old_python = _make_plugin_zip("plugin-a", "1.0.0", requires_python="<3.12")
    new_python = _make_plugin_zip("plugin-a", "1.0.0", requires_python=">=3.12")
    bufs = {LINUX_310: old_python, LINUX_311: old_python, LINUX_312: new_python, WINDOWS_312: new_python}
    assert (
        _render_versions_by_target("plugin-a", bufs, targets)
        == "1.0.0 (linux-x86_64-cp310, linux-x86_64-cp311), 1.0.0 (linux-x86_64-cp312, windows-x86_64)"
    )


# ---------------------------------------------------------------------------
# resolver behavior through bundle create
# ---------------------------------------------------------------------------

LINUX_AND_WINDOWS = ["linux-x86_64-cp312", "windows-x86_64-cp312"]


def _make_repo_dir(tmp_path: Path, archives: dict[str, bytes]) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    for filename, data in archives.items():
        (repo_dir / filename).write_bytes(data)
    return repo_dir


def _get_stderr(result) -> str:
    return " ".join(result.stderr.split())


def test_bundle_create_selects_older_root_when_newest_dependency_is_unavailable(tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a2.zip": _make_plugin_zip("a", "2.0.0", deps=["b"]),
            "a1.zip": _make_plugin_zip("a", "1.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, LINUX_AND_WINDOWS, ["a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/a-1.0.0-windows-x86_64.zip",
        "plugins/a-2.0.0-linux-x86_64.zip",
        "plugins/b-1.0.0.zip",
    ]
    stderr = _get_stderr(result)
    assert "resolved a: 2.0.0 (linux-x86_64), 1.0.0 (windows-x86_64)" in stderr
    assert "included dependency: b 1.0.0 (linux-x86_64)" in stderr


def test_bundle_create_selects_older_dependency_when_its_newest_dependency_is_unavailable(tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0", deps=["c"]),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
            "c1.zip": _make_plugin_zip("c", "1.0.0", platforms=["linux-x86_64"]),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, LINUX_AND_WINDOWS, ["a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/a-1.0.0.zip",
        "plugins/b-1.0.0-windows-x86_64.zip",
        "plugins/b-2.0.0-linux-x86_64.zip",
        "plugins/c-1.0.0.zip",
    ]
    assert "included dependency: b 2.0.0 (linux-x86_64), 1.0.0 (windows-x86_64)" in _get_stderr(result)


def test_bundle_create_selects_version_by_requires_python_across_platforms(tmp_path, requires_python_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(requires_python_repo_dir, out, ["linux-x86_64-cp310", "windows-x86_64-cp312"], ["plugin-a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == [
        "plugins/plugin-a-1.9.0-linux-x86_64.zip",
        "plugins/plugin-a-2.0.0-windows-x86_64.zip",
    ]
    assert "resolved plugin-a: 1.9.0 (linux-x86_64), 2.0.0 (windows-x86_64)" in _get_stderr(result)


def test_bundle_create_skips_optional_dependency_whose_subtree_fails(tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=[{"plugin": "opt", "required": False}]),
            "opt1.zip": _make_plugin_zip("opt", "1.0.0", deps=["missing"]),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], ["a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/a-1.0.0.zip"]
    assert (
        "skipped optional dependency a 1.0.0 -> opt (linux-x86_64): "
        "opt 1.0.0 needs missing, which is not in the repository"
    ) in _get_stderr(result)


def test_bundle_create_finds_common_version_for_two_roots(tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "r1.zip": _make_plugin_zip("r1", "1.0.0", deps=["b"]),
            "r2.zip": _make_plugin_zip("r2", "1.0.0", deps=["b==1.0.0"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], ["r1", "r2"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/b-1.0.0.zip", "plugins/r1-1.0.0.zip", "plugins/r2-1.0.0.zip"]


def test_bundle_create_fails_for_conflicting_pins_from_two_roots(tmp_path):
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "r1.zip": _make_plugin_zip("r1", "1.0.0", deps=["b==1.0.0"]),
            "r2.zip": _make_plugin_zip("r2", "1.0.0", deps=["b==2.0.0"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0"),
            "b1.zip": _make_plugin_zip("b", "1.0.0"),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], ["r1", "r2"])

    assert result.exit_code != 0
    assert str(result.exception) == (
        "cannot resolve r2 1.0.0 -> b==2.0.0 for linux-x86_64-cp312: "
        "b 1.0.0 is already selected for r1 1.0.0 -> b==1.0.0"
    )
    assert not out.exists()


def test_bundle_create_fails_for_ambiguous_dependency_name(tmp_path):
    other_host = "https://github.com/other/other"
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b-test.zip": _make_plugin_zip("b", "1.0.0"),
            "b-other.zip": _make_plugin_zip("b", "1.0.0", host=other_host),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], ["a"])

    assert result.exit_code != 0
    message = str(result.exception)
    assert message.startswith("cannot resolve a 1.0.0 -> b for linux-x86_64-cp312: b is ambiguous, use one of: ")
    assert f"b@{HOST}" in message
    assert f"b@{other_host}" in message


def test_bundle_create_includes_dependency_of_a_component(tmp_path):
    suite = _make_suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["b"]})])
    plugin_b = _make_plugin_zip("b", "1.0.0")
    repo_dir = _make_repo_dir(tmp_path, {"suite.zip": suite, "b1.zip": plugin_b})

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], ["suite"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/b-1.0.0.zip", "plugins/suite-1.0.0.zip"]


def test_bundle_create_includes_dependency_of_a_local_component(tmp_path):
    suite = tmp_path / "suite.zip"
    suite.write_bytes(_make_suite_zip("suite", "1.0.0", [("comp", "1.0.0", {"deps": ["b"]})]))
    repo_dir = _make_repo_dir(tmp_path, {"b1.zip": _make_plugin_zip("b", "1.0.0")})

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], [str(suite)])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/b-1.0.0.zip", "plugins/suite-1.0.0.zip"]


def _invoke_create_with_repos(tmp_path: Path, repos: dict[str, dict[str, bytes]], targets: list[str], specs: list[str]):
    """Run bundle create against configured repositories; the first one is the default."""
    from hcli.lib.ida import PluginRepository
    from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo

    configs: dict[str, PluginRepository] = {}
    for repo_name, archives in repos.items():
        repo_dir = tmp_path / repo_name
        repo_dir.mkdir()
        for filename, data in archives.items():
            (repo_dir / filename).write_bytes(data)
        configs[repo_name] = PluginRepository(name=repo_name, url=repo_dir.as_uri(), reserved=False)
    aggregate = AggregatePluginRepo(configs)
    obj = {
        "pip_options": PipOptions(),
        "plugin_repo": aggregate,
        "plugin_repos": aggregate,
        "default_plugin_repo": next(iter(repos)),
    }

    argv = ["create", "--path", str(tmp_path / "output.zip")]
    for target in targets:
        argv.extend(["--target", target])
    argv.extend(specs)
    return CliRunner(mix_stderr=False).invoke(bundle, argv, obj=obj)


OTHER_HOST = "https://github.com/other/other"


def test_bundle_create_selects_named_repository_for_prefixed_spec(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a2.zip": _make_plugin_zip("a", "2.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0", host=OTHER_HOST)},
        },
        ["linux-x86_64-cp312"],
        ["other/a"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-1.0.0.zip"]
    assert "resolved other/a: 1.0.0" in _get_stderr(result)


def test_bundle_create_prefixed_spec_selects_from_every_repository_that_lists_the_plugin(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a2.zip": _make_plugin_zip("a", "2.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0")},
        },
        ["linux-x86_64-cp312"],
        ["other/a"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-2.0.0.zip"]


def test_bundle_create_refuses_repository_prefix_with_repo_option(tmp_path, versioned_repo_dir):
    out = tmp_path / "output.zip"
    result = _invoke_create(versioned_repo_dir, out, ["linux-x86_64-cp312"], ["other/plugin-a"])

    assert result.exit_code != 0
    assert "Cannot use the repository prefix 'other/' with --repo" in result.output
    assert not out.exists()


def test_bundle_create_fails_for_two_repository_prefixes_that_select_different_plugins(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a3.zip": _make_plugin_zip("a", "3.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0", host=OTHER_HOST)},
        },
        ["linux-x86_64-cp312"],
        ["main/a", "other/a"],
    )

    assert result.exit_code != 0
    assert str(result.exception) == (
        f"cannot resolve a@{OTHER_HOST} for linux-x86_64-cp312: a 3.0.0 is already selected for a@{HOST}"
    )
    assert not (tmp_path / "output.zip").exists()


def test_bundle_create_accepts_two_repository_prefixes_that_select_one_plugin(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a3.zip": _make_plugin_zip("a", "3.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0")},
        },
        ["linux-x86_64-cp312"],
        ["main/a", "other/a"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-3.0.0.zip"]


def test_bundle_create_resolves_dependencies_of_prefixed_plugin(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"c1.zip": _make_plugin_zip("c", "1.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["c"])},
        },
        ["linux-x86_64-cp312"],
        ["other/a"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-1.0.0.zip", "plugins/c-1.0.0.zip"]


def test_bundle_create_satisfies_bare_dependency_with_the_prefixed_root(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a2.zip": _make_plugin_zip("a", "2.0.0"), "b1.zip": _make_plugin_zip("b", "1.0.0", deps=["a"])},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0")},
        },
        ["linux-x86_64-cp312"],
        ["other/a==1.0.0", "b"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-1.0.0.zip", "plugins/b-1.0.0.zip"]


def test_bundle_create_fails_for_prefixed_spec_that_the_repository_does_not_list(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"x1.zip": _make_plugin_zip("x", "1.0.0")},
            "other": {"a1.zip": _make_plugin_zip("a", "1.0.0")},
        },
        ["linux-x86_64-cp312"],
        ["other/x"],
    )

    assert result.exit_code != 0
    assert "Plugin x is not in repository other" in " ".join(result.output.split())
    assert not (tmp_path / "output.zip").exists()


def test_bundle_create_fails_for_prefixed_spec_that_is_ambiguous_in_the_repository(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"x1.zip": _make_plugin_zip("x", "1.0.0")},
            "other": {
                "a1.zip": _make_plugin_zip("a", "1.0.0"),
                "a-other.zip": _make_plugin_zip("a", "1.0.0", host=OTHER_HOST),
            },
        },
        ["linux-x86_64-cp312"],
        ["other/a"],
    )

    assert result.exit_code != 0
    output = " ".join(result.output.split())
    assert "Plugin name 'a' is ambiguous in repository other" in output
    assert f"other/a@{HOST}" in output
    assert f"other/a@{OTHER_HOST}" in output


def test_bundle_create_treats_one_plugin_in_two_repositories_as_one_plugin(tmp_path):
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
            "other": {"b1.zip": _make_plugin_zip("b", "1.0.0"), "b2.zip": _make_plugin_zip("b", "2.0.0")},
        },
        ["linux-x86_64-cp312"],
        ["a"],
    )

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(tmp_path / "output.zip") == ["plugins/a-1.0.0.zip", "plugins/b-2.0.0.zip"]


def test_bundle_create_lists_each_ambiguous_candidate_once(tmp_path):
    other_host = "https://github.com/other/other"
    result = _invoke_create_with_repos(
        tmp_path,
        {
            "main": {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
            "other": {
                "b1.zip": _make_plugin_zip("b", "1.0.0"),
                "b-other.zip": _make_plugin_zip("b", "1.0.0", host=other_host),
            },
        },
        ["linux-x86_64-cp312"],
        ["a"],
    )

    assert result.exit_code != 0
    message = str(result.exception)
    assert message.endswith(f"b is ambiguous, use one of: b@{HOST}, b@{other_host}")


def test_bundle_create_uses_local_root_for_dependency_of_repository_root(tmp_path):
    local_b = tmp_path / "b-local.zip"
    local_b.write_bytes(_make_plugin_zip("b", "1.0.0"))
    repo_dir = _make_repo_dir(
        tmp_path,
        {
            "a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]),
            "b2.zip": _make_plugin_zip("b", "2.0.0"),
        },
    )

    out = tmp_path / "output.zip"
    result = _invoke_create(repo_dir, out, ["linux-x86_64-cp312"], [str(local_b), "a"])

    assert result.exit_code == 0, result.output + result.stderr
    assert _get_bundled_plugin_members(out) == ["plugins/a-1.0.0.zip", "plugins/b-1.0.0.zip"]
    with zipfile.ZipFile(out) as zf:
        assert zf.read("plugins/b-1.0.0.zip") == local_b.read_bytes()


def test_bundle_create_names_the_cause_of_an_invalid_local_archive(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("noep/ida-plugin.json", json.dumps(_make_plugin_metadata("noep", "1.0.0")))
    local = tmp_path / "noep.zip"
    local.write_bytes(buf.getvalue())

    out = tmp_path / "output.zip"
    runner = CliRunner(mix_stderr=False)
    argv = ["create", "--path", str(out), "--target", "linux-x86_64-cp312", str(local)]
    result = runner.invoke(bundle, argv, obj={"pip_options": PipOptions()})

    assert result.exit_code != 0
    assert "Entry point file not found in archive: 'noep.py'" in str(result.exception)
    assert not out.exists()


def test_bundle_create_fetches_archives_from_a_configured_bundle_repository(tmp_path):
    from hcli.lib.ida import PluginRepository
    from hcli.lib.ida.plugin.repo.aggregate import AggregatePluginRepo

    repo_dir = _make_repo_dir(
        tmp_path,
        {"a1.zip": _make_plugin_zip("a", "1.0.0", deps=["b"]), "b1.zip": _make_plugin_zip("b", "1.0.0")},
    )
    source_bundle = tmp_path / "source.zip"
    result = _invoke_create(repo_dir, source_bundle, ["linux-x86_64-cp312"], ["a"])
    assert result.exit_code == 0, result.output + result.stderr

    aggregate = AggregatePluginRepo(
        {"offline": PluginRepository(name="offline", url=source_bundle.as_uri(), reserved=False)}
    )
    obj = {
        "pip_options": PipOptions(),
        "plugin_repo": aggregate,
        "plugin_repos": aggregate,
        "default_plugin_repo": "offline",
    }
    out = tmp_path / "output.zip"
    argv = ["create", "--path", str(out), "--target", "linux-x86_64-cp312", "a"]
    result = CliRunner(mix_stderr=False).invoke(bundle, argv, obj=obj)

    assert result.exit_code == 0, f"{result.output}{result.stderr}{result.exception!r}"
    assert _get_bundled_plugin_members(out) == ["plugins/a-1.0.0.zip", "plugins/b-1.0.0.zip"]
