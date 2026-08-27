import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from hcli.env import (
    ENV,
    OAUTH_REDIRECT_URL,
    OAUTH_SERVER_PORT,
)
from hcli.lib.config import config_store
from hcli.lib.constants.auth import (
    CONFIG_CREDENTIALS,
    Credentials,
    CredentialsConfig,
    CredentialType,
)

logger = logging.getLogger(__name__)

# Renew the access token this many seconds before it actually expires, so a
# request never races the expiry boundary.
_EXPIRY_SKEW_SECONDS = 60


def _b64url(data: bytes) -> str:
    """URL-safe base64 without padding, as required by PKCE (RFC 7636)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass
class User:
    email: str


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    id_token: str | None = None


@dataclass
class Session:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # epoch seconds
    user: User | None = None

    @classmethod
    def from_tokens(cls, tokens: OAuthTokens, user: User | None) -> "Session":
        return cls(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            user=user,
        )


@dataclass
class Discovery:
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None = None
    revocation_endpoint: str | None = None


class OAuthClient:
    """Minimal OAuth 2.1 / OIDC public client (Authorization Code + PKCE).

    Endpoints are discovered from the issuer's well-known document, so the
    client stays provider-agnostic. No client secret is used.
    """

    def __init__(self, issuer: str, client_id: str, scope: str):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.scope = scope
        self._discovery: Discovery | None = None

    def discover(self) -> Discovery:
        """Resolve and cache the authorization server metadata."""
        if self._discovery is not None:
            return self._discovery

        candidates = [
            f"{self.issuer}/.well-known/oauth-authorization-server",
            f"{self.issuer}/.well-known/openid-configuration",
        ]
        errors = []
        for url in candidates:
            try:
                resp = httpx.get(url, headers={"Accept": "application/json"}, timeout=30.0)
                if resp.status_code == 200:
                    data = resp.json()
                    self._discovery = Discovery(
                        authorization_endpoint=data["authorization_endpoint"],
                        token_endpoint=data["token_endpoint"],
                        userinfo_endpoint=data.get("userinfo_endpoint"),
                        revocation_endpoint=data.get("revocation_endpoint"),
                    )
                    return self._discovery
                errors.append(f"{url} -> HTTP {resp.status_code}")
            except Exception as e:
                errors.append(f"{url} -> {e}")
        raise RuntimeError(f"OAuth discovery failed for {self.issuer}: {'; '.join(errors)}")

    @staticmethod
    def generate_pkce() -> tuple[str, str]:
        """Return an (verifier, challenge) PKCE pair using S256."""
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return verifier, challenge

    def build_authorize_url(self, redirect_uri: str, state: str, challenge: str, prompt: str | None = None) -> str:
        discovery = self.discover()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if prompt:
            params["prompt"] = prompt
        return f"{discovery.authorization_endpoint}?{urlencode(params)}"

    def _form_post(self, endpoint: str, data: dict[str, str]) -> httpx.Response:
        """POST a form-encoded body to an OAuth endpoint."""
        return httpx.post(
            endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30.0,
        )

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> OAuthTokens:
        resp = self._form_post(
            self.discover().token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            },
        )
        resp.raise_for_status()
        return self._parse_token_response(resp.json())

    def refresh(self, refresh_token: str) -> OAuthTokens:
        resp = self._form_post(
            self.discover().token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
        )
        resp.raise_for_status()
        return self._parse_token_response(resp.json())

    def revoke(self, token: str) -> None:
        """Best-effort token revocation (RFC 7009)."""
        discovery = self.discover()
        if not discovery.revocation_endpoint:
            return
        self._form_post(discovery.revocation_endpoint, {"token": token, "client_id": self.client_id})

    def get_userinfo(self, access_token: str) -> dict | None:
        discovery = self.discover()
        if not discovery.userinfo_endpoint:
            return None
        resp = httpx.get(
            discovery.userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_token_response(data: dict) -> OAuthTokens:
        expires_in = data.get("expires_in")
        # `expires_in is not None` (not truthiness): a literal 0 means the token
        # is already expired, which must not be read as "no expiry / never stale".
        expires_at = time.time() + float(expires_in) - _EXPIRY_SKEW_SECONDS if expires_in is not None else None
        return OAuthTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=expires_at,
            id_token=data.get("id_token"),
        )


def _email_from_id_token(id_token: str | None) -> str | None:
    """Extract the email claim from an id_token without verifying its signature."""
    if not id_token:
        return None
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    try:
        padded = payload + "=" * ((4 - len(payload) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return claims.get("email")
    except Exception:
        return None


@dataclass
class _PendingFlow:
    """Client-side state for an in-progress OOB login.

    Only the PKCE verifier and redirect URI are kept: `state` protects the
    loopback callback, but in the OOB flow the code is pasted by the user
    rather than delivered on a redirect, so there is nothing to match it against.
    """

    verifier: str
    redirect_uri: str


class AuthService:
    """Singleton authentication service handling multiple credentials."""

    _instance: "AuthService | None" = None

    def __init__(self):
        if AuthService._instance is not None:
            raise RuntimeError("AuthService is a singleton. Use AuthService.instance")

        self.oauth = OAuthClient(
            issuer=ENV.HCLI_OAUTH_ISSUER,
            client_id=ENV.HCLI_OAUTH_CLIENT_ID,
            scope=ENV.HCLI_OAUTH_SCOPE,
        )

        # Current session state (for active interactive auth)
        self.session: Session | None = None
        self._server_thread: Thread | None = None
        self._oauth_code: str | None = None
        self._oauth_error: str | None = None
        self._pending_oob: _PendingFlow | None = None
        # Set once a refresh attempt fails this process, so repeated auth checks
        # in a single command don't each re-hit the token endpoint.
        self._refresh_failed: bool = False

        # Multi-source auth state
        self._auth_config: CredentialsConfig | None = None
        self._current_source: Credentials | None = None
        self._forced_credentials: str | None = None  # For --auth-source override

    @property
    def user(self) -> "User | None":
        """The current session's user, derived from the active session."""
        return self.session.user if self.session else None

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

        # Rebuild the in-memory session for interactive sources from stored
        # tokens. This is offline: validation/refresh happens lazily when a
        # token is actually needed.
        #
        # A trustworthy OAuth credential always carries validity metadata: an
        # `expires_at` and/or a `refresh_token`. A stored interactive token with
        # neither is a pre-migration (Supabase) credential the new API will only
        # ever 401 on — leave the session unset so the user is cleanly prompted
        # to log in again rather than being reported logged-in forever.
        self.session = None
        self._refresh_failed = False
        source = self._current_source
        if (
            source
            and source.type == CredentialType.INTERACTIVE
            and source.token
            and (source.expires_at is not None or source.refresh_token)
        ):
            self.session = Session(
                access_token=source.token,
                refresh_token=source.refresh_token,
                expires_at=source.expires_at,
                user=User(email=source.email),
            )

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
        """Remove a credentials, revoking its OAuth tokens best-effort."""
        if self._auth_config and name in self._auth_config.credentials:
            source = self._auth_config.credentials[name]
            self._revoke_interactive_tokens(source)

        if self._auth_config and self._auth_config.remove_credentials(name):
            self._save_auth_config()
            # Reload current source if we removed the active one
            if self._current_source and self._current_source.name == name:
                self._load_current_credentials()
            return True
        return False

    def _revoke_interactive_tokens(self, source: Credentials) -> None:
        """Revoke an interactive credential's grant at the AS (best-effort)."""
        if source.type != CredentialType.INTERACTIVE:
            return
        # Revoking the refresh token invalidates the whole grant (access token
        # included), so only fall back to the access token when there is no
        # refresh token — one request, not two sequential 30s-timeout calls.
        token = source.refresh_token or source.token
        if not token:
            return
        try:
            self.oauth.revoke(token)
        except Exception:
            pass

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

    # Auth-status queries used by the API client and command guards.
    def is_logged_in(self) -> bool:
        """Check if user is authenticated via any method."""
        # Check environment variable first (always available)
        if ENV.HCLI_API_KEY:
            return True

        # Check if we have a fresh session from OAuth flow (before source creation)
        if self.session is not None and self.session.user is not None and self._current_source is None:
            return True

        if not self._current_source:
            return False

        if self._current_source.type == CredentialType.KEY:
            return bool(self._current_source.token)
        elif self._current_source.type == CredentialType.INTERACTIVE:
            # Valid if we can produce a (possibly refreshed) access token.
            return self._ensure_valid_token() is not None

        return False

    def has_expired_session(self) -> bool:
        """True when an interactive credential has a stored token that can no longer be used."""
        source = self._current_source
        if ENV.HCLI_API_KEY or not source or source.type != CredentialType.INTERACTIVE:
            return False
        # A stored token that is_logged_in() can neither use nor refresh is expired.
        return bool(source.token) and not self.is_logged_in()

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
        """Get a valid access token for the current session, refreshing if needed."""
        return self._ensure_valid_token()

    def _ensure_valid_token(self) -> str | None:
        """Return a non-expired access token, refreshing via the refresh token if necessary."""
        if not self.session:
            return None

        now = time.time()
        if self.session.expires_at is None or self.session.expires_at > now:
            return self.session.access_token

        # Access token expired: try to refresh, at most once per process.
        if self._refresh_failed or not self.session.refresh_token:
            return None
        try:
            tokens = self.oauth.refresh(self.session.refresh_token)
        except Exception:
            self._refresh_failed = True
            return None

        self._store_session_tokens(tokens)
        return self.session.access_token

    def _persist_tokens(self, source: Credentials, tokens: OAuthTokens) -> None:
        """Copy an OAuthTokens triple onto a credential and save the config."""
        source.token = tokens.access_token
        source.refresh_token = tokens.refresh_token
        source.expires_at = tokens.expires_at
        source.update_last_used()
        self._save_auth_config()

    def _store_session_tokens(self, tokens: OAuthTokens) -> None:
        """Update the in-memory session and persist tokens to the active credential."""
        user = self.user
        # A refresh response may omit a rotated refresh token; keep the old one.
        if not tokens.refresh_token and self.session and self.session.refresh_token:
            tokens = replace(tokens, refresh_token=self.session.refresh_token)

        self._refresh_failed = False
        self.session = Session.from_tokens(tokens, user)

        if self._current_source and self._current_source.type == CredentialType.INTERACTIVE:
            self._persist_tokens(self._current_source, tokens)

    # Auth flow methods (updated for multi-source)
    def _create_or_update_interactive_credentials(
        self, email: str, tokens: OAuthTokens, name: str | None = None
    ) -> Credentials | None:
        """Create new or update existing interactive credentials for the given email."""
        # Check if interactive credentials already exist for this email
        existing_source = None
        if self._auth_config:
            existing_source = self._auth_config.find_credentials_by_email_and_type(email, CredentialType.INTERACTIVE)

        if existing_source:
            # Update existing credentials with new tokens
            self._persist_tokens(existing_source, tokens)

            # Set as current/default
            self._current_source = existing_source
            self.set_default_credentials(existing_source.name)

            return existing_source
        else:
            # Create new credentials
            source_name = name or email
            source_name = self.generate_unique_name(source_name)

            source = Credentials.create_credentials(
                source_name,
                CredentialType.INTERACTIVE,
                tokens.access_token,
                email,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
            )
            self.add_credentials(source)

            # Set as current/default
            self._current_source = source
            self.set_default_credentials(source_name)

            return source

    def _apply_tokens(self, tokens: OAuthTokens) -> User | None:
        """Resolve the user for freshly obtained tokens and set the active session."""
        # The scope requests `email`, so the id_token carries it locally; decode
        # that first and only fall back to a userinfo round-trip when it doesn't.
        email = _email_from_id_token(tokens.id_token)
        if not email:
            try:
                userinfo = self.oauth.get_userinfo(tokens.access_token)
                if userinfo:
                    email = userinfo.get("email")
            except Exception:
                pass

        user = User(email=email) if email else None
        self._refresh_failed = False
        self.session = Session.from_tokens(tokens, user)
        return user

    async def login_interactive(self, name: str | None = None, force: bool = False) -> Credentials | None:
        """Login using the browser-based Authorization Code + PKCE flow."""
        tokens = await self._login_flow(prompt="login" if force else None)
        if tokens and self.session and self.session.user:
            return self._create_or_update_interactive_credentials(self.session.user.email, tokens, name)
        return None

    def begin_oob_login(self, force: bool = False) -> str:
        """Start a headless (out-of-band) login and return the authorization URL.

        The caller shows the URL to the user, collects the pasted code, and
        passes it to :meth:`complete_oob_login`.
        """
        verifier, challenge = OAuthClient.generate_pkce()
        state = secrets.token_urlsafe(24)
        redirect_uri = ENV.HCLI_OAUTH_OOB_REDIRECT_URL
        url = self.oauth.build_authorize_url(redirect_uri, state, challenge, prompt="login" if force else None)
        self._pending_oob = _PendingFlow(verifier=verifier, redirect_uri=redirect_uri)
        return url

    def complete_oob_login(self, code: str, name: str | None = None) -> Credentials | None:
        """Complete a headless login by exchanging the pasted authorization code."""
        flow = self._pending_oob
        self._pending_oob = None
        if not flow:
            return None

        try:
            tokens = self.oauth.exchange_code(code.strip(), flow.verifier, flow.redirect_uri)
        except Exception as e:
            logger.warning(f"OOB token exchange failed: {e}")
            return None

        user = self._apply_tokens(tokens)
        if not user:
            return None
        return self._create_or_update_interactive_credentials(user.email, tokens, name)

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
            self._revoke_interactive_tokens(self._current_source)

        self.session = None

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

    # OAuth Authorization Code + PKCE flow (loopback)
    async def _login_flow(self, prompt: str | None = None) -> OAuthTokens | None:
        """Handle browser-based OAuth login with a loopback HTTP server."""
        from hcli.lib.console import console

        console.print("Starting browser login...")

        verifier, challenge = OAuthClient.generate_pkce()
        state = secrets.token_urlsafe(24)

        try:
            oauth_url = self.oauth.build_authorize_url(OAUTH_REDIRECT_URL, state, challenge, prompt)
        except Exception as e:
            console.print(f"[red]Could not reach the authorization server: {e}[/red]")
            return None

        console.print(f"Open this URL in your browser to continue login: {oauth_url}")
        webbrowser.open(oauth_url)

        # Start local HTTP server to handle callback and wait for the code.
        code = await self._start_oauth_server(state)
        if self._oauth_error:
            console.print(f"[red]Authorization failed: {self._oauth_error}[/red]")
            return None
        if not code:
            console.print("Login timeout or failed")
            return None

        try:
            tokens = self.oauth.exchange_code(code, verifier, OAUTH_REDIRECT_URL)
        except Exception as e:
            console.print(f"[red]Token exchange failed: {e}[/red]")
            return None

        user = self._apply_tokens(tokens)
        if not user:
            console.print("[red]Signed in, but could not determine your account email from the token.[/red]")
            return None
        console.print(f"{user.email} logged in successfully!")
        return tokens

    async def _start_oauth_server(self, expected_state: str) -> str | None:
        """Start a loopback HTTP server, wait for the OAuth callback, return the code."""
        self._oauth_code = None
        self._oauth_error = None

        service = self

        class OAuthHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self):
                parsed = urlparse(handler_self.path)
                if parsed.path != "/callback":
                    handler_self.send_response(404)
                    handler_self.end_headers()
                    return

                params = parse_qs(parsed.query)
                error = params.get("error", [None])[0]
                code = params.get("code", [None])[0]
                state = params.get("state", [None])[0]

                if error:
                    service._oauth_error = params.get("error_description", [error])[0]
                    handler_self._respond(HTML_FAILURE)
                elif code and state == expected_state:
                    service._oauth_code = code
                    handler_self._respond(HTML_SUCCESS)
                else:
                    # A stray or duplicate hit on the callback (browser prefetch,
                    # reload, link scanner) without a matching code+state: respond
                    # but keep waiting for the real redirect rather than latching a
                    # terminal error that would abort the still-pending login.
                    handler_self._respond(HTML_FAILURE)

            def _respond(handler_self, body: str):
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "text/html")
                handler_self.end_headers()
                handler_self.wfile.write(body.encode())

            def log_message(self, format, *args):
                pass  # Suppress server logs

        # Start server in a separate thread
        server = HTTPServer(("127.0.0.1", OAUTH_SERVER_PORT), OAuthHandler)
        self._server_thread = Thread(target=server.serve_forever)
        self._server_thread.daemon = True
        self._server_thread.start()

        # Wait for the callback (2 minute timeout)
        max_wait = 120
        wait_count = 0
        while wait_count < max_wait and self._oauth_code is None and self._oauth_error is None:
            await asyncio.sleep(1)
            wait_count += 1

        server.shutdown()
        server.server_close()

        return self._oauth_code


# Global auth service instance accessor
def get_auth_service() -> AuthService:
    """Get the global AuthService instance."""
    return AuthService.instance()


HTML_SUCCESS = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Login</title></head>
<body><p>Login successful! You can close this tab.</p></body>
</html>
"""

HTML_FAILURE = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Login</title></head>
<body><p>Login failed. You can close this tab and try again.</p></body>
</html>
"""
