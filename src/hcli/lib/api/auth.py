"""Authentication API client."""

from pydantic import BaseModel

from .common import get_api_client


class AuthUser(BaseModel):
    """Authentication user information."""

    email: str


class AuthAPI:
    """Authentication API client."""

    async def whoami(self) -> AuthUser:
        """Get current user information."""
        client = await get_api_client()
        data = await client.get_json("/api/whoami")
        return AuthUser(**data)


# Global instance
auth = AuthAPI()
