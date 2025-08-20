"""API module index - main entry point for all API functionality."""

from .auth import auth
from .common import get_api_client
from .customer import customer
from .keys import keys
from .license import license


class API:
    """Main API class that provides access to all API modules."""

    def __init__(self):
        self.auth = auth
        self.license = license
        self.customer = customer
        self.keys = keys

    async def download_file(self, *args, **kwargs):
        """Convenience method to download files."""
        client = await get_api_client()
        return await client.download_file(*args, **kwargs)


# Global API instance
api = API()

# Export individual modules for convenience
__all__ = [
    "api",
    "auth",
    "license",
    "customer",
    "keys",
    "get_api_client",
]
