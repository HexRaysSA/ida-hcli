"""Authentication-related constants."""

from datetime import datetime

from pydantic import BaseModel


class CredentialType:
    """Authentication type constants."""

    INTERACTIVE = "interactive"
    KEY = "key"

    # List of all valid auth types for validation
    VALID_TYPES = [INTERACTIVE, KEY]


class Credentials(BaseModel):
    """Authentication source data model."""

    name: str
    type: str  # AuthType.INTERACTIVE or AuthType.KEY
    email: str
    created_at: str
    last_used: str

    # Type-specific data
    token: str | None = None  # For INTERACTIVE type

    @classmethod
    def create_credentials(cls, name: str, credential_type: str, token: str, email: str | None = None) -> "Credentials":
        """Create a new API key credentials."""
        now = datetime.utcnow().isoformat() + "Z"

        return cls(name=name, type=credential_type, email=email or "", token=token, created_at=now, last_used=now)

    @property
    def label(self) -> str:
        """Computed label based on type."""
        if self.type == CredentialType.KEY:
            return f"{self.name} [{self.email}]"
        elif self.type == CredentialType.INTERACTIVE:
            return self.email

        return self.name  # fallback

    def update_last_used(self) -> None:
        """Update the last used timestamp."""
        self.last_used = datetime.utcnow().isoformat() + "Z"


class CredentialsConfig(BaseModel):
    """Complete credentials configuration."""

    default: str | None = None
    credentials: dict[str, Credentials] = {}

    def add_credentials(self, source: Credentials) -> None:
        """Add an credentials."""
        self.credentials[source.name] = source
        if self.default is None:
            self.default = source.name

    def remove_credentials(self, name: str) -> bool:
        """Remove an credentials. Returns True if removed."""
        if name in self.credentials:
            del self.credentials[name]
            if self.default == name:
                # Set new default to first remaining source
                self.default = next(iter(self.credentials.keys())) if self.credentials else None
            return True
        return False

    def get_default_credentials(self) -> Credentials | None:
        """Get the default credentials."""
        if self.default and self.default in self.credentials:
            return self.credentials[self.default]
        return None

    def set_default(self, name: str) -> bool:
        """Set the default credentials."""
        if name in self.credentials:
            self.default = name
            return True
        return False

    def find_credentials_by_email_and_type(self, email: str, credential_type: str) -> Credentials | None:
        """Find credentials by email and type. Returns the first match."""
        for credentials in self.credentials.values():
            if credentials.email == email and credentials.type == credential_type:
                return credentials
        return None


# Configuration keys
CONFIG_CREDENTIALS = "credentials"
