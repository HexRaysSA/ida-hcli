"""
Integration tests for HCLI download functionality.
These tests verify download-related CLI behavior including interactive commands.
"""

import time

import pexpect
import pytest


@pytest.mark.integration
def test_download_interrupt_handling():
    """Ctrl-C must terminate `hcli download`, including while its picker waits for input."""
    child = pexpect.spawn("uv run hcli download", timeout=10, encoding="utf-8")
    try:
        time.sleep(1)
        child.sendcontrol("c")

        try:
            child.expect(pexpect.EOF, timeout=5)
        except pexpect.TIMEOUT:
            child.kill(9)
            pytest.fail("download did not exit after Ctrl-C")
    finally:
        child.close()
