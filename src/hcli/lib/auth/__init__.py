import asyncio
import json
import logging
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import urlencode

import httpx

from hcli.env import ENV, OAUTH_REDIRECT_URL, OAUTH_SERVER_PORT
from hcli.lib.config import config_store
from hcli.lib.constants.auth import (
    CONFIG_CREDENTIALS,
    Credentials,
    CredentialsConfig,
    CredentialType,
)

logger = logging.getLogger(__name__)


# Lightweight replacements for supabase-auth SDK types


@dataclass
class GoTrueUser:
    email: str


@dataclass
class GoTrueUserResponse:
    user: GoTrueUser | None


@dataclass
class GoTrueSession:
    access_token: str
    refresh_token: str
    user: GoTrueUser | None = None


@dataclass
class GoTrueOAuthResponse:
    url: str


@dataclass
class GoTrueClient:
    """Minimal GoTrue HTTP client — replaces the supabase-auth SDK."""

    base_url: str
    anon_key: str
    _session: GoTrueSession | None = field(default=None, repr=False)

    def _headers(self, token: str | None = None) -> dict[str, str]:
        bearer = token or (self._session.access_token if self._session else None) or self.anon_key
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        }

    def get_user(self, token: str | None = None) -> GoTrueUserResponse:
        resp = httpx.get(f"{self.base_url}/user", headers=self._headers(token))
        resp.raise_for_status()
        data = resp.json()
        if data and data.get("email"):
            return GoTrueUserResponse(user=GoTrueUser(email=data["email"]))
        return GoTrueUserResponse(user=None)

    def get_session(self) -> GoTrueSession | None:
        return self._session

    def set_session(self, access_token: str, refresh_token: str) -> None:
        self._session = GoTrueSession(access_token=access_token, refresh_token=refresh_token)
        try:
            user_resp = self.get_user(access_token)
            if user_resp.user:
                self._session.user = user_resp.user
        except Exception:
            pass

    def sign_in_with_oauth(self, params: dict) -> GoTrueOAuthResponse:
        provider = params["provider"]
        options = params.get("options", {})
        redirect_to = options.get("redirect_to", "")
        query_params = options.get("query_params", {})
        qs = {"provider": provider, "redirect_to": redirect_to, **query_params}
        return GoTrueOAuthResponse(url=f"{self.base_url}/authorize?{urlencode(qs)}")

    def sign_in_with_otp(self, params: dict) -> None:
        resp = httpx.post(f"{self.base_url}/otp", json=params, headers=self._headers())
        resp.raise_for_status()

    def verify_otp(self, params: dict) -> None:
        resp = httpx.post(f"{self.base_url}/verify", json=params, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        if data.get("access_token"):
            self.set_session(data["access_token"], data.get("refresh_token", ""))

    def sign_out(self) -> None:
        if self._session:
            try:
                httpx.post(f"{self.base_url}/logout", headers=self._headers())
            except Exception:
                pass
        self._session = None


class AuthService:
    """Singleton authentication service handling multiple credentials."""

    _instance: "AuthService | None" = None

    def __init__(self):
        if AuthService._instance is not None:
            raise RuntimeError("AuthService is a singleton. Use AuthService.instance")

        self.supabase_auth = GoTrueClient(
            base_url=f"{ENV.HCLI_SUPABASE_URL}/auth/v1",
            anon_key=ENV.HCLI_SUPABASE_ANON_KEY,
        )

        # Current session state (for active interactive auth)
        self.session: GoTrueSession | None = None
        self.user: GoTrueUser | None = None
        self._server_thread: Thread | None = None
        self._oauth_result: dict[str, str] | None = None

        # Multi-source auth state
        self._auth_config: CredentialsConfig | None = None
        self._current_source: Credentials | None = None
        self._forced_credentials: str | None = None  # For --auth-source override

    @classmethod
    def instance(cls) -> "AuthService":
        """Get singleton instance of AuthService."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def init(self, forced_credentials: str | None = None) -> None:
        """Initialize the auth service and load credentials."""
        self._forced_credentials = forced_credentials
        self._load_auth_config()
        self._load_current_credentials()

    def _load_auth_config(self) -> None:
        """Load credentials configuration."""
        config_data = config_store.get_object(CONFIG_CREDENTIALS)
        if config_data:
            try:
                self._auth_config = CredentialsConfig(**config_data)
            except Exception:
                self._auth_config = CredentialsConfig()
        else:
            self._auth_config = CredentialsConfig()

    def _save_auth_config(self) -> None:
        """Save credentials configuration."""
        if self._auth_config:
            config_store.set_object(CONFIG_CREDENTIALS, self._auth_config.model_dump())

    def _load_current_credentials(self) -> None:
        """Load the current active credentials."""
        if not self._auth_config:
            return

        # Environment variable always takes precedence - don't load from sources
        if ENV.HCLI_API_KEY:
            self._current_source = None  # Environment key doesn't need a source
            return

        # Use forced source if specified
        if self._forced_credentials and self._forced_credentials in self._auth_config.credentials:
            self._current_source = self._auth_config.credentials[self._forced_credentials]
        else:
            # Use default source
            self._current_source = self._auth_config.get_default_credentials()

        # Initialize session for interactive sources
        if self._current_source and self._current_source.type == CredentialType.INTERACTIVE:
            try:
                if self._current_source.token:
                    # Validate the stored token against the server
                    user_response = self.supabase_auth.get_user(self._current_source.token)
                    if user_response and user_response.user:
                        self.user = user_response.user
                        self.session = GoTrueSession(
                            access_token=self._current_source.token,
                            refresh_token="",
                            user=user_response.user,
                        )
            except Exception:
                pass

    def force_credentials(self, name: str) -> bool:
        """Force a specific credentials for this session."""
        if self._auth_config and name in self._auth_config.credentials:
            self._forced_credentials = name
            self._load_current_credentials()
            return True
        return False

    def list_credentials(self) -> list[Credentials]:
        """Get all available credentials."""
        if not self._auth_config:
            return []
        return list(self._auth_config.credentials.values())

    def get_current_credentials(self) -> Credentials | None:
        """Get the currently active credentials."""
        return self._current_source

    def get_default_credentials_name(self) -> str | None:
        """Get the name of the default credentials."""
        return self._auth_config.default if self._auth_config else None

    def set_default_credentials(self, name: str) -> bool:
        """Set the default credentials."""
        if self._auth_config and self._auth_config.set_default(name):
            self._save_auth_config()
            self._load_current_credentials()
            return True
        return False

    def add_credentials(self, source: Credentials) -> None:
        """Add a new credentials."""
        if not self._auth_config:
            self._auth_config = CredentialsConfig()

        self._auth_config.add_credentials(source)
        self._save_auth_config()

    def remove_credentials(self, name: str) -> bool:
        """Remove an credentials."""
        if self._auth_config and self._auth_config.remove_credentials(name):
            self._save_auth_config()
            # Reload current source if we removed the active one
            if self._current_source and self._current_source.name == name:
                self._load_current_credentials()
            return True
        return False

    def generate_unique_name(self, base_name: str) -> str:
        """Generate a unique name for an credentials."""
        if not self._auth_config or base_name not in self._auth_config.credentials:
            return base_name

        counter = 1
        while f"{base_name}-{counter}" in self._auth_config.credentials:
            counter += 1
        return f"{base_name}-{counter}"

    def _should_show_multi_auth_ui(self) -> bool:
        """Return True if multi-auth UI should be shown (2+ sources)."""
        return len(self.list_credentials()) > 1

    # Legacy compatibility methods
    def is_logged_in(self) -> bool:
        """Check if user is authenticated via any method."""
        # Check environment variable first (always available)
        if ENV.HCLI_API_KEY:
            return True

        # Check if we have a fresh session from OAuth flow (before source creation)
        if self.session is not None and self.session.user is not None:
            return True

        if not self._current_source:
            return False

        if self._current_source.type == CredentialType.KEY:
            return bool(self._current_source.token)
        elif self._current_source.type == CredentialType.INTERACTIVE:
            # For interactive auth, check if session is valid by attempting to get user
            if self.session is not None and self.session.user is not None:
                return True
            # If we have credentials but no valid session, try to refresh
            if self._current_source.token:
                try:
                    user_response = self.supabase_auth.get_user(self._current_source.token)
                    if user_response and user_response.user:
                        self.user = user_response.user
                        self.session = GoTrueSession(
                            access_token=self._current_source.token,
                            refresh_token="",
                            user=user_response.user,
                        )
                        return True
                except Exception:
                    # Token exists but is expired/invalid
                    return False
            return False

        return False

    def has_expired_session(self) -> bool:
        """Check if user has credentials but session is expired."""
        # No expired session for environment API key
        if ENV.HCLI_API_KEY:
            return False

        # No expired session if no credentials exist
        if not self._current_source:
            return False

        # Only interactive auth can have expired sessions
        if self._current_source.type != CredentialType.INTERACTIVE:
            return False

        # Has credentials but session is invalid/expired
        if self._current_source.token and (self.session is None or self.session.user is None):
            try:
                # Try to verify if token is actually expired
                user_response = self.supabase_auth.get_user(self._current_source.token)
                return user_response is None or user_response.user is None
            except Exception:
                return True

        return False

    def get_auth_type(self) -> dict[str, str]:
        """Get the type of authentication being used."""
        # Environment variable takes precedence
        if ENV.HCLI_API_KEY:
            return {"type": CredentialType.KEY, "source": "env"}

        if not self._current_source:
            return {"type": CredentialType.INTERACTIVE, "source": "none"}

        source_origin = "forced" if self._forced_credentials else "default"
        return {"type": self._current_source.type, "source": source_origin}

    def get_api_key(self) -> str | None:
        """Get API key from current source."""
        # Check environment variable first (legacy behavior)
        if ENV.HCLI_API_KEY:
            return ENV.HCLI_API_KEY

        if self._current_source and self._current_source.type == CredentialType.KEY:
            return self._current_source.token
        return None

    def get_user(self) -> dict[str, str] | None:
        """Get current user information."""
        # Handle environment variable case
        if ENV.HCLI_API_KEY and not self._current_source:
            try:
                import asyncio

                from hcli.lib.api.auth import auth

                try:
                    asyncio.get_running_loop()
                    return {"email": "api-key-user"}  # Fallback for async contexts
                except RuntimeError:
                    user_info = asyncio.run(auth.whoami())
                    return {"email": user_info.email}
            except Exception:
                return {"email": "api-key-user"}

        if not self._current_source:
            return None

        # Update last used timestamp for managed sources
        self._current_source.update_last_used()
        self._save_auth_config()

        return {"email": self._current_source.email}

    def get_access_token(self) -> str | None:
        """Get access token from current session."""
        return self.session.access_token if self.session else None

    # Auth flow methods (updated for multi-source)
    def _create_or_update_interactive_credentials(
        self, email: str, token: str, name: str | None = None
    ) -> Credentials | None:
        """Create new or update existing interactive credentials for the given email."""
        # Check if interactive credentials already exist for this email
        existing_source = None
        if self._auth_config:
            existing_source = self._auth_config.find_credentials_by_email_and_type(email, CredentialType.INTERACTIVE)

        if existing_source:
            # Update existing credentials with new token
            existing_source.token = token
            existing_source.update_last_used()
            self._save_auth_config()

            # Set as current/default
            self._current_source = existing_source
            self.set_default_credentials(existing_source.name)

            return existing_source
        else:
            # Create new credentials
            source_name = name or email
            source_name = self.generate_unique_name(source_name)

            # Create new credentials
            source = Credentials.create_credentials(source_name, CredentialType.INTERACTIVE, token, email)
            self.add_credentials(source)

            # Set as current/default
            self._current_source = source
            self.set_default_credentials(source_name)

            return source

    async def login_interactive(self, name: str | None = None, force: bool = False) -> Credentials | None:
        """Login using OAuth flow and create new credentials."""
        await self._login_flow(prompt=force)
        if self.is_logged_in() and self.session and self.session.user:
            email = self.session.user.email
            token = self.session.access_token if self.session else ""
            return self._create_or_update_interactive_credentials(email, token, name)
        return None

    async def login_otp(self, email: str, name: str | None = None, force: bool = False) -> bool:
        """Login using OTP and create credentials."""
        if force:
            self.logout_current()

        self.supabase_auth.sign_in_with_otp({"email": email})
        return True

    def verify_otp(self, email: str, otp: str, name: str | None = None) -> Credentials | None:
        """Verify OTP and create credentials."""
        try:
            self.supabase_auth.verify_otp({"email": email, "token": otp, "type": "email"})

            # Refresh session after OTP verification
            session = self.supabase_auth.get_session()
            if session and session.user:
                self.user = session.user
                self.session = session

                token = session.access_token
                return self._create_or_update_interactive_credentials(email, token, name)
        except Exception:
            pass
        return None

    async def add_api_key_credentials(self, name: str, token: str) -> Credentials | None:
        """Add a new API key credentials."""
        # Get user email from API
        try:
            from hcli.lib.api.auth import auth

            # Temporarily set the API key to test it
            old_source = self._current_source
            temp_source = Credentials.create_credentials("temp", CredentialType.KEY, token, "temp@example.com")
            self._current_source = temp_source

            try:
                user_info = await auth.whoami()
                email = user_info.email
                # Create and add the source with key_name for label generation
                source = Credentials.create_credentials(name, CredentialType.KEY, token, email)

                self.remove_credentials(name)
                self.add_credentials(source)

                return source
            finally:
                self._current_source = old_source

        except Exception:
            return None

    def logout_current(self) -> None:
        """Logout from current session (for interactive auth)."""
        if self._current_source and self._current_source.type == CredentialType.INTERACTIVE:
            self.supabase_auth.sign_out()

        self.session = None
        self.user = None

    def show_login_info(self) -> None:
        """Display current login status and user information."""
        from hcli.lib.console import console

        if not self.is_logged_in():
            console.print("You are not logged in.")
            return

        # Handle environment variable case
        if ENV.HCLI_API_KEY and not self._current_source:
            user = self.get_user()
            email = user["email"] if user else "unknown"
            console.print(f"You are logged in as {email} using an API key from HCLI_API_KEY environment variable")
            return

        source = self.get_current_credentials()
        if not source:
            console.print("You are not logged in.")
            return

        # Simplified output for single source scenarios
        if not self._should_show_multi_auth_ui():
            console.print(f"You are logged in as {source.email}")
            return

        # Detailed output for multiple sources
        auth_info = ""
        if source.type == CredentialType.KEY:
            auth_info = f" using API key '{source.name}'"
        else:
            auth_info = f" using interactive login '{source.name}'"

        default_info = ""
        if self._forced_credentials:
            default_info = " (forced via --auth-source)"
        elif source.name == self.get_default_credentials_name():
            default_info = " (default)"

        label = getattr(source, "label", source.email)
        console.print(f"You are logged in as {label}{auth_info}{default_info}")

    # OAuth flow implementation (unchanged)
    async def _login_flow(self, prompt: bool = False):
        """Handle OAuth login flow with local HTTP server."""
        from hcli.lib.console import console

        console.print(f"Starting Google OAuth login{'with prompt' if prompt else ''}...")

        # Build OAuth URL with optional prompt parameter
        query_params = {}
        if prompt:
            query_params["prompt"] = "login"

        # Start OAuth flow
        auth_response = self.supabase_auth.sign_in_with_oauth(
            {
                "provider": "google",
                "options": {
                    "redirect_to": OAUTH_REDIRECT_URL,
                    "query_params": query_params,
                },
            }
        )

        oauth_url = auth_response.url
        if not oauth_url:
            console.print("No OAuth URL received")
            return

        console.print(f"Open this URL in your browser to continue login: {oauth_url}")
        webbrowser.open(oauth_url)

        # Start local HTTP server to handle callback
        await self._start_oauth_server()

    async def _start_oauth_server(self):
        """Start local HTTP server to handle OAuth callback."""
        self._oauth_result = None

        class OAuthHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                if handler_self.path.startswith("/callback"):
                    # Serve HTML page to extract tokens from URL hash
                    handler_self.send_response(200)
                    handler_self.send_header("Content-Type", "text/html")
                    handler_self.end_headers()
                    handler_self.wfile.write(HTML_PAGE.encode())
                else:
                    handler_self.send_response(404)
                    handler_self.end_headers()

            def do_POST(handler_self):
                if handler_self.path == "/token":
                    # Handle token submission from browser
                    content_length = int(handler_self.headers["Content-Length"])
                    post_data = handler_self.rfile.read(content_length)

                    try:
                        token_data = json.loads(post_data.decode())
                        access_token = token_data.get("access_token")
                        refresh_token = token_data.get("refresh_token")

                        if access_token:
                            self._oauth_result = {
                                "access_token": access_token,
                                "refresh_token": refresh_token,
                            }

                            handler_self.send_response(200)
                            handler_self.send_header("Content-Type", "text/plain")
                            handler_self.end_headers()
                            handler_self.wfile.write(b"Token received and saved.")
                        else:
                            handler_self.send_response(400)
                            handler_self.end_headers()
                    except Exception as e:
                        logger.warning(f"Failed to process token: {e}")
                        handler_self.send_response(500)
                        handler_self.end_headers()
                else:
                    handler_self.send_response(404)
                    handler_self.end_headers()

            def log_message(self, format, *args):
                pass  # Suppress server logs

        # Start server in a separate thread
        server = HTTPServer(("localhost", OAUTH_SERVER_PORT), OAuthHandler)
        self._server_thread = Thread(target=server.serve_forever)
        self._server_thread.daemon = True
        self._server_thread.start()

        # Wait for OAuth result
        max_wait = 120  # 2 minutes timeout
        wait_count = 0
        while wait_count < max_wait and self._oauth_result is None:
            await asyncio.sleep(1)
            wait_count += 1

        server.shutdown()
        server.server_close()

        if self._oauth_result:
            from hcli.lib.console import console

            # Set session with received tokens
            self.supabase_auth.set_session(self._oauth_result["access_token"], self._oauth_result["refresh_token"])

            # Refresh user and session info
            self.session = self.supabase_auth.get_session()
            if self.session and self.session.user:
                self.user = self.session.user
                console.print(f"{self.user.email} logged in successfully!")
        else:
            from hcli.lib.console import console

            console.print("Login timeout or failed")


# Global auth service instance accessor
def get_auth_service() -> AuthService:
    """Get the global AuthService instance."""
    return AuthService.instance()


HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login</title>
</head>
<body>
  <script>
    // Extract token from hash
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    const accessToken = hashParams.get("access_token");
    const refreshToken = hashParams.get("refresh_token");

    if (accessToken) {
      // Send token back to server
      fetch("http://localhost:9999/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: accessToken, refresh_token: refreshToken }),
      })
      .then(() => {
        document.body.innerHTML = "Login successful! You can close this tab.";
      })
      .catch((e) => {
        console.error("Error saving token:", e);
        document.body.innerHTML = "Error saving token.";
      });
    } else {
      document.body.innerHTML = "No token found in URL.";
    }
  </script>
</body>
</html>
"""
