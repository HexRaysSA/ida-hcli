"""Tests for version selection that honors `requiresPython`."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import make_test_install_context

from hcli.commands.plugin import plugin as plugin_group
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.dependencies import install_dependencies
from hcli.lib.ida.plugin.install import get_installed_plugin_records, is_plugin_installed
from hcli.lib.ida.plugin.repo import Plugin, PluginArchiveLocation, is_compatible_location
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from hcli.lib.ida.python import PipOptions

HOST = "https://github.com/test/test"

CURRENT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
NEXT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor + 1}"


def _make_metadata(
    name: str,
    version: str,
    *,
    requires_python: str | None = None,
    deps: list[str] | None = None,
    python_deps: list[str] | None = None,
) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if requires_python is not None:
        plugin["requiresPython"] = requires_python
    if deps is not None:
        plugin["dependencies"] = deps
    if python_deps is not None:
        plugin["pythonDependencies"] = python_deps
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_location(name: str, version: str, *, requires_python: str | None = None) -> PluginArchiveLocation:
    return PluginArchiveLocation(
        url=f"file:///{name}-{version}.zip",
        sha256="0" * 64,
        metadata=IDAMetadataDescriptor.model_validate(_make_metadata(name, version, requires_python=requires_python)),
    )


def _make_zip(name: str, version: str, **kwargs) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(_make_metadata(name, version, **kwargs)))
        zf.writestr(f"{name}/{name}.py", "# plugin")
    return buf.getvalue()


def _make_repo(*locations: PluginArchiveLocation) -> JSONFilePluginRepo:
    versions: dict[str, list[PluginArchiveLocation]] = {}
    for location in locations:
        versions.setdefault(location.metadata.plugin.version, []).append(location)
    name = locations[0].metadata.plugin.name
    return JSONFilePluginRepo([Plugin(name=name, host=HOST, versions=versions)])


def _get_installed_version(name: str) -> str | None:
    return next((r.version for r in get_installed_plugin_records() if r.name == name), None)


def _raise_if_called() -> str:
    raise AssertionError("python version was probed")


def test_is_compatible_location_checks_requires_python():
    location = _make_location("a", "2.0.0", requires_python=">=3.12")

    assert is_compatible_location(location, "linux-x86_64", python_version="3.12.4")
    assert not is_compatible_location(location, "linux-x86_64", python_version="3.10.12")
    assert is_compatible_location(location, "linux-x86_64")


def test_is_compatible_location_tests_major_minor_as_patch_zero():
    location = _make_location("a", "2.0.0", requires_python=">=3.12.1")

    assert not is_compatible_location(location, "linux-x86_64", python_version="3.12")
    assert is_compatible_location(location, "linux-x86_64", python_version="3.13")


def test_is_compatible_location_checks_platform_and_ida_version():
    location = _make_location("a", "2.0.0")

    assert is_compatible_location(location, "linux-x86_64", ida_version="9.1")
    assert not is_compatible_location(location, "linux-riscv", ida_version="9.1")
    assert not is_compatible_location(location, "linux-x86_64", ida_version="1.0")


def test_is_compatible_location_probes_python_only_when_required():
    location = _make_location("a", "2.0.0")

    assert is_compatible_location(location, "linux-x86_64", python_version=_raise_if_called)


@pytest.fixture
def requires_python_repo_dir(tmp_path: Path):
    """Filesystem repository with a 1.9.0 (no requiresPython) and a 2.0.0 whose requiresPython is set per test."""

    def make(requires_python: str) -> Path:
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / "a-1.9.0.zip").write_bytes(_make_zip("a", "1.9.0"))
        (repo_dir / "a-2.0.0.zip").write_bytes(_make_zip("a", "2.0.0", requires_python=requires_python))
        return repo_dir

    return make


def test_install_selects_older_version_when_newest_excludes_python(
    virtual_ida_environment_with_current_python, requires_python_repo_dir
):
    repo_dir = requires_python_repo_dir(f">={NEXT_PYTHON}")

    result = CliRunner(mix_stderr=False).invoke(plugin_group, ["--repo", str(repo_dir), "install", "a"])

    assert result.exit_code == 0, result.output
    assert _get_installed_version("a") == "1.9.0"


def test_install_selects_newest_version_when_python_satisfies_it(
    virtual_ida_environment_with_current_python, requires_python_repo_dir
):
    repo_dir = requires_python_repo_dir(f">={CURRENT_PYTHON}")

    result = CliRunner(mix_stderr=False).invoke(plugin_group, ["--repo", str(repo_dir), "install", "a"])

    assert result.exit_code == 0, result.output
    assert _get_installed_version("a") == "2.0.0"


def test_upgrade_skips_version_excluded_by_python(virtual_ida_environment_with_current_python, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "a-1.0.0.zip").write_bytes(_make_zip("a", "1.0.0"))
    (repo_dir / "a-1.9.0.zip").write_bytes(_make_zip("a", "1.9.0"))
    (repo_dir / "a-2.0.0.zip").write_bytes(_make_zip("a", "2.0.0", requires_python=f">={NEXT_PYTHON}"))
    runner = CliRunner(mix_stderr=False)

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "install", "a==1.0.0"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(plugin_group, ["--repo", str(repo_dir), "upgrade", "a"])

    assert result.exit_code == 0, result.output
    assert _get_installed_version("a") == "1.9.0"


def test_install_without_requires_python_does_not_probe_python(virtual_ida_environment_without_python, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "a-1.0.0.zip").write_bytes(_make_zip("a", "1.0.0"))

    result = CliRunner(mix_stderr=False).invoke(plugin_group, ["--repo", str(repo_dir), "install", "a"])

    assert result.exit_code == 0, result.output
    assert _get_installed_version("a") == "1.0.0"


def test_dependency_that_fails_requires_python_installs_no_python_packages(
    virtual_ida_environment_with_current_python, tmp_path
):
    dep_zip = _make_zip(
        "dep", "1.0.0", requires_python=f">={NEXT_PYTHON}", python_deps=["hcli-test-package-that-does-not-exist"]
    )
    dep_path = tmp_path / "dep-1.0.0.zip"
    dep_path.write_bytes(dep_zip)
    stale_index_location = PluginArchiveLocation(
        url=dep_path.as_uri(),
        sha256=hashlib.sha256(dep_zip).hexdigest(),
        metadata=IDAMetadataDescriptor.model_validate(_make_metadata("dep", "1.0.0")),
    )
    repo = _make_repo(stale_index_location)
    root = IDAMetadataDescriptor.model_validate(_make_metadata("root", "1.0.0", deps=["dep"]))
    ctx = make_test_install_context(pip_options=PipOptions(offline=True))

    result = install_dependencies(root, repo, ctx)

    assert result.required_failure is not None
    assert result.required_failure[0] == "dep"
    assert f"requires Python >={NEXT_PYTHON}" in " ".join(result.required_failure[1].split())
    assert not is_plugin_installed("dep")
