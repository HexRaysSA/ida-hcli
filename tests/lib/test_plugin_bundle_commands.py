from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from hcli.commands.plugin.bundle import _collect_all_python_deps, _resolve_loose_deps, bundle
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo
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
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if deps is not None:
        plugin["dependencies"] = deps
    if python_deps is not None:
        plugin["pythonDependencies"] = python_deps
    if components is not None:
        plugin["components"] = components
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
# _resolve_loose_deps: unit tests
# ---------------------------------------------------------------------------


def test_resolve_loose_deps_fetches_direct_dependency():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "2.0.0")

    with _make_fs_repo({"plugin-b.zip": plugin_b}) as repo:
        resolved = _resolve_loose_deps({"plugin-a": plugin_a}, repo, target_platforms=None)

    assert "plugin-b" in resolved


def test_resolve_loose_deps_fetches_transitive_dependencies():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", deps=["plugin-c"])
    plugin_c = _make_plugin_zip("plugin-c", "1.0.0")

    with _make_fs_repo({"plugin-b.zip": plugin_b, "plugin-c.zip": plugin_c}) as repo:
        resolved = _resolve_loose_deps({"plugin-a": plugin_a}, repo, target_platforms=None)

    assert "plugin-b" in resolved
    assert "plugin-c" in resolved


def test_resolve_loose_deps_skips_already_included():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0")

    with _make_fs_repo({"plugin-b.zip": plugin_b}) as repo:
        resolved = _resolve_loose_deps(
            {"plugin-a": plugin_a, "plugin-b": plugin_b},
            repo,
            target_platforms=None,
        )

    assert resolved == {}


def test_resolve_loose_deps_handles_no_repo():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    resolved = _resolve_loose_deps({"plugin-a": plugin_a}, None, target_platforms=None)
    assert resolved == {}


def test_resolve_loose_deps_handles_optional_missing_dep():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=[{"plugin": "missing-dep", "required": False}])
    with _make_fs_repo({}) as repo:
        resolved = _resolve_loose_deps({"plugin-a": plugin_a}, repo, target_platforms=None)
    assert resolved == {}


def test_resolve_loose_deps_handles_cycle():
    plugin_a = _make_plugin_zip("plugin-a", "1.0.0", deps=["plugin-b"])
    plugin_b = _make_plugin_zip("plugin-b", "1.0.0", deps=["plugin-a"])

    with _make_fs_repo({"plugin-b.zip": plugin_b}) as repo:
        resolved = _resolve_loose_deps({"plugin-a": plugin_a}, repo, target_platforms=None)

    assert "plugin-b" in resolved
    assert len(resolved) == 1


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
