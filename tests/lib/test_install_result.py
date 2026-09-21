"""Tests for InstallResult and InstallStatus types."""

from hcli.lib.ida.plugin.result import InstallResult, InstallStatus


def test_install_status_values():
    assert InstallStatus.SUCCESS.value == "success"
    assert InstallStatus.ALREADY_INSTALLED.value == "already_installed"
    assert InstallStatus.FAILED.value == "failed"
    assert InstallStatus.ROLLED_BACK.value == "rolled_back"
    assert InstallStatus.SKIPPED_OPTIONAL.value == "skipped_optional"


def test_install_result_construction():
    result = InstallResult(
        plugin="my-plugin",
        version="1.0.0",
        status=InstallStatus.SUCCESS,
    )
    assert result.plugin == "my-plugin"
    assert result.version == "1.0.0"
    assert result.status == InstallStatus.SUCCESS
    assert result.reason is None
    assert result.dependencies == []


def test_install_result_with_reason():
    result = InstallResult(
        plugin="broken",
        version="2.0.0",
        status=InstallStatus.FAILED,
        reason="pip not available",
    )
    assert result.reason == "pip not available"


def test_install_result_frozen():
    result = InstallResult(
        plugin="my-plugin",
        version="1.0.0",
        status=InstallStatus.SUCCESS,
    )
    try:
        result.plugin = "other"  # type: ignore[misc]
        assert False, "should have raised"
    except AttributeError:
        pass


def test_install_result_nested_dependencies():
    child_a = InstallResult(
        plugin="dep-a",
        version="0.1.0",
        status=InstallStatus.SUCCESS,
    )
    child_b = InstallResult(
        plugin="dep-b",
        version="0.2.0",
        status=InstallStatus.ALREADY_INSTALLED,
    )
    parent = InstallResult(
        plugin="suite",
        version="3.0.0",
        status=InstallStatus.SUCCESS,
        dependencies=[child_a, child_b],
    )
    assert len(parent.dependencies) == 2
    assert parent.dependencies[0].plugin == "dep-a"
    assert parent.dependencies[1].status == InstallStatus.ALREADY_INSTALLED


def test_install_result_deeply_nested():
    leaf = InstallResult(plugin="leaf", version="0.0.1", status=InstallStatus.FAILED, reason="network error")
    mid = InstallResult(plugin="mid", version="1.0.0", status=InstallStatus.SUCCESS, dependencies=[leaf])
    root = InstallResult(plugin="root", version="2.0.0", status=InstallStatus.SUCCESS, dependencies=[mid])

    assert root.dependencies[0].dependencies[0].plugin == "leaf"
    assert root.dependencies[0].dependencies[0].reason == "network error"
