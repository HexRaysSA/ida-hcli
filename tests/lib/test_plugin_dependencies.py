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
from fixtures import make_test_install_context
from pydantic import ValidationError

from hcli.commands.plugin import plugin as plugin_group
from hcli.commands.plugin.lint import _check_dependency_specs
from hcli.lib.ida.plugin import IDAMetadataDescriptor
from hcli.lib.ida.plugin.dependencies import install_dependencies
from hcli.lib.ida.plugin.install import (
    get_installed_plugin_records,
    install_plugin_archive,
    is_plugin_installed,
    uninstall_plugin,
    upgrade_plugin_archive,
)
from hcli.lib.ida.plugin.reference import parse_dependency_spec
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo

logger = logging.getLogger(__name__)

HOST = "https://github.com/test/test-pack"
HOST_B = "https://plugins.hex-rays.com/test-org/test-repo/test-pack"


def _make_plugin_metadata(name: str, version: str, deps: list[str] | None = None, host: str = HOST) -> dict:
    plugin: dict = {
        "name": name,
        "version": version,
        "entryPoint": f"{name}.py",
        "urls": {"repository": host},
        "authors": [{"name": "Test", "email": "test@example.com"}],
    }
    if deps is not None:
        plugin["dependencies"] = deps
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_plugin_zip(name: str, version: str, deps: list[str] | None = None, host: str = HOST) -> bytes:
    buf = io.BytesIO()
    metadata = _make_plugin_metadata(name, version, deps, host=host)
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
    assert descriptor.plugin.dependencies == ["dep-a", "dep-b==1.0.0"]


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
    assert descriptor.plugin.dependencies == ["dep-a@https://github.com/org/repo"]


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
# install_dependencies (library-level integration tests)
# ---------------------------------------------------------------------------


def test_install_pack_installs_dependencies(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "2.0.0")

    with _make_fs_repo({"dep-a.zip": dep_a_zip, "dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert set(result.installed) == {"dep-a", "dep-b"}
    assert not result.failed
    assert is_plugin_installed("dep-a")
    assert is_plugin_installed("dep-b")


def test_install_pack_skips_already_installed_dep(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    install_plugin_archive(dep_a_zip, "dep-a", ctx)

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    with _make_fs_repo({"dep-a.zip": dep_a_zip}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.skipped == ["dep-a"]
    assert not result.installed


def test_install_pack_upgrades_outdated_pinned_dep(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")
    install_plugin_archive(dep_a_v1, "dep-a", ctx)
    assert _get_installed_version("dep-a") == "1.0.0"

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a==2.0.0"])
    with _make_fs_repo({"dep-a-v2.zip": dep_a_v2}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.upgraded == ["dep-a"]
    assert _get_installed_version("dep-a") == "2.0.0"


def test_install_pack_no_downgrade_pinned_dep(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")
    install_plugin_archive(dep_a_v2, "dep-a", ctx)

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a==1.0.0"])
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    with _make_fs_repo({"dep-a-v1.zip": dep_a_v1}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.skipped == ["dep-a"]
    assert _get_installed_version("dep-a") == "2.0.0"


def test_install_pack_partial_dep_failure(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-missing"])
    with _make_fs_repo({"dep-a.zip": dep_a_zip}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.installed == ["dep-a"]
    assert len(result.failed) == 1
    assert result.failed[0][0] == "dep-missing"
    assert is_plugin_installed("dep-a")
    assert is_plugin_installed("my-pack")


def test_install_pack_local_directory_warns_about_deps(virtual_ida_environment, capsys):
    from hcli.commands.plugin.install import _handle_install_dependencies

    ctx = make_test_install_context(check_environment=False)
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    install_plugin_archive(pack_zip, "my-pack", ctx)
    _, metadata = _parse_metadata(pack_zip, "my-pack")

    _handle_install_dependencies(
        metadata=metadata,
        plugin_repo=None,
        install_ctx=ctx,
    )

    captured = capsys.readouterr()
    assert "cannot be auto-installed" in captured.out
    assert "dep-a" in captured.out
    assert "dep-b" in captured.out


def test_install_pack_without_dependencies(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0")
    install_plugin_archive(pack_zip, "my-pack", ctx)

    _, metadata = _parse_metadata(pack_zip, "my-pack")
    assert metadata.plugin.dependencies == []


# ---------------------------------------------------------------------------
# Upgrade with dependencies
# ---------------------------------------------------------------------------


def test_upgrade_pack_installs_new_deps(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_v1 = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    pack_v2 = _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    with _make_fs_repo({"dep-a.zip": dep_a_zip, "dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_v1, "my-pack", ctx)
        _, meta_v1 = _parse_metadata(pack_v1, "my-pack")
        install_dependencies(
            metadata=meta_v1,
            plugin_repo=repo,
            ctx=ctx,
        )

        upgrade_plugin_archive(pack_v2, "my-pack", ctx)
        _, meta_v2 = _parse_metadata(pack_v2, "my-pack")
        result = install_dependencies(
            metadata=meta_v2,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert "dep-b" in result.installed
    assert "dep-a" in result.skipped
    assert is_plugin_installed("dep-b")


def test_upgrade_pack_reports_dropped_deps(virtual_ida_environment):
    from hcli.commands.plugin.upgrade import _handle_upgrade_dependencies

    ctx = make_test_install_context(check_environment=False)
    pack_v1 = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    pack_v2 = _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    with _make_fs_repo({"dep-a.zip": dep_a_zip, "dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_v1, "my-pack", ctx)
        _, meta_v1 = _parse_metadata(pack_v1, "my-pack")
        install_dependencies(
            metadata=meta_v1,
            plugin_repo=repo,
            ctx=ctx,
        )

        upgrade_plugin_archive(pack_v2, "my-pack", ctx)
        _, meta_v2 = _parse_metadata(pack_v2, "my-pack")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _handle_upgrade_dependencies(
                old_deps=list(meta_v1.plugin.dependencies),
                new_metadata=meta_v2,
                plugin_repo=repo,
                install_ctx=ctx,
            )
        output = buf.getvalue()

    assert "dep-b" in output
    assert "removed" in output.lower() or "remain installed" in output
    assert is_plugin_installed("dep-b")


def test_upgrade_pack_upgrades_unsatisfied_deps(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_a_v1 = _make_plugin_zip("dep-a", "1.0.0")
    dep_a_v2 = _make_plugin_zip("dep-a", "2.0.0")

    install_plugin_archive(dep_a_v1, "dep-a", ctx)
    assert _get_installed_version("dep-a") == "1.0.0"

    pack_v1 = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    pack_v2 = _make_plugin_zip("my-pack", "2.0.0", deps=["dep-a==2.0.0"])

    with _make_fs_repo({"dep-a.zip": dep_a_v2}) as repo:
        install_plugin_archive(pack_v1, "my-pack", ctx)

        upgrade_plugin_archive(pack_v2, "my-pack", ctx)
        _, meta_v2 = _parse_metadata(pack_v2, "my-pack")
        result = install_dependencies(
            metadata=meta_v2,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.upgraded == ["dep-a"]
    assert _get_installed_version("dep-a") == "2.0.0"


def test_uninstall_standalone_dep_works_normally(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_zip = _make_plugin_zip("dep-a", "1.0.0")
    install_plugin_archive(dep_zip, "dep-a", ctx)
    assert is_plugin_installed("dep-a")

    uninstall_plugin("dep-a")
    assert not is_plugin_installed("dep-a")


# ---------------------------------------------------------------------------
# Uninstall CLI with dependency prompt
# ---------------------------------------------------------------------------


def test_uninstall_pack_lists_deps(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    install_plugin_archive(pack_zip, "my-pack", ctx)
    install_plugin_archive(dep_a_zip, "dep-a", ctx)
    install_plugin_archive(dep_b_zip, "dep-b", ctx)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "my-pack"])

    assert result.exit_code == 0, result.output
    assert "dep-a" in result.output
    assert "dep-b" in result.output
    assert "dependencies" in result.output.lower()
    assert is_plugin_installed("dep-a")
    assert is_plugin_installed("dep-b")


def test_uninstall_pack_removes_deps_with_yes_flag(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    install_plugin_archive(pack_zip, "my-pack", ctx)
    install_plugin_archive(dep_a_zip, "dep-a", ctx)
    install_plugin_archive(dep_b_zip, "dep-b", ctx)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "--yes", "my-pack"])

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("my-pack")
    assert not is_plugin_installed("dep-a")
    assert not is_plugin_installed("dep-b")


def test_uninstall_pack_noninteractive_keeps_deps(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"])
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0")

    install_plugin_archive(pack_zip, "my-pack", ctx)
    install_plugin_archive(dep_a_zip, "dep-a", ctx)

    runner = CliRunner(mix_stderr=False)
    result = runner.invoke(plugin_group, ["uninstall", "my-pack"])

    assert result.exit_code == 0, result.output
    assert not is_plugin_installed("my-pack")
    assert is_plugin_installed("dep-a")
    assert "no longer needed" in result.output.lower()


# ---------------------------------------------------------------------------
# Cross-repo dependency resolution
# ---------------------------------------------------------------------------


class _CombinedRepo(BasePluginRepo):
    """Merges plugins from multiple FileSystemPluginRepo instances for testing."""

    def __init__(self, repos: list[FileSystemPluginRepo]):
        super().__init__()
        self._repos = repos

    def get_plugins(self) -> list[Plugin]:
        plugins: list[Plugin] = []
        for repo in self._repos:
            plugins.extend(repo.get_plugins())
        return plugins


@contextlib.contextmanager
def _make_combined_repo(repo_specs: list[dict[str, bytes]]) -> Iterator[_CombinedRepo]:
    with contextlib.ExitStack() as stack:
        repos = []
        for archives in repo_specs:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            repo_dir = Path(tmp)
            for filename, data in archives.items():
                (repo_dir / filename).write_bytes(data)
            repos.append(FileSystemPluginRepo(repo_dir))
        yield _CombinedRepo(repos)


def test_cross_repo_dep_resolves_from_aggregate(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"], host=HOST_B)
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0", host=HOST)

    with _make_combined_repo(
        [
            {"dep-a.zip": dep_a_zip},
        ]
    ) as combined:
        install_plugin_archive(pack_zip, "my-pack", ctx)
        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(metadata=metadata, plugin_repo=combined, ctx=ctx)

    assert result.installed == ["dep-a"]
    assert not result.failed


def test_cross_repo_dep_with_host_resolves_correctly(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=[f"dep-a@{HOST}"], host=HOST_B)
    dep_a_community = _make_plugin_zip("dep-a", "1.0.0", host=HOST)
    dep_a_private = _make_plugin_zip("dep-a", "2.0.0", host=HOST_B)

    with _make_combined_repo(
        [
            {"dep-a.zip": dep_a_community},
            {"dep-a.zip": dep_a_private},
        ]
    ) as combined:
        install_plugin_archive(pack_zip, "my-pack", ctx)
        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(metadata=metadata, plugin_repo=combined, ctx=ctx)

    assert result.installed == ["dep-a"]
    assert _get_installed_version("dep-a") == "1.0.0"


def test_cross_repo_bare_name_ambiguous_raises(virtual_ida_environment):

    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"], host=HOST_B)
    dep_a_community = _make_plugin_zip("dep-a", "1.0.0", host=HOST)
    dep_a_private = _make_plugin_zip("dep-a", "2.0.0", host=HOST_B)

    with _make_combined_repo(
        [
            {"dep-a.zip": dep_a_community},
            {"dep-a.zip": dep_a_private},
        ]
    ) as combined:
        install_plugin_archive(pack_zip, "my-pack", ctx)
        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(metadata=metadata, plugin_repo=combined, ctx=ctx)

    assert len(result.failed) == 1
    assert result.failed[0][0] == "dep-a"


def test_cross_repo_unique_bare_name_resolves(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a"], host=HOST_B)
    dep_a_zip = _make_plugin_zip("dep-a", "1.0.0", host=HOST)
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0", host=HOST_B)

    with _make_combined_repo(
        [
            {"dep-a.zip": dep_a_zip},
            {"dep-b.zip": dep_b_zip},
        ]
    ) as combined:
        install_plugin_archive(pack_zip, "my-pack", ctx)
        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(metadata=metadata, plugin_repo=combined, ctx=ctx)

    assert result.installed == ["dep-a"]
    assert not result.failed


def test_cross_repo_transitive_dep_resolves(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["mid-dep"], host=HOST_B)
    mid_dep_zip = _make_plugin_zip("mid-dep", "1.0.0", deps=["leaf-dep"], host=HOST)
    leaf_dep_zip = _make_plugin_zip("leaf-dep", "1.0.0", host=HOST_B)

    with _make_combined_repo(
        [
            {"mid-dep.zip": mid_dep_zip},
            {"leaf-dep.zip": leaf_dep_zip},
        ]
    ) as combined:
        install_plugin_archive(pack_zip, "my-pack", ctx)
        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(metadata=metadata, plugin_repo=combined, ctx=ctx)

    assert "mid-dep" in result.installed
    assert "leaf-dep" in result.installed
    assert not result.failed
    assert is_plugin_installed("mid-dep")
    assert is_plugin_installed("leaf-dep")


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
    descriptor.plugin.dependencies = ["dep-a", "!!!invalid"]
    count = _check_dependency_specs(descriptor, "test")
    assert count == 1


def test_lint_bare_dep_warns_for_non_community_plugin(capsys):
    data = _make_plugin_metadata("my-pack", "1.0.0", deps=["dep-a"], host=HOST_B)
    descriptor = IDAMetadataDescriptor.model_validate(data)
    count = _check_dependency_specs(descriptor, "test")
    assert count == 1
    captured = capsys.readouterr()
    assert "name@host" in captured.out


def test_lint_qualified_dep_no_warning_for_non_community_plugin(capsys):
    data = _make_plugin_metadata("my-pack", "1.0.0", deps=[f"dep-a@{HOST}"], host=HOST_B)
    descriptor = IDAMetadataDescriptor.model_validate(data)
    count = _check_dependency_specs(descriptor, "test")
    assert count == 0


def test_lint_bare_dep_no_warning_for_community_plugin(capsys):
    data = _make_plugin_metadata("my-pack", "1.0.0", deps=["dep-a"], host=HOST)
    descriptor = IDAMetadataDescriptor.model_validate(data)
    count = _check_dependency_specs(descriptor, "test")
    assert count == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_metadata(zip_data: bytes, name: str) -> tuple[Path, IDAMetadataDescriptor]:
    from hcli.lib.ida.plugin import get_metadata_from_plugin_archive

    return get_metadata_from_plugin_archive(zip_data, name)
