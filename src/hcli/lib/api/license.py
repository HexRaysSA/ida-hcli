"""License API client."""

from pydantic import BaseModel

from .common import get_api_client


class Product(BaseModel):
    """Product information."""

    id: int
    code: str
    name: str
    family: str | None = None
    catalog: str
    edition: str | None = None
    platform: str | None = None
    ui_label: str | None = None
    base_code: str | None = None
    update_code: str | None = None
    product_type: str
    product_subtype: str | None = None


class Addon(BaseModel):
    """License addon information."""

    id: int | None = None
    pubhash: str | None = None
    license_key: str | None = None
    seats: int | None = None
    password: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    product_code: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    activation_owner: str | None = None
    activation_owner_type: str | None = None
    product: "Product | None" = None


class Edition(BaseModel):
    """Edition information."""

    id: int | None = None
    tags: list[str] | None = None
    plan_id: str | None = None
    max_items: int | None = None
    plan_name: str | None = None
    edition_id: str | None = None
    edition_name: str | None = None
    build_edition_id: str | None = None
    build_product_id: str | None = None


class License(BaseModel):
    """License information."""

    id: int | None = None
    pubhash: str | None = None
    plan_id: str | None = None
    license_key: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    license_type: str | None = None
    seats: int | None = None
    password: str | None = None
    product_code: str | None = None
    customer_id: int | None = None
    end_customer_id: int | None = None
    activation_owner: str | None = None
    activation_owner_type: str | None = None
    status: str | None = None
    comment: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    generation_date: str | None = None
    download_date: str | None = None
    addons: "list[Addon] | None" = None
    edition: "Edition | None" = None
    asset_types: list[str] | None = None
    format_version: str | None = None
    product_catalog: str | None = None
    end_customer_visible: bool | None = None


class PagedResponse(BaseModel):
    """Paged response wrapper."""

    items: list[License]
    total: int


class LicenseAPI:
    """License API client."""

    async def get_licenses(self, customer_id: str) -> list[License]:
        """Get licenses for a customer."""
        try:
            client = await get_api_client()
            data = await client.get_json(f"/api/licenses/{customer_id}?page=1&limit=100")
            paged_response = PagedResponse(**data)

            # Sort licenses by end_date (descending, with null dates last)
            licenses = paged_response.items
            licenses.sort(key=lambda x: (x.end_date is None, x.end_date), reverse=True)

            return licenses
        except Exception as e:
            print(f"Exception occurred: {e}")
            return []

    async def download_license(
        self, customer_id: str, license_id: str, asset_type: str, target_dir: str = "./"
    ) -> str | None:
        """Download a license file."""
        client = await get_api_client()
        download_url = await client.get_json(f"/api/licenses/{customer_id}/download/{asset_type}/{license_id}")

        if download_url:
            return await client.download_file(download_url, target_dir=target_dir, auth=False)

        return None


# Global instance
license = LicenseAPI()
