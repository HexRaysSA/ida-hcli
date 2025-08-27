"""Test environment variable precedence for IDA installation directory discovery."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hcli.lib.ida import find_current_ida_install_directory


class TestIDAEnvironmentVariables:
    """Test environment variable precedence for IDA installation directory."""

    def test_hcli_ida_install_dir_takes_precedence(self):
        """Test that HCLI_IDA_INSTALL_DIR takes precedence over other methods."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            
            with patch.dict(os.environ, {"HCLI_IDA_INSTALL_DIR": str(test_path)}, clear=False):
                result = find_current_ida_install_directory()
                assert result == test_path

    def test_idadir_fallback_when_hcli_ida_install_dir_not_set(self):
        """Test that IDADIR is used as fallback when HCLI_IDA_INSTALL_DIR is not set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            
            # Ensure HCLI_IDA_INSTALL_DIR is not set, but HCLI_INSTALL_DIR is not set either
            env_without_hcli = {k: v for k, v in os.environ.items() 
                              if k not in ["HCLI_IDA_INSTALL_DIR", "HCLI_INSTALL_DIR"]}
            env_without_hcli["IDADIR"] = str(test_path)
            
            with patch.dict(os.environ, env_without_hcli, clear=True):
                result = find_current_ida_install_directory()
                assert result == test_path

    def test_hcli_ida_install_dir_over_idadir(self):
        """Test that HCLI_IDA_INSTALL_DIR takes precedence over IDADIR."""
        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            primary_path = Path(tmpdir1)
            fallback_path = Path(tmpdir2)
            
            env_vars = {
                "HCLI_IDA_INSTALL_DIR": str(primary_path),
                "IDADIR": str(fallback_path)
            }
            
            with patch.dict(os.environ, env_vars, clear=False):
                result = find_current_ida_install_directory()
                assert result == primary_path

    def test_backward_compatibility_hcli_install_dir_removed(self):
        """Test that old HCLI_INSTALL_DIR is no longer recognized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            
            # Clear all our env vars and set only the old one
            env_without_new = {k: v for k, v in os.environ.items() 
                             if k not in ["HCLI_IDA_INSTALL_DIR", "IDADIR"]}
            env_without_new["HCLI_INSTALL_DIR"] = str(test_path)
            
            with patch.dict(os.environ, env_without_new, clear=True):
                # This should NOT use HCLI_INSTALL_DIR anymore and should fall back to config
                with pytest.raises(ValueError, match="ida-config.json doesn't exist"):
                    find_current_ida_install_directory()