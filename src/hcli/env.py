import os

from . import __version__


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable using one consistent truthy set."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "yes", "on", "1")


class ENV:
    """Environment configuration mirroring the Deno version."""

    HCLI_API_KEY: str | None = os.getenv("HCLI_API_KEY")
    HCLI_DEBUG: bool = _env_bool("HCLI_DEBUG")
    HCLI_API_URL: str = os.getenv("HCLI_API_URL", "https://api.eu.hex-rays.com")
    HCLI_CLOUD_URL: str = os.getenv("HCLI_CLOUD_URL", "https://api.hcli.run")
    HCLI_PORTAL_URL: str = os.getenv("HCLI_PORTAL_URL", "https://my.hex-rays.com")
    HCLI_RELEASE_URL: str = os.getenv("HCLI_RELEASE_URL", "https://hcli.docs.hex-rays.com")

    # GitHub integration
    HCLI_GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    HCLI_GITHUB_API_URL: str = os.getenv("GITHUB_API_URL", "https://api.github.com")
    HCLI_GITHUB_URL: str = os.getenv("HCLI_GITHUB_URL", "https://github.com/HexRaysSA/ida-hcli")

    HCLI_SUPABASE_ANON_KEY: str = os.getenv(
        "HCLI_SUPABASE_ANON_KEY",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF0aGF3ZXRjYW9zb2Zyd29vaXhsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjYxNDAxNzYsImV4cCI6MjA0MTcxNjE3Nn0.cOkB4DJ-jeT2aSItfSFsk2C6wtJ2f1UfErWzsf8144o",
    )
    HCLI_SUPABASE_URL: str = os.getenv("HCLI_SUPABASE_URL", "https://auth.hex-rays.com")

    HCLI_VERSION: str = os.getenv("HCLI_VERSION", __version__)
    HCLI_BINARY_NAME: str = os.getenv("HCLI_BINARY_NAME", "hcli")
    HCLI_VERSION_EXTRA: str = os.getenv("HCLI_VERSION_EXTRA", "")
    HCLI_MODE: str = os.getenv("HCLI_MODE", "user")
    QUIET: bool = False

    HCLI_DISABLE_UPDATES: bool = _env_bool("HCLI_DISABLE_UPDATES")

    IDAUSR: str | None = os.getenv("IDAUSR")
    IDADIR: str | None = os.getenv("IDADIR")

    # IDA-specific environment variables
    HCLI_IDAUSR: str | None = os.getenv("HCLI_IDAUSR")
    HCLI_CURRENT_IDA_INSTALL_DIR: str | None = os.getenv("HCLI_CURRENT_IDA_INSTALL_DIR")
    HCLI_CURRENT_IDA_PLATFORM: str | None = os.getenv("HCLI_CURRENT_IDA_PLATFORM")
    HCLI_CURRENT_IDA_VERSION: str | None = os.getenv("HCLI_CURRENT_IDA_VERSION")
    HCLI_CURRENT_IDA_PYTHON_EXE: str | None = os.getenv("HCLI_CURRENT_IDA_PYTHON_EXE")

    # KE download settings
    HCLI_KE_DOWNLOADS_DIR: str | None = os.getenv("HCLI_KE_DOWNLOADS_DIR")
    HCLI_KE_DOWNLOADS_RETENTION_DAYS: int = int(os.getenv("HCLI_KE_DOWNLOADS_RETENTION_DAYS", "3"))
    # Allow KE deep links to download from private/loopback/link-local hosts. Off by
    # default so a clicked ida:// link cannot make hcli reach internal services; set
    # to true/yes/on/1 for self-hosted KE deployments on an internal network.
    HCLI_KE_ALLOW_PRIVATE_HOSTS: bool = _env_bool("HCLI_KE_ALLOW_PRIVATE_HOSTS")


# Constants
CONFIG_API_KEY = "apiKey"
OAUTH_REDIRECT_URL = "http://localhost:9999/callback"
OAUTH_SERVER_PORT = 9999
