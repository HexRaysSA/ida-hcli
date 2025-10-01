"""Unit tests for update version utilities."""

import sys
from unittest.mock import patch

from hcli.lib.update.version import is_binary


class TestIsBinary:
    """Test binary detection functionality."""

    def test_is_binary_when_frozen(self):
        """Test that is_binary returns True when sys.frozen is True."""
        with patch.object(sys, "frozen", True, create=True):
            assert is_binary() is True

    def test_is_binary_when_not_frozen(self):
        """Test that is_binary returns False when sys.frozen is False."""
        with patch.object(sys, "frozen", False, create=True):
            assert is_binary() is False

    def test_is_binary_when_frozen_not_set(self):
        """Test that is_binary returns False when sys.frozen doesn't exist."""
        # Remove frozen attribute if it exists
        if hasattr(sys, "frozen"):
            original = sys.frozen
            delattr(sys, "frozen")
            try:
                assert is_binary() is False
            finally:
                sys.frozen = original
        else:
            assert is_binary() is False

    def test_is_binary_pip_install(self):
        """Test that regular Python installation is not detected as binary."""
        # This is the actual runtime state during tests
        assert is_binary() is False
