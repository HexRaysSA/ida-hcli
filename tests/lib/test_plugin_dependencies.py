"""Tests for loose plugin dependencies."""

from __future__ import annotations

import contextlib
import io
import json
import logging
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from pydantic import ValidationError

from hcli.commands.plugin import plugin as plugin_group
from hcli.commands.plugin.lint import _check_dependency_specs
from hcli.lib.ida.plugin import DependencySpec, IDAMetadataDescriptor
from hcli.lib.ida.plugin.exceptions import DependencyUnavailableError
from hcli.lib.ida.plugin.install import (
    get_installed_plugin_records,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.reference import parse_dependency_spec
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo

logger = logging.getLogger(__name__)

HOST = "https://github.com/test/test-pack"


def _make_plugin_metadata(
    name: str, version: str, deps: list | None = None, settings: list[dict] | None = None
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
    if settings is not None:
        plugin["settings"] = settings
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_plugin_zip(name: str, version: str, deps: list | None = None, settings: list[dict] | None = None) -> bytes:
    buf = io.BytesIO()
    metadata = _make_plugin_metadata(name, version, deps, settings)
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/ida-plugin.json", json.dumps(metadata))
        zf.writestr(f"{name}/{name}.py", "# plugin")
    return buf.getvalue()


@contextlib.contextmanager
def _make_fs_repo(archives: dict[str, bytes]) -> Iterator[FileSystemPluginRepo]:
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp)
        for filename, data in archives.items():
            (repo_dir / filename).write_bytes(data)
        yield FileSystemPluginRepo(repo_dir)


def _write_fs_repo(root: Path, archives: dict[str, bytes]) -> Path:
    repo_dir = root / "repo"
    repo_dir.mkdir(exist_ok=True)
    for filename, data in archives.items():
        (repo_dir / filename).write_bytes(data)
    return repo_dir


@contextlib.contextmanager
def _noninteractive_console() -> Iterator[None]:
    from hcli.lib.console import console

    old = console.is_interactive
    console.is_interactive = False
    try:
        yield
    finally:
        console.is_interactive = old


def _invoke(runner: CliRunner, repo_dir: Path, *args: str):
    with _noninteractive_console():
        return runner.invoke(plugin_group, ["--repo", str(repo_dir), *args])


API_KEY = {"key": "api_key", "type": "string", "required": True, "name": "API key"}


def _get_installed_version(name: str) -> str | None:
    for r in get_installed_plugin_records():
        if r.name == name:
            return r.version
    return None


# ---------------------------------------------------------------------------
# parse_dependency_spec
# ---------------------------------------------------------------------------


def test_parse_dependency_spec_bare_name():
    ref = parse_dependency_spec("my-plugin")
    assert ref.name == "my-plugin"
    assert ref.version_spec == ""
    assert ref.host is None
    assert ref.repo is None


def test_parse_dependency_spec_pinned_version():
    ref = parse_dependency_spec("my-plugin==1.2.3")
    assert ref.name == "my-plugin"
    assert ref.version_spec == "==1.2.3"
    assert ref.host is None


def test_parse_dependency_spec_with_host():
    ref = parse_dependency_spec("my-plugin@https://github.com/org/repo")
    assert ref.name == "my-plugin"
    assert ref.version_spec == ""
    assert ref.host == "https://github.com/org/repo"


def test_parse_dependency_spec_with_host_and_version():
    ref = parse_dependency_spec("my-plugin==2.0.0@https://github.com/org/repo")
    assert ref.name == "my-plugin"
    assert ref.version_spec == "==2.0.0"
    assert ref.host == "https://github.com/org/repo"


def test_parse_dependency_spec_rejects_repo_prefix():
    with pytest.raises(ValueError, match="repository prefix"):
        parse_dependency_spec("community/my-plugin")


def test_parse_dependency_spec_rejects_empty():
    with pytest.raises(ValueError):
        parse_dependency_spec("")


def test_parse_dependency_spec_rejects_invalid_name():
    with pytest.raises(ValueError):
        parse_dependency_spec("-bad-name")


def test_parse_dependency_spec_rejects_non_equality_operators():
    with pytest.raises(ValueError, match="only supports =="):
        parse_dependency_spec("my-plugin>=1.0.0")


# ---------------------------------------------------------------------------
# PluginMetadata.dependencies field
# ---------------------------------------------------------------------------

MINIMAL_METADATA = {
    "IDAMetadataDescriptorVersion": 1,
    "plugin": {
        "name": "test-pack",
        "version": "1.0.0",
        "entryPoint": "noop.py",
        "urls": {"repository": HOST},
        "authors": [{"name": "Test Author", "email": "test@example.com"}],
    },
}


def _metadata_with_deps(deps: list[str]) -> dict:
    data = json.loads(json.dumps(MINIMAL_METADATA))
    data["plugin"]["dependencies"] = deps
    return data


def test_metadata_with_dependencies():
    data = _metadata_with_deps(["dep-a", "dep-b==1.0.0"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert [spec.plugin for spec in descriptor.plugin.dependencies] == ["dep-a", "dep-b==1.0.0"]


def test_metadata_without_dependencies():
    descriptor = IDAMetadataDescriptor.model_validate(MINIMAL_METADATA)
    assert descriptor.plugin.dependencies == []


def test_metadata_empty_dependencies():
    data = _metadata_with_deps([])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert descriptor.plugin.dependencies == []


def test_metadata_with_host_dependency():
    data = _metadata_with_deps(["dep-a@https://github.com/org/repo"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert [spec.plugin for spec in descriptor.plugin.dependencies] == ["dep-a@https://github.com/org/repo"]


def test_metadata_rejects_invalid_dependency_spec():
    data = _metadata_with_deps(["community/bad-prefix"])
    with pytest.raises(ValidationError):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_serialization_includes_dependencies():
    data = _metadata_with_deps(["dep-a", "dep-b"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    serialized = descriptor.model_dump(mode="json", by_alias=True)
    assert serialized["plugin"]["dependencies"] == ["dep-a", "dep-b"]


def test_metadata_serialization_empty_dependencies():
    descriptor = IDAMetadataDescriptor.model_validate(MINIMAL_METADATA)
    serialized = descriptor.model_dump(mode="json", by_alias=True)
    assert serialized["plugin"]["dependencies"] == []


# ---------------------------------------------------------------------------
# Installing a pack resolves its dependencies through the planner
# ---------------------------------------------------------------------------


def test_install_pack_installs_dependencies(virtual_ida_environment):
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "2.0.0")

    with _make_fs_repo({"dep-a.zip": dep_a_zip, "dep-b.zip": dep_b_zip}) as repo:
        result = install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)

    assert {r.name for r in result.committed_operations} == {"dep-a", "dep-b", "my-pack"}
    assert is_plugin_installed("dep-a")
    assert is_plugin_installed("dep-b")
    assert is_plugin_installed("my-pack")


def test_install_pack_skips_already_installed_dep(virtual_ida_environment):
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    install_plugin_archive(dep_a_zip, "dep-a", check_environment=False)

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    with _make_fs_repo({"dep-a.zip": dep_a_zip}) as repo:
        result = install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)

    assert [r.name for r in result.committed_operations] == ["my-pack"]
    assert [r.name for r in result.present] == ["dep-a"]


def test_install_pack_upgrades_outdated_pinned_dep(virtual_ida_environment):
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")
    install_plugin_archive(dep_a_v1, "dep-a", check_environment=False)
    assert _get_installed_version("dep-a") == "1.0.0"

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a==2.0.0"])
    with _make_fs_repo({"dep-a-v2.zip": dep_a_v2}) as repo:
        result = install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)

    dep = result.node_for_name("dep-a")
    assert dep is not None
    assert dep.outcome == "upgraded"
    assert dep.previous_version == "1.0.0"
    assert _get_installed_version("dep-a") == "2.0.0"


def test_install_pack_keeps_newer_installed_dep_over_pin(virtual_ida_environment):
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")
    install_plugin_archive(dep_a_v2, "dep-a", check_environment=False)

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a==1.0.0"])
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    with _make_fs_repo({"dep-a-v1.zip": dep_a_v1}) as repo:
        result = install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)

    assert [r.name for r in result.committed_operations] == ["my-pack"]
    assert [r.name for r in result.present] == ["dep-a"]
    assert any("not downgrading" in w for w in result.plan.warnings)
    assert _get_installed_version("dep-a") == "2.0.0"


def test_install_pack_missing_dep_blocks_whole_install(virtual_ida_environment):
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-missing"])
    with (
        _make_fs_repo({"dep-a.zip": dep_a_zip}) as repo,
        pytest.raises(DependencyUnavailableError, match="dep-missing"),
    ):
        install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)

    assert not is_plugin_installed("dep-a")
    assert not is_plugin_installed("my-pack")


def test_install_pack_without_repo_fails_when_deps_missing(virtual_ida_environment):
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])

    with pytest.raises(DependencyUnavailableError, match="no plugin repository"):
        install_plugin_archive(pack_zip, "my-pack", check_environment=False)

    assert not is_plugin_installed("my-pack")


def test_install_pack_without_repo_succeeds_when_deps_present(virtual_ida_environment):
    install_plugin_archive(_make_plugin_zip("dep-a", "1.0.0"), "dep-a", check_environment=False)
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])

    result = install_plugin_archive(pack_zip, "my-pack", check_environment=False)

    assert [r.name for r in result.committed_operations] == ["my-pack"]
    assert is_plugin_installed("my-pack")


def test_install_cli_local_archive_blocks_on_missing_dependency(virtual_ida_environment, tmp_path):
    pack_zip = tmp_path / "my-pack.zip"
    pack_zip.write_bytes(_make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"]))
    repo_dir = _write_fs_repo(tmp_path, {"dep-a.zip": _make_plugin_zip("dep-a", "1.0.0")})

    result = _invoke(CliRunner(mix_stderr=False), repo_dir, "install", str(pack_zip))

    assert result.exit_code != 0
    assert "dep-b" in result.output
    assert "unavailable" in result.output
    assert not is_plugin_installed("my-pack")
    assert not is_plugin_installed("dep-a")


def test_install_cli_reports_dependency_edges(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack.zip": _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"]),
            "dep-a.zip": _make_plugin_zip("dep-a", "1.0.0", deps=["dep-b"]),
            "dep-b.zip": _make_plugin_zip("dep-b", "1.0.0"),
        },
    )

    result = _invoke(CliRunner(mix_stderr=False), repo_dir, "install", "my-pack")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "Installed plugin: my-pack==1.0.0"
    assert "  Installed dependency: dep-b==1.0.0 (required by dep-a)" in lines
    assert "  Installed dependency: dep-a==1.0.0 (required by my-pack)" in lines


def test_install_cli_dependency_config_required_before_mutation(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack.zip": _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"]),
            "dep-a.zip": _make_plugin_zip("dep-a", "1.0.0", settings=[API_KEY]),
        },
    )
    runner = CliRunner(mix_stderr=False)

    result = _invoke(runner, repo_dir, "install", "my-pack")
    assert result.exit_code != 0
    assert "--dependency-config dep-a.api_key=<value>" in result.output
    assert not is_plugin_installed("my-pack")
    assert not is_plugin_installed("dep-a")

    result = _invoke(runner, repo_dir, "install", "my-pack", "--dependency-config", "dep-a.api_key=secret")
    assert result.exit_code == 0, result.output
    assert is_plugin_installed("my-pack")
    assert is_plugin_installed("dep-a")

    from hcli.lib.ida import get_ida_config

    assert get_ida_config().plugins["dep-a"].settings["api_key"] == "secret"


def test_install_cli_dependency_config_rejects_unknown_target(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(tmp_path, {"my-pack.zip": _make_plugin_zip("my-pack", "1.0.0")})

    result = _invoke(CliRunner(mix_stderr=False), repo_dir, "install", "my-pack", "--dependency-config", "nope.k=v")

    assert result.exit_code != 0
    assert "unknown plugin or component in configuration: 'nope'" in result.output
    assert not is_plugin_installed("my-pack")


def test_install_cli_optional_dependency_skipped_without_config(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack.zip": _make_plugin_zip("my-pack", "1.0.0", deps=[{"plugin": "opt", "required": False}]),
            "opt.zip": _make_plugin_zip("opt", "1.0.0", settings=[API_KEY]),
        },
    )

    result = _invoke(CliRunner(mix_stderr=False), repo_dir, "install", "my-pack")

    assert result.exit_code == 0, result.output
    assert is_plugin_installed("my-pack")
    assert not is_plugin_installed("opt")
    assert "Unavailable optional dependency: opt" in result.output
    assert "api_key" in result.output


def test_install_cli_upgrade_repairs_missing_dependency(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack.zip": _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"]),
            "dep-a.zip": _make_plugin_zip("dep-a", "1.0.0"),
        },
    )
    runner = CliRunner(mix_stderr=False)
    assert _invoke(runner, repo_dir, "install", "my-pack").exit_code == 0
    uninstall_plugin("dep-a")

    result = _invoke(runner, repo_dir, "install", "my-pack")
    assert result.exit_code != 0
    assert "already installed" in result.output

    result = _invoke(runner, repo_dir, "install", "--upgrade", "my-pack")
    assert result.exit_code == 0, result.output
    assert "Already installed plugin: my-pack==1.0.0" in result.output
    assert "Installed dependency: dep-a==1.0.0" in result.output
    assert is_plugin_installed("dep-a")


def test_install_pack_without_dependencies(virtual_ida_environment):
    pack_zip = _make_plugin_zip("my-pack", "1.0.0")
    install_plugin_archive(pack_zip, "my-pack", check_environment=False)

    _, metadata = _parse_metadata(pack_zip, "my-pack")
    assert metadata.plugin.dependencies == []


# ---------------------------------------------------------------------------
# Upgrade with dependencies
# ---------------------------------------------------------------------------


def test_upgrade_pack_installs_new_deps(virtual_ida_environment):
    pack_v1 = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    pack_v2 = _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    with _make_fs_repo({"dep-a.zip": dep_a_zip, "dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_v1, "my-pack", plugin_repo=repo, check_environment=False)
        result = upgrade_plugin_archive(pack_v2, "my-pack", plugin_repo=repo, check_environment=False)

    assert {r.name: r.outcome for r in result.committed_operations} == {"dep-b": "installed", "my-pack": "upgraded"}
    assert [r.name for r in result.present] == ["dep-a"]
    assert is_plugin_installed("dep-b")
    assert _get_installed_version("my-pack") == "2.0.0"


def test_upgrade_cli_reports_dropped_dependencies(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack-1.zip": _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"]),
            "my-pack-2.zip": _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a"]),
            "dep-a.zip": _make_plugin_zip("dep-a", "1.0.0"),
            "dep-b.zip": _make_plugin_zip("dep-b", "1.0.0"),
        },
    )
    runner = CliRunner(mix_stderr=False)
    assert _invoke(runner, repo_dir, "install", "my-pack==1.0.0").exit_code == 0

    result = _invoke(runner, repo_dir, "upgrade", "my-pack")

    assert result.exit_code == 0, result.output
    assert "Upgraded plugin: my-pack==2.0.0" in result.output
    assert "Present dependency: dep-a" in result.output
    assert "dep-b" in result.output
    assert "remain installed" in result.output
    assert is_plugin_installed("dep-b")
    assert _get_installed_version("my-pack") == "2.0.0"


def test_upgrade_cli_current_root_reports_up_to_date(virtual_ida_environment, tmp_path):
    repo_dir = _write_fs_repo(
        tmp_path,
        {
            "my-pack.zip": _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"]),
            "dep-a.zip": _make_plugin_zip("dep-a", "1.0.0"),
        },
    )
    runner = CliRunner(mix_stderr=False)
    assert _invoke(runner, repo_dir, "install", "my-pack").exit_code == 0
    uninstall_plugin("dep-a")

    result = _invoke(runner, repo_dir, "upgrade", "my-pack")

    assert result.exit_code == 0, result.output
    assert "Already up to date plugin: my-pack==1.0.0" in result.output
    assert "Installed dependency: dep-a==1.0.0" in result.output
    assert is_plugin_installed("dep-a")


def test_upgrade_pack_upgrades_unsatisfied_deps(virtual_ida_environment):
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")

    install_plugin_archive(dep_a_v1, "dep-a", check_environment=False)
    assert _get_installed_version("dep-a") == "1.0.0"

    pack_v1 = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    pack_v2 = _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a==2.0.0"])

    with _make_fs_repo({"dep-a.zip": dep_a_v2}) as repo:
        install_plugin_archive(pack_v1, "my-pack", plugin_repo=repo, check_environment=False)
        result = upgrade_plugin_archive(pack_v2, "my-pack", plugin_repo=repo, check_environment=False)

    dep = result.node_for_name("dep-a")
    assert dep is not None
    assert dep.outcome == "upgraded"
    assert _get_installed_version("dep-a") == "2.0.0"


def test_uninstall_standalone_dep_works_normally(virtual_ida_environment):
    dep_zip = _make_plugin_zip("dep-a", "1.0.0")
    install_plugin_archive(dep_zip, "dep-a", check_environment=False)
    assert is_plugin_installed("dep-a")

    uninstall_plugin("dep-a")
    assert not is_plugin_installed("dep-a")


# ---------------------------------------------------------------------------
# Uninstall CLI with dependency prompt
# ---------------------------------------------------------------------------


def _install_pack_with_deps(*deps: str) -> None:
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=list(deps))
    archives = {f"{dep}.zip": _make_plugin_zip(dep, "1.0.0") for dep in deps}
    with _make_fs_repo(archives) as repo:
        install_plugin_archive(pack_zip, "my-pack", plugin_repo=repo, check_environment=False)


def test_uninstall_pack_lists_deps(virtual_ida_environment):
    _install_pack_with_deps("dep-a", "dep-b")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "my-pack"])

    assert result.exit_code == 0, result.output
    assert "dep-a" in result.output
    assert "dep-b" in result.output
    assert "dependencies" in result.output.lower()
    assert is_plugin_installed("dep-a")
    assert is_plugin_installed("dep-b")


def test_uninstall_pack_removes_deps_with_yes_flag(virtual_ida_environment):
    _install_pack_with_deps("dep-a", "dep-b")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "--yes", "my-pack"])

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("my-pack")
    assert not is_plugin_installed("dep-a")
    assert not is_plugin_installed("dep-b")


def test_uninstall_pack_noninteractive_keeps_deps(virtual_ida_environment):
    _install_pack_with_deps("dep-a")

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "my-pack"])

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("my-pack")
    assert is_plugin_installed("dep-a")
    assert "no longer needed" in result.output.lower()


# ---------------------------------------------------------------------------
# Lint validation
# ---------------------------------------------------------------------------


def test_lint_valid_dependencies(capsys):
    data = _metadata_with_deps(["dep-a", "dep-b==1.0.0"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    count = _check_dependency_specs(descriptor, "test")
    assert count == 0


def test_lint_invalid_dependency_spec():
    data = {
        "IDAMetadataDescriptorVersion": 1,
        "plugin": {
            "name": "test-pack",
            "version": "1.0.0",
            "entryPoint": "noop.py",
            "urls": {"repository": HOST},
            "authors": [{"name": "Test", "email": "test@example.com"}],
            "dependencies": [],
        },
    }
    descriptor = IDAMetadataDescriptor.model_validate(data)
    descriptor.plugin.dependencies = [
        DependencySpec(plugin="dep-a"),
        DependencySpec.model_construct(plugin="!!!invalid"),
    ]
    count = _check_dependency_specs(descriptor, "test")
    assert count == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_metadata(zip_data: bytes, name: str) -> tuple[Path, IDAMetadataDescriptor]:
    from hcli.lib.ida.plugin import get_metadata_from_plugin_archive

    return get_metadata_from_plugin_archive(zip_data, name)
