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
from hcli.lib.ida.plugin.reference import DependencyEntry, parse_dependency_spec
from hcli.lib.ida.plugin.repo import BasePluginRepo, Plugin
from hcli.lib.ida.plugin.repo.fs import FileSystemPluginRepo

logger = logging.getLogger(__name__)

HOST = "https://github.com/test/test-pack"
HOST_B = "https://plugins.hex-rays.com/test-org/test-repo/test-pack"


def _make_plugin_metadata(
    name: str,
    version: str,
    deps: list[str | dict] | None = None,
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
    return {"IDAMetadataDescriptorVersion": 1, "plugin": plugin}


def _make_plugin_zip(
    name: str,
    version: str,
    deps: list[str | dict] | None = None,
    host: str = HOST,
) -> bytes:
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


def _dep_names(entries: list[DependencyEntry]) -> list[str]:
    return [e.reference.name for e in entries]


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
# DependencyEntry parsing
# ---------------------------------------------------------------------------


def test_dependency_entry_from_string():
    from hcli.lib.ida.plugin.reference import parse_dependency_entry

    entry = parse_dependency_entry("dep-a")
    assert entry.reference.name == "dep-a"
    assert entry.required is True


def test_dependency_entry_from_object_optional():
    from hcli.lib.ida.plugin.reference import parse_dependency_entry

    entry = parse_dependency_entry({"plugin": "dep-a", "required": False})
    assert entry.reference.name == "dep-a"
    assert entry.required is False


def test_dependency_entry_from_object_required_explicit():
    from hcli.lib.ida.plugin.reference import parse_dependency_entry

    entry = parse_dependency_entry({"plugin": "dep-a==1.0.0", "required": True})
    assert entry.reference.name == "dep-a"
    assert entry.reference.version_spec == "==1.0.0"
    assert entry.required is True


def test_dependency_entry_missing_plugin_field():
    from hcli.lib.ida.plugin.reference import parse_dependency_entry

    with pytest.raises(ValueError, match="'plugin' field"):
        parse_dependency_entry({"required": False})


def test_dependency_entry_invalid_type():
    from hcli.lib.ida.plugin.reference import parse_dependency_entry

    with pytest.raises(TypeError, match="string or object"):
        parse_dependency_entry(42)  # type: ignore[arg-type]


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


def _metadata_with_deps(deps: list[str | dict]) -> dict:
    data = json.loads(json.dumps(MINIMAL_METADATA))
    data["plugin"]["dependencies"] = deps
    return data


def test_metadata_with_dependencies():
    data = _metadata_with_deps(["dep-a", "dep-b==1.0.0"])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert _dep_names(descriptor.plugin.dependencies) == ["dep-a", "dep-b"]
    assert all(e.required for e in descriptor.plugin.dependencies)


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
    assert len(descriptor.plugin.dependencies) == 1
    assert descriptor.plugin.dependencies[0].reference.host == "https://github.com/org/repo"


def test_metadata_rejects_invalid_dependency_spec():
    data = _metadata_with_deps(["community/bad-prefix"])
    with pytest.raises(ValidationError):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_mixed_string_and_object_deps():
    data = _metadata_with_deps(
        [
            "always-needed",
            {"plugin": "nice-to-have", "required": False},
            {"plugin": "also-needed==2.0.0", "required": True},
        ]
    )
    descriptor = IDAMetadataDescriptor.model_validate(data)
    assert len(descriptor.plugin.dependencies) == 3
    assert descriptor.plugin.dependencies[0].required is True
    assert descriptor.plugin.dependencies[1].required is False
    assert descriptor.plugin.dependencies[2].required is True


def test_metadata_rejects_duplicate_deps():
    data = _metadata_with_deps(["dep-a", {"plugin": "dep-a", "required": False}])
    with pytest.raises(ValidationError, match="duplicate"):
        IDAMetadataDescriptor.model_validate(data)


def test_metadata_rejects_duplicate_deps_case_insensitive():
    data = _metadata_with_deps(["Dep-A", "dep-a"])
    with pytest.raises(ValidationError, match="duplicate"):
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


def test_metadata_serialization_mixed_roundtrip():
    data = _metadata_with_deps(
        [
            "always-needed",
            {"plugin": "nice-to-have", "required": False},
        ]
    )
    descriptor = IDAMetadataDescriptor.model_validate(data)
    serialized = descriptor.model_dump(mode="json", by_alias=True)
    assert serialized["plugin"]["dependencies"] == [
        "always-needed",
        {"plugin": "nice-to-have", "required": False},
    ]


def test_metadata_serialization_required_true_as_string():
    data = _metadata_with_deps([{"plugin": "dep-a", "required": True}])
    descriptor = IDAMetadataDescriptor.model_validate(data)
    serialized = descriptor.model_dump(mode="json", by_alias=True)
    assert serialized["plugin"]["dependencies"] == ["dep-a"]


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


def test_required_dep_failure_stops_siblings(virtual_ida_environment):
    ctx = make_test_install_context()

    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-missing", "dep-b"])
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")
    with _make_fs_repo({"dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.failed_required is not None
    assert result.failed_required[0] == "dep-missing"
    assert not result.installed
    assert not is_plugin_installed("dep-b")
    assert is_plugin_installed("my-pack")


def test_optional_dep_failure_continues_siblings(virtual_ida_environment):
    ctx = make_test_install_context()
    dep_b_zip = _make_plugin_zip("dep-b", "1.0.0")

    pack_zip = _make_plugin_zip(
        "my-pack",
        "1.0.0",
        deps=[{"plugin": "dep-missing", "required": False}, "dep-b"],
    )
    with _make_fs_repo({"dep-b.zip": dep_b_zip}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.failed_required is None
    assert result.installed == ["dep-b"]
    assert len(result.skipped_optional) == 1
    assert result.skipped_optional[0][0] == "dep-missing"
    assert is_plugin_installed("dep-b")
    assert is_plugin_installed("my-pack")


def test_optional_dep_failure_logged_at_info(virtual_ida_environment, caplog):
    ctx = make_test_install_context()

    pack_zip = _make_plugin_zip(
        "my-pack",
        "1.0.0",
        deps=[{"plugin": "dep-missing", "required": False}],
    )
    with _make_fs_repo({}) as repo:
        install_plugin_archive(pack_zip, "my-pack", ctx)

        _, metadata = _parse_metadata(pack_zip, "my-pack")
        with caplog.at_level(logging.INFO, logger="hcli.lib.ida.plugin.dependencies"):
            install_dependencies(
                metadata=metadata,
                plugin_repo=repo,
                ctx=ctx,
            )

    assert any("Skipping optional dependency dep-missing" in r.message for r in caplog.records)


def test_install_pack_local_directory_warns_about_deps(virtual_ida_environment):
    from hcli.lib.ida.plugin.install import _install_loose_dependencies
    from hcli.lib.ida.plugin.result import InstallStatus

    ctx = make_test_install_context(check_environment=False)
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-a", "dep-b"])
    install_plugin_archive(pack_zip, "my-pack", ctx)
    _, metadata = _parse_metadata(pack_zip, "my-pack")

    results, _failed_required = _install_loose_dependencies(metadata, None, ctx)

    assert len(results) == 2
    assert all(r.status == InstallStatus.FAILED for r in results)
    assert all("cannot auto-install" in (r.reason or "") for r in results)


def test_install_pack_without_dependencies(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("my-pack", "1.0.0")
    install_plugin_archive(pack_zip, "my-pack", ctx)

    _, metadata = _parse_metadata(pack_zip, "my-pack")
    assert metadata.plugin.dependencies == []


# ---------------------------------------------------------------------------
# Required dependency cascading rollback
# ---------------------------------------------------------------------------


def test_required_dep_cascading_rollback(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("pack-a", "1.0.0", deps=["pack-b"])
    pack_b_zip = _make_plugin_zip("pack-b", "1.0.0", deps=["dep-missing"])

    with _make_fs_repo({"pack-b.zip": pack_b_zip}) as repo:
        install_plugin_archive(pack_zip, "pack-a", ctx)

        _, metadata = _parse_metadata(pack_zip, "pack-a")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.failed_required is not None
    assert not is_plugin_installed("pack-b")


def test_transitive_optional_absorbs_failed_required(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip(
        "pack-a",
        "1.0.0",
        deps=[{"plugin": "pack-b", "required": False}],
    )
    pack_b_zip = _make_plugin_zip("pack-b", "1.0.0", deps=["dep-missing"])

    with _make_fs_repo({"pack-b.zip": pack_b_zip}) as repo:
        install_plugin_archive(pack_zip, "pack-a", ctx)

        _, metadata = _parse_metadata(pack_zip, "pack-a")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.failed_required is None
    assert len(result.skipped_optional) == 1
    assert result.skipped_optional[0][0] == "pack-b"
    assert is_plugin_installed("pack-a")
    assert not is_plugin_installed("pack-b")


def test_transitive_required_with_optional_child(virtual_ida_environment):
    ctx = make_test_install_context()
    pack_zip = _make_plugin_zip("pack-a", "1.0.0", deps=["pack-b"])
    pack_b_zip = _make_plugin_zip(
        "pack-b",
        "1.0.0",
        deps=[{"plugin": "dep-missing", "required": False}],
    )

    with _make_fs_repo({"pack-b.zip": pack_b_zip}) as repo:
        install_plugin_archive(pack_zip, "pack-a", ctx)

        _, metadata = _parse_metadata(pack_zip, "pack-a")
        result = install_dependencies(
            metadata=metadata,
            plugin_repo=repo,
            ctx=ctx,
        )

    assert result.failed_required is None
    assert "pack-b" in result.installed
    assert is_plugin_installed("pack-b")
    assert is_plugin_installed("pack-a")


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
    from hcli.lib.ida.plugin.install import apply_upgrade
    from hcli.lib.ida.plugin.result import InstallStatus

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

        _, meta_v2 = _parse_metadata(pack_v2, "my-pack")

        result = apply_upgrade(
            zip_data=pack_v2,
            plugin_name="my-pack",
            metadata=meta_v2,
            ctx=ctx,
            plugin_repo=repo,
            old_deps=list(meta_v1.plugin.dependencies),
        )

    assert result.status == InstallStatus.SUCCESS
    dropped = [d for d in result.dependencies if d.plugin == "dep-b"]
    assert len(dropped) == 1
    assert "dropped" in (dropped[0].reason or "")
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


def test_lint_valid_mixed_dependencies(capsys):
    data = _metadata_with_deps(
        [
            "dep-a",
            {"plugin": "dep-b==1.0.0", "required": False},
        ]
    )
    descriptor = IDAMetadataDescriptor.model_validate(data)
    count = _check_dependency_specs(descriptor, "test")
    assert count == 0


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
# apply_install with required dep failure
# ---------------------------------------------------------------------------


def test_apply_install_rolls_back_on_required_dep_failure(virtual_ida_environment):
    from hcli.lib.ida.plugin.install import apply_install
    from hcli.lib.ida.plugin.result import InstallStatus

    ctx = make_test_install_context(check_environment=False)
    pack_zip = _make_plugin_zip("my-pack", "1.0.0", deps=["dep-missing"])
    _, metadata = _parse_metadata(pack_zip, "my-pack")

    with _make_fs_repo({}) as repo:
        result = apply_install(
            source=pack_zip,
            plugin_name="my-pack",
            metadata=metadata,
            ctx=ctx,
            plugin_repo=repo,
        )

    assert result.status == InstallStatus.ROLLED_BACK
    assert not is_plugin_installed("my-pack")


def test_apply_install_succeeds_with_optional_dep_failure(virtual_ida_environment):
    from hcli.lib.ida.plugin.install import apply_install
    from hcli.lib.ida.plugin.result import InstallStatus

    ctx = make_test_install_context(check_environment=False)
    pack_zip = _make_plugin_zip(
        "my-pack",
        "1.0.0",
        deps=[{"plugin": "dep-missing", "required": False}],
    )
    _, metadata = _parse_metadata(pack_zip, "my-pack")

    with _make_fs_repo({}) as repo:
        result = apply_install(
            source=pack_zip,
            plugin_name="my-pack",
            metadata=metadata,
            ctx=ctx,
            plugin_repo=repo,
        )

    assert result.status == InstallStatus.SUCCESS
    assert is_plugin_installed("my-pack")
    skipped = [d for d in result.dependencies if d.status == InstallStatus.SKIPPED_OPTIONAL]
    assert len(skipped) == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_metadata(zip_data: bytes, name: str) -> tuple[Path, IDAMetadataDescriptor]:
    from hcli.lib.ida.plugin import get_metadata_from_plugin_archive

    return get_metadata_from_plugin_archive(zip_data, name)
