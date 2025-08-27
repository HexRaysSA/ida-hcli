import os

from . import __version__


class ENV:
    """Environment configuration mirroring the Deno version."""

    HCLI_API_KEY: str | None = os.getenv("HCLI_API_KEY")
    HCLI_DEBUG: bool = os.getenv("HCLI_DEBUG", "").lower() in ("true", "yes", "on", "1")
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

    HCLI_DISABLE_UPDATES: bool = os.getenv("HCLI_DISABLE_UPDATES", "").lower() in ("true", "yes", "on", "1")

    # IDA-specific environment variables
    HCLI_IDAUSR: str | None = os.getenv("HCLI_IDAUSR")
    HCLI_CURRENT_IDA_INSTALL_DIR: str | None = os.getenv("HCLI_CURRENT_IDA_INSTALL_DIR")
    HCLI_CURRENT_IDA_PLATFORM: str | None = os.getenv("HCLI_CURRENT_IDA_PLATFORM")
    HCLI_CURRENT_IDA_VERSION: str | None = os.getenv("HCLI_CURRENT_IDA_VERSION")
    HCLI_CURRENT_IDA_PYTHON_EXE: str | None = os.getenv("HCLI_CURRENT_IDA_PYTHON_EXE")


# Constants
CONFIG_API_KEY = "apiKey"
OAUTH_REDIRECT_URL = "http://localhost:9999/callback"
OAUTH_SERVER_PORT = 9999
