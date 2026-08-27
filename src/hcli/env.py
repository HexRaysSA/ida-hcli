import os

from . import __version__


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable using one consistent truthy set."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "yes", "on", "1")


def _env_optional(name: str) -> str | None:
    """An optional environment variable, treating set-but-empty as unset.

    `export HCLI_IDAUSR=` leaves the variable set to "", which must not
    survive as an override: readers do `Path(ENV.HCLI_IDAUSR)`, and
    `Path("")` is the current directory.
    """
    return os.getenv(name) or None


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable, falling back to *default* on a bad value.

    Parsed at class-body eval and imported by the CLI entrypoint, so a malformed
    value must not raise — that would brick every command, not just the feature.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


class ENV:
    """Environment configuration mirroring the Deno version."""

    HCLI_API_KEY: str | None = _env_optional("HCLI_API_KEY")
    HCLI_DEBUG: bool = _env_bool("HCLI_DEBUG")
    HCLI_API_URL: str = os.getenv("HCLI_API_URL", "https://api.eu.hex-rays.com")
    HCLI_CLOUD_URL: str = os.getenv("HCLI_CLOUD_URL", "https://api.hcli.run")
    HCLI_PORTAL_URL: str = os.getenv("HCLI_PORTAL_URL", "https://my.hex-rays.com")
    HCLI_RELEASE_URL: str = os.getenv("HCLI_RELEASE_URL", "https://hcli.docs.hex-rays.com")

    # GitHub integration
    HCLI_GITHUB_TOKEN: str | None = _env_optional("GITHUB_TOKEN") or _env_optional("GH_TOKEN")
    HCLI_GITHUB_API_URL: str = os.getenv("GITHUB_API_URL", "https://api.github.com")
    HCLI_GITHUB_URL: str = os.getenv("HCLI_GITHUB_URL", "https://github.com/HexRaysSA/ida-hcli")

    # OAuth 2.1 / OIDC authorization server (Ory Hydra). The issuer's
    # .well-known document is used to discover the authorize/token endpoints,
    # so only the issuer, client_id and scope need configuring. Point these at
    # the .io environment for dev.
    HCLI_OAUTH_ISSUER: str = os.getenv("HCLI_OAUTH_ISSUER", "https://oauth.hex-rays.com")
    HCLI_OAUTH_CLIENT_ID: str = os.getenv("HCLI_OAUTH_CLIENT_ID", "74a4232c-af42-4974-90c4-3d0f8f83303d")
    HCLI_OAUTH_SCOPE: str = os.getenv("HCLI_OAUTH_SCOPE", "openid offline_access email profile licenses:read")
    # Out-of-band redirect for headless (--no-browser) logins: the portal consent
    # page renders the authorization code for the user to paste back. It is a
    # pre-registered OAuth redirect URI, so it must move in lockstep with the
    # client registration when pointing HCLI_OAUTH_ISSUER at another environment.
    HCLI_OAUTH_OOB_REDIRECT_URL: str = os.getenv("HCLI_OAUTH_OOB_REDIRECT_URL", f"{HCLI_PORTAL_URL}/oauth/consent")

    HCLI_VERSION: str = os.getenv("HCLI_VERSION", __version__)
    HCLI_BINARY_NAME: str = os.getenv("HCLI_BINARY_NAME", "hcli")
    # Namespace used for the stored auth keys (credentials, login.email). Defaults
    # to the binary name so each binary keeps its own login, but can be set
    # explicitly to let sibling binaries share a single credential store.
    HCLI_CONFIG_NAMESPACE: str = os.getenv("HCLI_CONFIG_NAMESPACE", HCLI_BINARY_NAME)
    HCLI_VERSION_EXTRA: str = os.getenv("HCLI_VERSION_EXTRA", "")

    HCLI_DISABLE_UPDATES: bool = _env_bool("HCLI_DISABLE_UPDATES")

    IDAUSR: str | None = _env_optional("IDAUSR")
    IDADIR: str | None = _env_optional("IDADIR")

    # IDA-specific environment variables
    HCLI_IDAUSR: str | None = _env_optional("HCLI_IDAUSR")
    HCLI_CURRENT_IDA_INSTALL_DIR: str | None = _env_optional("HCLI_CURRENT_IDA_INSTALL_DIR")
    HCLI_CURRENT_IDA_PLATFORM: str | None = _env_optional("HCLI_CURRENT_IDA_PLATFORM")
    HCLI_CURRENT_IDA_VERSION: str | None = _env_optional("HCLI_CURRENT_IDA_VERSION")
    HCLI_CURRENT_IDA_PYTHON_EXE: str | None = _env_optional("HCLI_CURRENT_IDA_PYTHON_EXE")
    IDAPYTHON_VENV_EXECUTABLE: str | None = _env_optional("IDAPYTHON_VENV_EXECUTABLE")

    # KE download settings
    HCLI_KE_DOWNLOADS_DIR: str | None = _env_optional("HCLI_KE_DOWNLOADS_DIR")
    HCLI_KE_DOWNLOADS_RETENTION_DAYS: int = _env_int("HCLI_KE_DOWNLOADS_RETENTION_DAYS", 3)
    # Allow KE deep links to download from private/loopback/link-local hosts. Off by
    # default so a clicked ida:// link cannot make hcli reach internal services; set
    # to true/yes/on/1 for self-hosted KE deployments on an internal network.
    HCLI_KE_ALLOW_PRIVATE_HOSTS: bool = _env_bool("HCLI_KE_ALLOW_PRIVATE_HOSTS")
    # Suppress the "open downloaded IDB in IDA?" confirmation prompt. The KE download
    # path is reachable from any web page, so by default hcli asks before handing
    # attacker-influenced content to IDA; set to true/yes/on/1 for one-click flows.
    HCLI_KE_SKIP_CONFIRM: bool = _env_bool("HCLI_KE_SKIP_CONFIRM")
    # Optional cap (in MB) on a single KE asset download; 0 means no limit. Downloads
    # always stream to disk regardless, so this only bounds total bytes written.
    # Clamp negatives to 0: a misconfigured "-1" must not silently disable the cap
    # (the consumer's `> 0` test would otherwise read a negative value as "no limit").
    HCLI_KE_MAX_DOWNLOAD_MB: int = max(0, _env_int("HCLI_KE_MAX_DOWNLOAD_MB", 0))


# Constants
CONFIG_API_KEY = "apiKey"
OAUTH_SERVER_PORT = 9999
# Loopback redirect for the browser flow. Ory Hydra matches redirect URIs
# exactly, so this must be pre-registered on the client verbatim (127.0.0.1,
# not localhost).
OAUTH_REDIRECT_URL = f"http://127.0.0.1:{OAUTH_SERVER_PORT}/callback"
