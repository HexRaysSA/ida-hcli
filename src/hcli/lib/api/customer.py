"""Customer API client."""

from pydantic import BaseModel

from .common import get_api_client


class Customer(BaseModel):
    id: int | None = None
    email: str | None = None
    notes: str | None = None
    company: str | None = None
    country: str | None = None
    last_name: str | None = None
    created_at: str | None = None
    first_name: str | None = None
    updated_at: str | None = None
    vat_number: str | None = None
    acquired_at: str | None = None
    cf_reseller: bool | None = None
    cf_coupon_id: str | None = None
    cf_kyc_status: str | None = None
    net_term_days: int | None = None
    cf_customer_key: str | None = None
    cf_gdpr_deleted: bool | None = None
    cf_customer_category: str | None = None
    chargebee_customer_id: str | None = None
    cf_notifications_enabled: bool | None = None


class CustomerAPI:
    """Customer API client."""

    async def get_customers(self) -> list[Customer]:
        """Get all customers."""
        client = await get_api_client()
        data = await client.get_json("/api/customers")
        return [Customer(**item) for item in data]


# Global instance
customer = CustomerAPI()
