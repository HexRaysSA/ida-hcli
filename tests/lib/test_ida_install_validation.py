"""Tests for IDA install command file validation."""

from pathlib import Path

import pytest

from hcli.lib.ida import _install_ida_mac, _install_ida_unix, _install_ida_windows


@pytest.mark.unit
def test_install_unix_nonexistent_file(tmp_path):
    """Test that _install_ida_unix raises FileNotFoundError for nonexistent installer."""
    nonexistent_installer = tmp_path / "nonexistent-installer.run"
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    with pytest.raises(FileNotFoundError) as exc_info:
        _install_ida_unix(nonexistent_installer, install_dir)

    assert "Installer file not found" in str(exc_info.value)
    assert str(nonexistent_installer) in str(exc_info.value)


@pytest.mark.unit
def test_install_mac_nonexistent_file(tmp_path):
    """Test that _install_ida_mac raises FileNotFoundError for nonexistent installer."""
    nonexistent_installer = tmp_path / "nonexistent-installer.app"
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    with pytest.raises(FileNotFoundError) as exc_info:
        _install_ida_mac(nonexistent_installer, install_dir)

    assert "Installer file not found" in str(exc_info.value)
    assert str(nonexistent_installer) in str(exc_info.value)


@pytest.mark.unit
def test_install_windows_nonexistent_file(tmp_path):
    """Test that _install_ida_windows raises FileNotFoundError for nonexistent installer."""
    nonexistent_installer = tmp_path / "nonexistent-installer.exe"
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    with pytest.raises(FileNotFoundError) as exc_info:
        _install_ida_windows(nonexistent_installer, install_dir)

    assert "Installer file not found" in str(exc_info.value)
    assert str(nonexistent_installer) in str(exc_info.value)


@pytest.mark.unit
def test_install_unix_relative_path_not_found(tmp_path, monkeypatch):
    """Test that _install_ida_unix handles relative path correctly when file doesn't exist."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    # Use a relative path that doesn't exist
    nonexistent_installer = Path("ida-pro_92_x64linux.run")
    install_dir = tmp_path / "install"
    install_dir.mkdir()

    with pytest.raises(FileNotFoundError) as exc_info:
        _install_ida_unix(nonexistent_installer, install_dir)

    assert "Installer file not found" in str(exc_info.value)
