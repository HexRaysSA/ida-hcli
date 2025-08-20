"""License API client."""

from typing import List, Optional

from pydantic import BaseModel

from .common import get_api_client


class Product(BaseModel):
    """Product information."""

    id: int
    code: str
    name: str
    family: Optional[str] = None
    catalog: str
    edition: Optional[str] = None
    platform: Optional[str] = None
    ui_label: Optional[str] = None
    base_code: Optional[str] = None
    update_code: Optional[str] = None
    product_type: str
    product_subtype: Optional[str] = None


class Addon(BaseModel):
    """License addon information."""

    id: Optional[int] = None
    pubhash: Optional[str] = None
    license_key: Optional[str] = None
    seats: Optional[int] = None
    password: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    product_code: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    activation_owner: Optional[str] = None
    activation_owner_type: Optional[str] = None
    product: Optional["Product"] = None


class Edition(BaseModel):
    """Edition information."""

    id: Optional[int] = None
    tags: Optional[List[str]] = None
    plan_id: Optional[str] = None
    max_items: Optional[int] = None
    plan_name: Optional[str] = None
    edition_id: Optional[str] = None
    edition_name: Optional[str] = None
    build_edition_id: Optional[str] = None
    build_product_id: Optional[str] = None


class License(BaseModel):
    """License information."""

    id: Optional[int] = None
    pubhash: Optional[str] = None
    plan_id: Optional[str] = None
    license_key: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    license_type: Optional[str] = None
    seats: Optional[int] = None
    password: Optional[str] = None
    product_code: Optional[str] = None
    customer_id: Optional[int] = None
    end_customer_id: Optional[int] = None
    activation_owner: Optional[str] = None
    activation_owner_type: Optional[str] = None
    status: Optional[str] = None
    comment: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    generation_date: Optional[str] = None
    download_date: Optional[str] = None
    addons: Optional[List["Addon"]] = None
    edition: Optional["Edition"] = None
    asset_types: Optional[List[str]] = None
    format_version: Optional[str] = None
    product_catalog: Optional[str] = None
    end_customer_visible: Optional[bool] = None


class PagedResponse(BaseModel):
    """Paged response wrapper."""

    items: List[License]
    total: int


class LicenseAPI:
    """License API client."""

    async def get_licenses(self, customer_id: str) -> List[License]:
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
    ) -> Optional[str]:
        """Download a license file."""
        client = await get_api_client()
        download_url = await client.get_json(f"/api/licenses/{customer_id}/download/{asset_type}/{license_id}")

        if download_url:
            return await client.download_file(download_url, target_dir=target_dir, auth=False)

        return None


# Global instance
license = LicenseAPI()
