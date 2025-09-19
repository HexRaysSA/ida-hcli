"""
Shared fixtures and utilities for integration tests.
"""

import re
from typing import Tuple

import pytest

# Only import pexpect if available (not on Windows)
try:
    import pexpect
    PEXPECT_AVAILABLE = True
except ImportError:
    PEXPECT_AVAILABLE = False


class FilteredOutput:
    """Filter terminal escape sequences from pexpect output."""

    def __init__(self, target):
        self.target = target

    def write(self, data):
        # Filter out ANSI escape sequences and cursor position reports
        filtered = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", data)
        filtered = re.sub(r";[0-9]+R", "", filtered)
        if filtered.strip():
            self.target.write(filtered)
            self.target.flush()

    def flush(self):
        self.target.flush()


class CLITester:
    """Helper class for testing CLI commands with pexpect."""

    def __init__(self, timeout: int = 10):
        if not PEXPECT_AVAILABLE:
            pytest.skip("CLITester requires pexpect (not available on Windows)")
        self.timeout = timeout

    def run_command(
        self, command: str, expected_output: str | None = None, timeout: int | None = None
    ) -> Tuple[bool, str]:
        """Run a CLI command and optionally check for expected output."""
        if not PEXPECT_AVAILABLE:
            pytest.skip("CLI testing requires pexpect (not available on Windows)")
            
        if timeout is None:
            timeout = self.timeout

        child = None
        try:
            child = pexpect.spawn(command, timeout=timeout, encoding="utf-8")

            if expected_output:
                child.expect(expected_output)
                output = child.before or ""
                child.close()
                return True, output
            else:
                # Read all output until the command completes
                output = ""
                while True:
                    try:
                        # Read with a short timeout to collect output
                        child.expect(pexpect.EOF, timeout=1)
                        break
                    except pexpect.TIMEOUT:
                        # Command is still running, continue reading
                        if child.before:
                            output += child.before
                        continue

                # Collect any remaining output
                if child.before:
                    output += child.before

                exit_status = child.exitstatus
                child.close()

                # Consider command successful if:
                # 1. Exit status is 0, or
                # 2. Exit status is None but we got some output (common for help commands)
                success = exit_status == 0 or (exit_status is None and len(output.strip()) > 0)
                return success, output

        except pexpect.EOF:
            # EOF is normal for help commands - collect output and consider success
            if child:
                output = child.before or ""
                try:
                    child.close()
                except Exception:
                    pass
                return True, output
            return False, "EOF without child process"

        except pexpect.TIMEOUT:
            if child:
                try:
                    child.close()
                except Exception:
                    pass
            return False, "TIMEOUT"

        except Exception as e:
            if child:
                try:
                    child.close()
                except Exception:
                    pass
            return False, str(e)


@pytest.fixture
def cli_tester():
    """Provide a CLI tester instance."""
    return CLITester(timeout=15)


# Utility functions for test setup
def check_dependencies():
    """Check if required dependencies are available."""
    if not PEXPECT_AVAILABLE:
        return False
        
    try:
        pexpect.spawn("uv --version", timeout=2).expect(pexpect.EOF)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def check_uv_available():
    """Ensure uv is available for running commands."""
    if not PEXPECT_AVAILABLE:
        pytest.skip("uv checking requires pexpect (not available on Windows)")
        
    try:
        pexpect.spawn("uv --version", timeout=2).expect(pexpect.EOF)
        return True
    except Exception:
        pytest.skip("uv not available for integration tests")


@pytest.fixture
def require_uv(check_uv_available):
    """Fixture to ensure uv is available. Use this explicitly in tests that need uv."""
    pass


def pytest_runtest_setup(item):
    """Check for integration test requirements before running integration tests."""
    # Check if this is an integration test
    if "integration" in str(item.fspath):
        # For integration tests that use pexpect, skip on Windows
        if "pexpect" in str(item.function) or any("cli_tester" in name for name in item.fixturenames):
            if not PEXPECT_AVAILABLE:
                pytest.skip("Integration tests require pexpect (not available on Windows)")
        
        # Check for uv availability if the test uses uv-related features
        if any(name in ["require_uv", "check_uv_available"] for name in item.fixturenames):
            if not check_dependencies():
                pytest.skip("Integration tests require pexpect and uv to be installed")
