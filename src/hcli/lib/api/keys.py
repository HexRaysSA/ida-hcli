"""API Keys management client."""

from pydantic import BaseModel

from .common import get_api_client


class ApiKey(BaseModel):
    """API key information."""

    name: str
    created_at: str
    last_used_at: str | None
    request_count: int


class ApiKeyToken(BaseModel):
    """API key token response."""

    key: str


class KeysAPI:
    """API Keys management client."""

    async def get_keys(self) -> list[ApiKey]:
        """Get all API keys for the current user."""
        client = await get_api_client()
        data = await client.get_json("/api/keys")
        return [ApiKey(**item) for item in data]

    async def create_key(self, name: str) -> str:
        """Create a new API key."""
        client = await get_api_client()
        data = await client.post_json("/api/keys", {"name": name})
        token = ApiKeyToken(**data)
        return token.key

    async def revoke_key(self, name: str) -> None:
        """Revoke an API key."""
        client = await get_api_client()
        await client.post_json(f"/api/keys/revoke/{name}", {})


# Global instance
keys = KeysAPI()
