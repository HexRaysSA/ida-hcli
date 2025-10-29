"""
Tests for the accept-eula command.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from hcli.commands.ida.accept_eula import accept_eula_cmd
from hcli.lib.ida import MissingCurrentInstallationDirectory


@pytest.fixture
def cli_runner():
    """Provide a CLI runner for testing."""
    return CliRunner()


def test_accept_eula_cmd_no_default_no_path(cli_runner):
    """Test accept-eula command with no default installation and no path provided."""
    with patch("hcli.commands.ida.accept_eula.find_current_ida_install_directory") as mock_find:
        mock_find.side_effect = MissingCurrentInstallationDirectory("directory doesn't exist")
        with patch("hcli.commands.ida.accept_eula.find_standard_installations") as mock_std:
            mock_std.return_value = []
            result = cli_runner.invoke(accept_eula_cmd, [])
            assert result.exit_code == 0
            assert "No default IDA installation set" in result.output
            assert "Please provide an installation path" in result.output


def test_accept_eula_cmd_with_nonexistent_path(cli_runner):
    """Test accept-eula command with a nonexistent path."""
    result = cli_runner.invoke(accept_eula_cmd, ["/nonexistent/path"])
    assert result.exit_code == 0
    assert "Path does not exist" in result.output


def test_accept_eula_cmd_with_invalid_ida_dir(cli_runner, tmp_path):
    """Test accept-eula command with an invalid IDA directory."""
    # Create a directory but without IDA binary
    test_dir = tmp_path / "not-ida"
    test_dir.mkdir()

    result = cli_runner.invoke(accept_eula_cmd, [str(test_dir)])
    assert result.exit_code == 0
    assert "Not a valid IDA installation directory" in result.output


def test_accept_eula_cmd_success(cli_runner, tmp_path):
    """Test successful EULA acceptance."""
    test_dir = tmp_path / "ida-pro"
    test_dir.mkdir()

    with patch("hcli.commands.ida.accept_eula.is_ida_dir") as mock_is_ida:
        mock_is_ida.return_value = True
        with patch("hcli.commands.ida.accept_eula.accept_eula") as mock_accept:
            with patch("hcli.commands.ida.accept_eula.get_ida_path") as mock_get_path:
                mock_get_path.return_value = test_dir
                result = cli_runner.invoke(accept_eula_cmd, [str(test_dir)])
                assert result.exit_code == 0
                assert "Accepting EULA" in result.output
                assert "EULA accepted successfully" in result.output
                mock_accept.assert_called_once_with(test_dir)


def test_accept_eula_cmd_runtime_error(cli_runner, tmp_path):
    """Test EULA acceptance when idalib is not available."""
    test_dir = tmp_path / "ida-pro"
    test_dir.mkdir()

    with patch("hcli.commands.ida.accept_eula.is_ida_dir") as mock_is_ida:
        mock_is_ida.return_value = True
        with patch("hcli.commands.ida.accept_eula.accept_eula") as mock_accept:
            mock_accept.side_effect = RuntimeError("idalib not available")
            with patch("hcli.commands.ida.accept_eula.get_ida_path") as mock_get_path:
                mock_get_path.return_value = test_dir
                result = cli_runner.invoke(accept_eula_cmd, [str(test_dir)])
                assert result.exit_code == 0
                assert "Failed to accept EULA" in result.output
                assert "idalib is not available" in result.output


def test_accept_eula_cmd_with_default_installation(cli_runner, tmp_path):
    """Test accept-eula command using default installation."""
    test_dir = tmp_path / "ida-pro"
    test_dir.mkdir()

    with patch("hcli.commands.ida.accept_eula.find_current_ida_install_directory") as mock_find:
        mock_find.return_value = test_dir
        with patch("hcli.commands.ida.accept_eula.accept_eula") as mock_accept:
            with patch("hcli.commands.ida.accept_eula.get_ida_path") as mock_get_path:
                mock_get_path.return_value = test_dir
                result = cli_runner.invoke(accept_eula_cmd, [])
                assert result.exit_code == 0
                assert "Accepting EULA" in result.output
                assert "EULA accepted successfully" in result.output
                mock_accept.assert_called_once_with(test_dir)
