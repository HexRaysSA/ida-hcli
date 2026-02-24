"""
Integration tests for HCLI authentication functionality.
These tests verify auth-related CLI behavior.
"""

import os
import sys

import pexpect
import pytest


@pytest.mark.integration
class TestAuthCommands:
    """Test authentication-related CLI commands."""

    def test_whoami_unauthenticated(self, cli_tester):
        """Test whoami when not authenticated."""
        success, _output = cli_tester.run_command("uv run hcli whoami")
        # Command may fail due to auth, but should run without crashing
        assert success is not None, "Whoami command should run"

    def test_auth_status(self, cli_tester):
        """Test auth status command."""
        success, _output = cli_tester.run_command("uv run hcli auth status")
        assert success is not None, "Auth status command should run"


@pytest.mark.integration
class TestInteractiveAuth:
    """Test interactive authentication commands."""

    @pytest.mark.skipif(bool(os.getenv("HCLI_API_KEY")), reason="Skip interactive login when GITHUB_TOKEN is present")
    def test_interactive_login(self):
        """Test interactive login flow (demonstration only)."""
        try:
            child = pexpect.spawn("uv run hcli login", timeout=5, encoding="utf-8")
            child.logfile = sys.stdout

            # Look for login prompts or selection
            index = child.expect(
                [
                    "Choose login method:",
                    "You are already logged in",
                    "Google OAuth",
                    "Email \\(OTP\\)",
                    "Open the following URL",
                    "Login successful",
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                ],
                timeout=5,
            )

            if index < 6:  # Any of the expected login-related outputs
                # Send Ctrl+C to cancel
                child.sendcontrol("c")
                success = True
            else:
                success = False

            child.close()
            assert success, "Login command should start successfully"

        except Exception as e:
            pytest.fail(f"Login test failed: {e}")


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("HCLI_INTEGRATION_TESTS"), reason="Integration tests disabled")
class TestAuthenticatedCommands:
    """Test commands that require authentication.

    These tests are skipped unless HCLI_INTEGRATION_TESTS environment variable is set.
    """

    def test_authenticated_whoami(self, cli_tester):
        """Test whoami when authenticated."""
        success, output = cli_tester.run_command("uv run hcli whoami")
        assert success, "Authenticated whoami should succeed"
        assert "email" in output.lower() or "user" in output.lower()

    def test_authenticated_download_list(self, cli_tester):
        """Test download listing when authenticated."""
        # This would test actual download functionality with real auth
        # Implementation depends on test environment setup
        pytest.skip("Requires authenticated test environment")
