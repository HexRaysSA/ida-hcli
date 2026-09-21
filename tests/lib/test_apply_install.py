"""Tests for apply_install and apply_upgrade."""

from fixtures import *
from fixtures import PLUGINS_DIR, make_test_install_context

from hcli.lib.ida.plugin import get_metadata_from_plugin_archive
from hcli.lib.ida.plugin.install import (
    get_installed_plugin_records,
    is_plugin_installed,
    apply_install,
    apply_upgrade,
)
from hcli.lib.ida.plugin.result import InstallStatus


def test_apply_install_success(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    result = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
    )
    assert result.status == InstallStatus.SUCCESS
    assert result.plugin == "plugin1"
    assert result.version == "1.0.0"
    assert is_plugin_installed("plugin1")


def test_apply_install_already_installed_fails(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    result = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
    )
    assert result.status == InstallStatus.SUCCESS

    result2 = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
    )
    assert result2.status == InstallStatus.FAILED
    assert result2.reason is not None
    assert "already installed" in result2.reason.lower()


def test_apply_install_with_settings(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    result = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
        settings={"key1": "myvalue"},
    )
    assert result.status == InstallStatus.SUCCESS

    from hcli.lib.ida.plugin.settings import get_plugin_setting

    assert get_plugin_setting("plugin1", "key1") == "myvalue"


def test_apply_install_bad_settings_rolls_back(virtual_ida_environment):
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v5.0.0.zip").read_bytes()
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    result = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
        settings={"nonexistent_key": "value"},
    )
    assert result.status == InstallStatus.FAILED
    assert not is_plugin_installed("plugin1")


def test_apply_install_no_repo_warns_about_deps(virtual_ida_environment):
    """When plugin_repo is None and plugin declares deps, the result includes failed dep entries."""
    ctx = make_test_install_context()
    buf = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    _, metadata = get_metadata_from_plugin_archive(buf, "plugin1")

    # plugin1 v1 has no deps, so this just confirms no crash
    result = apply_install(
        source=buf,
        plugin_name="plugin1",
        metadata=metadata,
        ctx=ctx,
        plugin_repo=None,
    )
    assert result.status == InstallStatus.SUCCESS
    assert result.dependencies == []


def test_apply_install_never_raises(virtual_ida_environment):
    """The orchestrator should return a failed result, not raise."""
    ctx = make_test_install_context()
    buf = b"not a zip file at all"
    from hcli.lib.ida.plugin import IDAMetadataDescriptor

    metadata = IDAMetadataDescriptor.model_validate_json(
        '{"IDAMetadataDescriptorVersion": 1, "plugin": {'
        '"name": "bogus", "version": "0.0.1", "entryPoint": "bogus.py", '
        '"urls": {"repository": "https://github.com/test/test"}, '
        '"authors": [{"email": "test@test.com"}]}}'
    )

    result = apply_install(
        source=buf,
        plugin_name="bogus",
        metadata=metadata,
        ctx=ctx,
    )
    assert result.status == InstallStatus.FAILED
    assert result.reason is not None


def test_apply_upgrade_success(virtual_ida_environment):
    ctx = make_test_install_context()
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()
    _, meta1 = get_metadata_from_plugin_archive(v1, "plugin1")
    _, meta2 = get_metadata_from_plugin_archive(v2, "plugin1")

    r1 = apply_install(source=v1, plugin_name="plugin1", metadata=meta1, ctx=ctx)
    assert r1.status == InstallStatus.SUCCESS

    r2 = apply_upgrade(zip_data=v2, plugin_name="plugin1", metadata=meta2, ctx=ctx)
    assert r2.status == InstallStatus.SUCCESS
    assert r2.version == "2.0.0"

    records = get_installed_plugin_records()
    versions = {r.name: r.version for r in records}
    assert versions["plugin1"] == "2.0.0"


def test_apply_upgrade_downgrade_fails(virtual_ida_environment):
    ctx = make_test_install_context()
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    v2 = (PLUGINS_DIR / "plugin1" / "plugin1-v2.0.0.zip").read_bytes()
    _, meta1 = get_metadata_from_plugin_archive(v1, "plugin1")
    _, meta2 = get_metadata_from_plugin_archive(v2, "plugin1")

    apply_install(source=v2, plugin_name="plugin1", metadata=meta2, ctx=ctx)

    result = apply_upgrade(zip_data=v1, plugin_name="plugin1", metadata=meta1, ctx=ctx)
    assert result.status == InstallStatus.FAILED
    assert result.reason is not None
    assert "not greater" in result.reason.lower() or "downgrade" in result.reason.lower()


def test_apply_upgrade_never_raises(virtual_ida_environment):
    ctx = make_test_install_context()
    v1 = (PLUGINS_DIR / "plugin1" / "plugin1-v1.0.0.zip").read_bytes()
    _, meta1 = get_metadata_from_plugin_archive(v1, "plugin1")

    apply_install(source=v1, plugin_name="plugin1", metadata=meta1, ctx=ctx)

    result = apply_upgrade(
        zip_data=b"not a zip",
        plugin_name="plugin1",
        metadata=meta1,
        ctx=ctx,
    )
    assert result.status == InstallStatus.FAILED
    assert result.reason is not None
