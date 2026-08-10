"""
Shared fixtures and utilities for integration tests.
"""

import pexpect
import pytest


@pytest.fixture(scope="session", autouse=True)
def require_uv():
    """Integration tests drive the CLI through `uv run`."""
    try:
        pexpect.spawn("uv --version", timeout=10).expect(pexpect.EOF)
    except Exception:
        pytest.skip("uv not available for integration tests")
