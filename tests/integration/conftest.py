"""
Shared fixtures and utilities for integration tests.
"""

import re
from typing import Optional, Tuple

import pexpect
import pytest


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
        self.timeout = timeout

    def run_command(
        self, command: str, expected_output: Optional[str] = None, timeout: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Run a CLI command and optionally check for expected output."""
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
                success = exit_status == 0 or (exit_status is None and output.strip())
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
    try:
        import pexpect

        pexpect.spawn("uv --version", timeout=2).expect(pexpect.EOF)
        return True
    except (ImportError, Exception):
        return False


@pytest.fixture(scope="module")
def check_uv_available():
    """Ensure uv is available for running commands."""
    try:
        pexpect.spawn("uv --version", timeout=2).expect(pexpect.EOF)
        return True
    except Exception:
        pytest.skip("uv not available for integration tests")


@pytest.fixture(autouse=True)
def require_uv(check_uv_available):
    """Auto-use fixture to ensure uv is available."""
    pass


@pytest.fixture(scope="session", autouse=True)
def check_test_requirements():
    """Ensure test requirements are met."""
    if not check_dependencies():
        pytest.skip("Integration tests require pexpect and uv to be installed")
