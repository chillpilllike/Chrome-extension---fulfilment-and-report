from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel


class StorePayload(BaseModel):
    name: str
    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_password: str
    website_id: Optional[Union[str, int]] = None


class AddressPayload(BaseModel):
    label: str
    company_name: str = "Nutricity"
    phone_number: str = ""
    address_line1: str
    address_line2: str = ""
    address_line3: str = ""
    city: str
    state_or_region: str = ""
    postal_code: str
    country_code: str = "US"
    is_default: bool = False


class AmazonAccountPayload(BaseModel):
    name: str
    api_base_url: str = "https://na.business-api.amazon.com"
    tracking_api_base_url: str = "https://na.business-api.amazon.com"
    lwa_token_url: str = "https://api.amazon.com/auth/o2/token"
    lwa_client_id: str = ""
    lwa_client_secret: str = ""
    lwa_refresh_token: str = ""
    api_access_token: str = ""
    buyer_email: Optional[str] = ""
    buying_group_id: Optional[str] = ""
    product_region: Optional[str] = "US"
    locale: Optional[str] = "en_US"
    cxml_from_identity: Optional[str] = ""
    cxml_shared_secret: Optional[str] = ""
    cxml_po_url: Optional[str] = ""
    cxml_punchout_url: Optional[str] = ""
    cxml_punchout_test_url: Optional[str] = ""
    cxml_auth_mode: Optional[str] = "header"
    cxml_cart_session_id: Optional[str] = ""
    cxml_credential_domain: Optional[str] = "NetworkId"
    cxml_to_identity: Optional[str] = "Amazon"
    is_default: bool = False


class PullPayload(BaseModel):
    store_id: Optional[int] = None
    store_ids: list[int] = []
    days: int = 7
    limit: int = 50
    refresh: bool = True


class PlacePayload(BaseModel):
    store_id: int
    address_id: Optional[int] = None
    amazon_account_id: Optional[int] = None
    line_ids: list[int] = []
    club: bool = False
    ordering_engine: str = "rest"
    allow_missing_spaid: bool = False


class ChromeJobCompletePayload(BaseModel):
    amazon_order_id: str = ""
    amazon_order_url: str = ""
    amazon_account_name: str = ""
    line_ids: list[int] = []
    pricing_summary: list[dict[str, Any]] = []
    worker_id: str = ""


class ChromeJobFailPayload(BaseModel):
    message: str
    line_ids: list[int] = []
    missing_asin: str = ""
    missing_line_id: Optional[int] = None
    worker_id: str = ""


class ChromeJobCostlyPayload(BaseModel):
    message: str = ""
    line_ids: list[int] = []
    costly_asin: str = ""
    costly_line_id: Optional[int] = None
    store_total_price: float = 0
    amazon_total_price: float = 0
    worker_id: str = ""


class ChromeTrackingUpdatePayload(BaseModel):
    amazon_order_id: str
    amazon_order_url: str = ""
    packages: list[dict[str, Any]] = []


class ManualAmazonOrderMatchPayload(BaseModel):
    amazon_order_id: str
    amazon_order_url: str = ""
    amazon_account_name: str = ""
    order_names: list[str] = []
    source_text: str = ""
    store_id: Optional[int] = None


class ChromeJobHeartbeatPayload(BaseModel):
    worker_id: str = ""


class EpostTrackingUpdatePayload(BaseModel):
    results: list[dict[str, Any]] = []


class EpostSyncPayload(BaseModel):
    store_id: int
    days: int = 2


class ExportCreatePayload(BaseModel):
    view: str
    store_id: Optional[int] = None
    columns: list[dict[str, str]] = []
    select_all: bool = False
    selected_ids: list[Union[int, str]] = []
    filters: dict[str, Any] = {}


class StoreActionPayload(BaseModel):
    store_id: int


class InventoryCreatePayload(BaseModel):
    store_id: int
    asin: str
    quantity: float = 1
    product_name: str = ""
    notes: str = ""


class DeleteLinesPayload(BaseModel):
    store_id: int
    line_ids: list[int] = []


class LineSpaidPayload(BaseModel):
    store_id: int
    supplier_part_auxiliary_id: str = ""


class ReplacementPayload(BaseModel):
    store_id: int
    asin: str
    note: str = ""


class BulkPlacePayload(BaseModel):
    store_id: int
    line_ids: list[int]
    address_id: Optional[int] = None
    amazon_account_id: Optional[int] = None
    ordering_engine: str = "rest"


class CostlyApprovalPayload(BaseModel):
    store_id: int
    line_ids: list[int] = []


class PunchoutReturnUrlPayload(BaseModel):
    label: str
    url: str
    is_default: bool = False


class EnginePayload(BaseModel):
    ordering_engine: str


class ServiceSettingsPayload(BaseModel):
    settings: dict[str, str] = {}


class AdminSettingsPayload(BaseModel):
    admin_access_token: str
