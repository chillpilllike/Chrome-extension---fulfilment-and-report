from __future__ import annotations

from typing import Any, Optional, Union

from pydantic import BaseModel, Field


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
    limit: int = 0
    batch_size: int = 0
    refresh: bool = True


class PlacePayload(BaseModel):
    store_id: int
    address_id: Optional[int] = None
    amazon_account_id: Optional[int] = None
    line_ids: list[int] = []
    club: bool = False
    ordering_engine: str = ""
    allow_missing_spaid: bool = False
    include_missing_asins: bool = False


class ShopifyFulfilmentPushPayload(BaseModel):
    store_id: int
    line_ids: list[int] = []


class ChromeJobCompletePayload(BaseModel):
    amazon_order_id: str = ""
    amazon_order_url: str = ""
    amazon_account_name: str = ""
    order_date: str = ""
    amazon_recipient: str = ""
    amazon_asins: list[str] = []
    line_ids: list[int] = []
    order_mappings: list[dict[str, Any]] = []
    pricing_summary: list[dict[str, Any]] = []
    worker_id: str = ""


class ChromeJobFailPayload(BaseModel):
    message: str
    line_ids: list[int] = []
    missing_asin: str = ""
    missing_line_id: Optional[int] = None
    failure_code: str = ""
    requested_quantity: Optional[float] = None
    fulfilled_quantity: Optional[float] = None
    available_quantity: Optional[float] = None
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
    amazon_account_name: str = ""
    packages: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    recipient: str = ""
    order_status: str = ""
    order_date: str = ""
    otp: str = ""
    payment_revision_needed: bool = False
    payment_revision_url: str = ""
    order_cancelled: bool = False
    cancellation_message: str = ""
    page_text: str = ""
    alert_html: str = ""


class DispatchScanPayload(BaseModel):
    scan_code: str
    store_id: Optional[int] = None
    operator: str = ""
    alias_codes: list[str] = Field(default_factory=list)


class DispatchPlacePayload(BaseModel):
    tote_code: str = ""
    status: str = "sorted_holding"
    exception_reason: str = ""
    operator: str = ""


class AmazonHistoryOrderPayload(BaseModel):
    amazon_order_id: str
    amazon_order_url: str = ""
    recipient: str = ""
    status: str = ""
    order_date: str = ""
    asins: list[str] = []
    items: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    asin_quantities: dict[str, float] = {}
    cancelled: bool = False


class AmazonHistoryLookupPayload(BaseModel):
    orders: list[AmazonHistoryOrderPayload] = []
    amazon_order_ids: list[str] = []


class ManualAmazonOrderMatchPayload(BaseModel):
    amazon_order_id: str
    amazon_order_url: str = ""
    amazon_account_name: str = ""
    order_date: str = ""
    order_names: list[str] = []
    line_ids: list[int] = []
    source_text: str = ""
    store_id: Optional[int] = None
    replace_existing: bool = False


class ManualFulfilmentPayload(BaseModel):
    store_id: int
    line_ids: list[int] = []
    reference: str = ""
    url: str = ""
    third_party: bool = False
    total_cost: float = 0
    estimated_delivery_at: str = ""


class PackagePickupCountPayload(BaseModel):
    store_id: Optional[int] = None
    pickup_date: str
    amazon_picked_up: int = Field(default=0, ge=0, le=100000)
    non_amazon_picked_up: int = Field(default=0, ge=0, le=100000)
    amazon_unreported_count: int = Field(default=0, ge=0, le=100000)
    non_amazon_order_numbers: list[str] = Field(default_factory=list)


class PackagePickupBulkRowPayload(BaseModel):
    source_id: Optional[int] = None
    package_type: str
    odoo_order_name: str = ""
    tracking_input: str = ""


class PackagePickupBulkPayload(BaseModel):
    store_id: Optional[int] = None
    pickup_date: str
    rows: list[PackagePickupBulkRowPayload] = Field(default_factory=list)


class PackagePickupManualAmazonPayload(BaseModel):
    store_id: Optional[int] = None
    pickup_date: str
    odoo_order_name: str
    tracking_input: str


class PackagePickupReceivedPayload(BaseModel):
    source_type: str
    source_id: int
    status: str = ""
    received: Optional[bool] = None


class PackagePickupSettingsPayload(BaseModel):
    pickup_time: str


class PackagePickupScanPayload(BaseModel):
    scan_code: str
    alias_codes: list[str] = Field(default_factory=list)
    store_id: Optional[int] = None


class PackagePickupDeletePayload(BaseModel):
    source_type: str
    source_id: int


class ChromeJobHeartbeatPayload(BaseModel):
    worker_id: str = ""
    stage: str = ""
    item_index: int = 0
    item_count: int = 0
    asins: list[str] = []
    target_window_id: Optional[int] = None
    extension_build: str = ""
    paused: bool = False
    paused_stage: str = ""
    last_error: str = ""


class ChromeJobResetPayload(BaseModel):
    worker_id: str = ""
    line_ids: list[int] = []


class ChromeBrowserlessRunPayload(BaseModel):
    worker_id: str = ""
    store_id: Optional[int] = None
    ordering_engine: str = "chrome_browserless"
    split_mixed_asin: bool = False
    max_jobs: int = 0


class ChromeTrackingBrowserlessRunPayload(BaseModel):
    worker_id: str = ""
    store_id: Optional[int] = None
    max_orders: int = 0
    amazon_order_ids: list[str] = Field(default_factory=list)


class EpostBrowserlessRunPayload(BaseModel):
    worker_id: str = ""
    store_id: Optional[int] = None
    interval_days: int = 1
    interval_hours: int = 24
    max_batches: int = 0
    include_recent: bool = True


class EpostTrackingUpdatePayload(BaseModel):
    results: list[dict[str, Any]] = []


class EpostSyncPayload(BaseModel):
    store_id: Optional[int] = None
    days: int = 30


class EpostRefundPayload(BaseModel):
    status: str


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
    ordering_engine: str = ""


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


class BackupKeyPayload(BaseModel):
    key: str


class AdminSettingsPayload(BaseModel):
    admin_access_token: str


class UiCopyPayload(BaseModel):
    key: str
    title: str = ""
    description: str = ""
    icon: str = ""
