"""Customer API client."""

from typing import List, Optional

from pydantic import BaseModel

from .common import get_api_client


class Customer(BaseModel):
    id: Optional[int] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    company: Optional[str] = None
    country: Optional[str] = None
    last_name: Optional[str] = None
    created_at: Optional[str] = None
    first_name: Optional[str] = None
    updated_at: Optional[str] = None
    vat_number: Optional[str] = None
    acquired_at: Optional[str] = None
    cf_reseller: Optional[bool] = None
    cf_coupon_id: Optional[str] = None
    cf_kyc_status: Optional[str] = None
    net_term_days: Optional[int] = None
    cf_customer_key: Optional[str] = None
    cf_gdpr_deleted: Optional[bool] = None
    cf_customer_category: Optional[str] = None
    chargebee_customer_id: Optional[str] = None
    cf_notifications_enabled: Optional[bool] = None


class CustomerAPI:
    """Customer API client."""

    async def get_customers(self) -> List[Customer]:
        """Get all customers."""
        client = await get_api_client()
        data = await client.get_json("/api/customers")
        return [Customer(**item) for item in data]


# Global instance
customer = CustomerAPI()
