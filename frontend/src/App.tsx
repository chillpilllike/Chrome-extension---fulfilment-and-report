import { useEffect, useMemo, useRef, useState } from "react"
import type { DragEvent, KeyboardEvent as ReactKeyboardEvent, MouseEvent, ReactNode } from "react"
import {
  IconAlertCircle as AlertCircle,
  IconBell as Bell,
  IconBuildingStore as StoreIcon,
  IconCheck as Check,
  IconCircleCheck as CheckCircle2,
  IconChevronDown as ChevronDown,
  IconChevronLeft as ChevronLeft,
  IconChevronRight as ChevronRight,
  IconColumns3 as Columns3,
  IconCopy as Copy,
  IconDatabase as Database,
  IconDownload as Download,
  IconEdit as Edit,
  IconExternalLink as ExternalLink,
  IconGripVertical as GripVertical,
  IconHome as Home,
  IconLink as Link,
  IconLock as Lock,
  IconMoon as Moon,
  IconPackage as PackageCheck,
  IconPlus as Plus,
  IconRefresh as RefreshCw,
  IconSearch as Search,
  IconSettings as Settings,
  IconSun as Sun,
  IconShoppingCart as ShoppingCart,
  IconTrash as Trash2,
  IconLogout as Logout,
  IconUserCircle as UserCircle,
} from "@tabler/icons-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

type SelectId = number | string
const KNOWN_APP_PAGES = new Set([
  "home",
  "orders",
  "pull-jobs",
  "chrome-queue",
  "tracking",
  "payment-failed",
  "amazon-otp",
  "epost",
  "duplicate-tracking",
  "fulfilment-pending",
  "missing",
  "back-in-stock",
  "partial-fulfilments",
  "bulk",
  "costly",
  "profit-loss",
  "accounting",
  "downloads",
  "shopify-fulfilment",
  "shopify-tracking",
  "inventory",
  "cancelled-orders",
  "settings",
])

function appPageFromLocation() {
  if (typeof window === "undefined") return "home"
  const path = window.location.pathname.replace(/^\/+|\/+$/g, "")
  const page = path || "home"
  return KNOWN_APP_PAGES.has(page) ? page : "home"
}

function pagePath(page: string) {
  return page === "home" ? "/" : `/${page}`
}

function rangeSelection<T extends SelectId>(
  visibleIds: T[],
  selected: T[],
  id: T,
  checked: boolean,
  shiftKey: boolean,
  anchor: T | null,
) {
  let ids = [id]
  if (shiftKey && anchor !== null) {
    const start = visibleIds.indexOf(anchor)
    const end = visibleIds.indexOf(id)
    if (start >= 0 && end >= 0) {
      const [from, to] = start < end ? [start, end] : [end, start]
      ids = visibleIds.slice(from, to + 1)
    }
  }
  return checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((current) => !ids.includes(current))
}

type Store = {
  id: number
  name: string
  odoo_url: string
  odoo_db: string
  odoo_user: string
  odoo_password: string
  website_id?: number | string | null
}

type Address = {
  id: number
  label: string
  company_name: string
  phone_number: string
  address_line1: string
  address_line2: string
  address_line3: string
  city: string
  state_or_region: string
  postal_code: string
  country_code: string
  is_default: number
}

type AmazonAccount = {
  id: number
  name: string
  api_base_url: string
  tracking_api_base_url: string
  lwa_token_url: string
  lwa_client_id: string
  lwa_client_secret: string
  lwa_refresh_token: string
  api_access_token: string
  buyer_email: string
  buying_group_id: string
  product_region: string
  locale: string
  cxml_from_identity: string
  cxml_shared_secret: string
  cxml_po_url: string
  cxml_punchout_url: string
  cxml_punchout_test_url: string
  cxml_auth_mode: string
  cxml_cart_session_id: string
  cxml_credential_domain: string
  cxml_to_identity: string
  is_default: number
}

type OrderLine = {
  id: number
  store_id: number
  store_name?: string
  odoo_order_id?: number
  odoo_order_name: string
  odoo_order_date: string
  odoo_order_url: string
  destination_country?: string
  destination_country_code?: string
  destination_country_name?: string
  product_name: string
  default_code: string
  asin: string
  missing_asin?: string
  missing_asin_url?: string
  original_asin?: string
  original_product_name?: string
  replacement_asin?: string
  replacement_product_name?: string
  replacement_note?: string
  replacement_assigned_at?: string
  cost_review_loss?: number
  cost_approved_at?: string
  asin_url?: string
  replacement_asin_url?: string
  supplier_part_auxiliary_id: string
  quantity: number
  state: string
  odoo_status_label: string
  amazon_status?: string
  amazon_order_id: string
  amazon_order_url: string
  shopify_order_id?: string
  shopify_order_name?: string
  shopify_order_url?: string
  shopify_financial_status?: string
  shopify_fulfillment_status?: string
  shopify_fulfillment_at?: string
  amazon_account_name: string
  order_engine: string
  tracking_status: string
  tracking_payload: string
  amazon_cancelled_at?: string
  amazon_cancelled_order_id?: string
  fulfilment_note: string
  last_error: string
  pulled_at: string
  ordered_at: string
  created_at: string
  updated_at: string
  duplicate_asin_count: number
  odoo_order_distinct_asin_count: number
  inventory_quantity: number
}

type DuplicateAsin = {
  asin: string
  asin_url?: string
  line_count: number
  order_count: number
  total_quantity: number
  orders: string
}

type TrackingOrder = {
  amazon_order_id: string
  amazon_order_url: string
  odoo_order_names: string[]
  tracking_status: string
  tracking_checked_at: string
  amazon_cancelled_at?: string
  amazon_cancelled_order_id?: string
  lines: OrderLine[]
}

type PaymentFailure = {
  amazon_order_id: string
  amazon_order_url: string
  revise_payment_url: string
  action_url: string
  store_id?: number
  odoo_order_names: string[]
  message: string
  status: string
  detected_at: string
  updated_at: string
  resolved_at?: string
}

type FulfilmentPendingRow = OrderLine & {
  store_name: string
  fulfilment_status: string
  message: string
  picking_names: string[]
  picking_states: string[]
  open_picking_names: string[]
  odoo_sale_state: string
  odoo_invoice_status: string
  tracking_checked_at: string
}

type PartialFulfilment = {
  id: number
  store_id: number
  odoo_order_id: number
  odoo_order_name: string
  odoo_order_url?: string
  amazon_orders?: Array<{ amazon_order_id: string; amazon_order_url?: string; amazon_account_name?: string }>
  amazon_order_ids?: string[]
  amazon_group_key?: string
  missing_line_ids: number[]
  missing_asins: string[]
  missing_asin_urls?: Record<string, string>
  remaining_line_ids: number[]
  message?: string
  status: string
  created_at?: string
  updated_at?: string
  processed_at?: string
}

type EpostTrackingRow = {
  id: number
  store_id: number
  store_name: string
  odoo_order_id: number
  odoo_order_name: string
  odoo_order_url: string
  amazon_order_id: string
  amazon_order_url: string
  picking_name: string
  tracking_code: string
  tracking_url: string
  status: string
  last_update_at: string
  location: string
  destination: string
  awb: string
  last_checked_at: string
  epost_status: string
  shipping_fee: number
  fulfilment_fee: number
  shipping_total: number
  shipping_match_type: string
}

type ShippingChargeRow = {
  id: number
  import_filename: string
  import_month: string
  odoo_order_name: string
  shipment_date: string
  tracking_number: string
  carrier: string
  service: string
  quantity: number
  shipping_fee: number
  fulfilment_fee: number
  total_cost: number
  matched_line_count: number
  created_at: string
}

type DuplicateTrackingRow = {
  id: string
  tracking_code: string
  tracking_url: string
  duplicate_reason: string
  epost_row_count: number
  shipping_row_count: number
  order_count: number
  odoo_order_names: string[]
  amazon_order_ids: string[]
  statuses: string[]
  destinations: string[]
  shipping_fee: number
  fulfilment_fee: number
  shipping_total: number
  invoice_count: number
  documents: AccountingDocument[]
  epost_rows: EpostTrackingRow[]
  shipping_rows: ShippingChargeRow[]
  last_seen_at: string
}

type AmazonOtpRow = {
  id: number
  amazon_order_id: string
  amazon_order_url: string
  otp: string
  tracking_url: string
  tracking_numbers: string
  carriers: string
  tracking_status: string
  match_status: string
  product_summary: string
  recipient: string
  store_names: string
  odoo_order_names: string
  otp_email_date: string
  dispatch_email_date: string
  tracking_checked_at: string
  updated_at: string
}

type InventoryItem = {
  id: number
  store_id: number
  asin: string
  quantity: number
  product_name: string
  source_odoo_order_name: string
  amazon_order_id: string
  amazon_order_url: string
  amazon_account_name: string
  source_type: string
  reserved_order_line_id: number | null
  notes: string
  status: string
  updated_at: string
}

type CancelledOrderRow = OrderLine & {
  inventory_item_id?: number
  inventory_status?: string
  inventory_updated_at?: string
}

type BackInStockRow = {
  id: number
  order_line_id: number
  store_id: number
  asin: string
  asin_url?: string
  product_name: string
  odoo_order_id: number
  odoo_order_name: string
  odoo_order_url?: string
  status: string
  availability_message: string
  price?: number
  checked_url: string
  checked_by: string
  queued_at: string
  first_seen_at: string
  last_checked_at: string
  order_state?: string
  amazon_group_key?: string
  replacement_asin?: string
  amazon_order_id?: string
}

type BulkGroup = {
  asin: string
  asin_url: string
  quantity: number
  order_names: string[]
  line_ids: number[]
  product_names: string[]
  has_missing_order: boolean
}

type ChromeQueueItem = {
  asin: string
  quantity: number
  product_name?: string
  line_id?: number
}

type ChromeQueueJob = {
  group_key: string
  store_id?: number
  store_name?: string
  amazon_status?: string
  back_in_stock?: boolean
  order_names?: string[]
  recipient_name?: string
  items?: ChromeQueueItem[]
  claimed_by?: string
  claimed_at?: string
  claim_expires_at?: string
  last_error?: string
  updated_at?: string
}

type ChromeQueueCount = {
  state: string
  count: number
  locked?: number
}

type DashboardData = {
  stores: Store[]
  current_store_id: number | null
  rows: OrderLine[]
  page: number
  per_page: number
  total: number
  counts: { state: string; count: number }[]
  addresses: Address[]
  amazon_accounts: AmazonAccount[]
  punchout_return_urls: PunchoutReturnUrl[]
  duplicate_asins: DuplicateAsin[]
  duplicate_asins_page?: number
  duplicate_asins_per_page?: number
  duplicate_asins_total?: number
  tracking_orders?: TrackingOrder[]
  default_ordering_engine: string
  pull_orders_days?: number
  pull_orders_limit?: number
  pull_orders_batch_size?: number
  message?: string
  ok?: boolean
  punchout_launch_url?: string
}

type ModalState = { title: string; message: string; ok: boolean } | null

const ADMIN_TOKEN_STORAGE_KEY = "admin_access_token"
const PULL_DAYS_STORAGE_KEY = "pull_orders_days"
const PULL_LIMIT_STORAGE_KEY = "pull_orders_limit"
const PULL_STORE_IDS_STORAGE_KEY = "pull_orders_store_ids"
const DASHBOARD_CACHE_STORAGE_KEY = "fulfilment.dashboard.cache.v1"
const DASHBOARD_CACHE_MAX_AGE_MS = 10 * 60 * 1000
const PROFIT_LOSS_PERIOD_STORAGE_KEY = "profit_loss_period"
const PROFIT_LOSS_CACHE_PREFIX = "profit_loss.cache."
const PROFIT_LOSS_CACHE_MAX_AGE_MS = 5 * 60 * 1000

const defaultUiCopy: UiCopy = {
  app_header: {
    title: "Amazon Business Fulfilment",
    description: "Odoo ASIN orders, Amazon ordering, delivery tracking.",
    icon: "package",
  },
  home: { title: "Dashboard", description: "Control panel overview for fulfilment, tracking, and exceptions." },
  orders: { title: "Orders", description: "Review Odoo order lines and queue Amazon fulfilment." },
  "pull-jobs": { title: "Pull Jobs", description: "Monitor background Odoo order imports." },
  "chrome-queue": { title: "Chrome Queue", description: "Review Chrome extension jobs and release stale locks." },
  tracking: { title: "Amazon Tracking", description: "Review package tracking captured from Amazon." },
  "payment-failed": { title: "Payment Failed", description: "Review Amazon orders that need payment revision." },
  "amazon-otp": { title: "Amazon OTP", description: "Match OTP emails to Amazon and Odoo orders." },
  epost: { title: "ePost Tracking", description: "Monitor ePost Global shipment events." },
  "duplicate-tracking": { title: "Duplicate Tracking", description: "Review repeated tracking numbers before dispatch updates." },
  "fulfilment-pending": { title: "Pending Dispatch", description: "Find Amazon-delivered orders still pending in Odoo." },
  missing: { title: "Missing ASINs", description: "Resolve unavailable Amazon products and replacements." },
  "back-in-stock": { title: "Back In Stock", description: "Review missing ASINs that the Chrome extension found available again." },
  "partial-fulfilments": { title: "Partial Fulfilments", description: "Review orders split between Amazon fulfilment and Missing ASINs." },
  bulk: { title: "Bulk Ordering", description: "Group compatible items before ordering." },
  costly: { title: "Cost Review", description: "Approve or replace items with unfavorable cost." },
  "profit-loss": { title: "Profit / Loss", description: "Review fulfilment profitability and shipping costs." },
  accounting: { title: "Accounting", description: "Manage invoices, credit notes, and supporting documents." },
  downloads: { title: "Downloads", description: "Export filtered data and download generated files." },
  "shopify-fulfilment": { title: "Shopify Fulfilment", description: "Queue Amazon-ordered Odoo sales into DTC or DTB Shopify fulfilment." },
  "shopify-tracking": { title: "Shopify Tracking to Odoo", description: "Sync Shopify tracking codes back to matching Odoo deliveries." },
  inventory: { title: "Inventory", description: "Use available stock before placing Amazon orders." },
  "cancelled-orders": { title: "Cancelled Orders", description: "Find cancelled Odoo orders that already have Amazon orders." },
  settings: { title: "Settings", description: "Configure stores, accounts, automation, alerts, and integrations." },
}

const uiIconComponents = {
  alert: AlertCircle,
  bell: Bell,
  database: Database,
  home: Home,
  package: PackageCheck,
  search: Search,
  settings: Settings,
  shop: StoreIcon,
  user: UserCircle,
}

class AdminAuthError extends Error {
  constructor(message = "Admin access code required.") {
    super(message)
    this.name = "AdminAuthError"
  }
}

function savedAdminToken() {
  if (typeof window === "undefined") return ""
  const queryToken = new URLSearchParams(window.location.search).get("admin_token") || ""
  let localToken = ""
  let sessionToken = ""
  try {
    localToken = window.localStorage?.getItem(ADMIN_TOKEN_STORAGE_KEY) || ""
  } catch {
    localToken = ""
  }
  try {
    sessionToken = window.sessionStorage?.getItem(ADMIN_TOKEN_STORAGE_KEY) || ""
  } catch {
    sessionToken = ""
  }
  return (
    localToken
    || sessionToken
    || queryToken
    || ""
  )
}

function savedPullSetting(key: string, fallback: string) {
  return typeof window !== "undefined" ? window.localStorage.getItem(key) || fallback : fallback
}

function savedProfitLossPeriod() {
  if (typeof window === "undefined") return "monthly"
  const saved = window.localStorage.getItem(PROFIT_LOSS_PERIOD_STORAGE_KEY) || ""
  return ["daily", "weekly", "monthly"].includes(saved) ? saved : "monthly"
}

function savedPullStoreIds() {
  if (typeof window === "undefined") return []
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PULL_STORE_IDS_STORAGE_KEY) || "[]")
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function cachedDashboardData() {
  if (typeof window === "undefined" || !savedAdminToken()) return null
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DASHBOARD_CACHE_STORAGE_KEY) || "null")
    if (!parsed || Date.now() - Number(parsed.savedAt || 0) > DASHBOARD_CACHE_MAX_AGE_MS) return null
    const data = parsed.data as DashboardData
    if (Number(data?.page || 1) !== 1) {
      return { ...data, rows: [], page: 1 }
    }
    return data
  } catch {
    return null
  }
}

function saveDashboardCache(data: DashboardData) {
  if (typeof window === "undefined" || !savedAdminToken()) return
  try {
    const page = Number(data?.page || 1)
    const cacheData = page === 1 ? data : { ...data, rows: [], page: 1 }
    window.localStorage.setItem(DASHBOARD_CACHE_STORAGE_KEY, JSON.stringify({ savedAt: Date.now(), data: cacheData }))
  } catch {
    // Ignore quota failures; the live API remains the source of truth.
  }
}

function notifyAdminAuthRequired() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("admin-auth-required"))
  }
}

const PAGE_SIZE = 100
const ORDERS_PAGE_SIZE = 20
const DUPLICATE_ASIN_PAGE_SIZE = 12

const orderConditionOptions = [
  { value: "all", label: "All orders" },
  { value: "ready", label: "Ready for fulfilment" },
  { value: "missing", label: "Missing marked" },
  { value: "ignored", label: "Do not process" },
  { value: "costly", label: "Cost review" },
  { value: "queued", label: "Queued for Amazon" },
  { value: "ordered", label: "Ordered / dispatched" },
  { value: "delivered", label: "Delivered" },
  { value: "inventory", label: "Using inventory" },
  { value: "error", label: "Errors" },
  { value: "cancelled_refunded", label: "Cancelled / refunded" },
]

type ExportColumn = { key: string; label: string }

type ExportFile = {
  id: number
  part_number: number
  row_count: number
  filename: string
  downloaded_at: string
  expires_at: string
  deleted_at: string
}

type ExportJob = {
  id: string
  view: string
  status: string
  total_records: number
  processed_records: number
  part_count: number
  error: string
  created_at: string
  updated_at: string
  completed_at: string
  files: ExportFile[]
}

type PullJob = {
  id: string
  store_id: number
  store_name: string
  status: string
  days: number
  limit_value: number
  batch_size: number
  total_orders: number
  processed_orders: number
  inserted_records: number
  error: string
  created_at: string
  updated_at: string
  completed_at: string
}

type ShopifyFulfilmentJob = {
  id: string
  store_name: string
  odoo_order_name: string
  odoo_order_url?: string
  route: string
  status: string
  attempts: number
  max_attempts: number
  shopify_dest_name?: string
  shopify_order_id?: string
  shopify_order_url?: string
  last_error: string
  created_at: string
  updated_at: string
  completed_at: string
}

type ShopifyOAuthMissing = {
  route: string
  dest_name: string
  shop: string
  authorized?: boolean
  error?: string
}

type ShopifyFulfilmentProgress = {
  status: string
  total: number
  processed: number
  current_order?: string
  current_route?: string
  message?: string
  started_at?: string
  updated_at?: string
  completed_at?: string
  error?: string
}

type ShopifyDuplicateOrder = {
  id: string
  name: string
  created_at: string
  cancelled_at?: string
  financial_status?: string
  fulfillment_status?: string
  url?: string
  keep?: boolean
  duplicate?: boolean
  protected?: boolean
  cancel_status?: string
  cancel_error?: string
}

type ShopifyDuplicateGroup = {
  key: string
  route: string
  dest_name: string
  shop: string
  odoo_order_name: string
  odoo_order_url?: string
  duplicate_count: number
  orders: ShopifyDuplicateOrder[]
  error?: string
}

type ShopifyDuplicateProgress = {
  status: string
  total: number
  processed: number
  duplicates_found: number
  cancelled: number
  cancel_failed: number
  message?: string
  error?: string
}

type ShopifyProductRepairProgress = {
  status: string
  total: number
  processed: number
  repaired: number
  missing: number
  failed: number
  current_order?: string
  cancel_requested?: boolean
  message?: string
  error?: string
}

type ShopifyProductRepairLog = {
  odoo_order_name: string
  shopify_order_id: string
  shopify_order_url?: string
  dest_name: string
  shop: string
  order_line_title?: string
  order_line_sku?: string
  old_product_title: string
  old_sku: string
  new_product_title: string
  new_sku: string
  verified_product_title?: string
  verified_variant_sku?: string
  status: string
  error?: string
  logged_at?: string
}

type ShopifyOrderStatusForceSyncProgress = {
  status: string
  total: number
  processed: number
  matched: number
  current_order?: string
  message?: string
  error?: string
  completed_at?: string
}

type ShopifyTrackingJob = {
  id: string
  status: string
  attempts: number
  from_date: string
  to_date: string
  dry_run: number
  skip_done_pickings: number
  validate_deliveries: number
  report_csv: string
  last_error: string
  created_at: string
  updated_at: string
  completed_at: string
}

const trackingExportColumns: ExportColumn[] = [
  { key: "odoo_order_names", label: "Odoo Orders" },
  { key: "amazon_order_id", label: "Amazon Order" },
  { key: "tracking_status", label: "Status" },
  { key: "carrier_tracking", label: "Carrier / Tracking" },
  { key: "latest_update", label: "Latest Update" },
  { key: "tracking_checked_at", label: "Checked" },
]

const fulfilmentPendingExportColumns: ExportColumn[] = [
  { key: "store_name", label: "Store" },
  { key: "odoo_order_name", label: "Odoo Order" },
  { key: "amazon_order_id", label: "Amazon Order" },
  { key: "carrier_tracking", label: "Carrier / Tracking" },
  { key: "tracking_status", label: "Tracking" },
  { key: "picking_summary", label: "Odoo Pickings" },
  { key: "fulfilment_status", label: "Status" },
  { key: "message", label: "Message" },
]

const epostExportColumns: ExportColumn[] = [
  { key: "store_name", label: "Store" },
  { key: "odoo_order_name", label: "Odoo Order" },
  { key: "amazon_order_id", label: "Amazon Order" },
  { key: "tracking_code", label: "ePost Tracking" },
  { key: "status", label: "Status" },
  { key: "shipping_fee", label: "Shipping Fee" },
  { key: "fulfilment_fee", label: "Fulfilment Fee" },
  { key: "shipping_total", label: "Shipping Total" },
  { key: "last_update_at", label: "Last Update" },
  { key: "destination", label: "Destination" },
  { key: "awb", label: "AWB" },
  { key: "last_checked_at", label: "Checked" },
]

const duplicateTrackingExportColumns: ExportColumn[] = [
  { key: "tracking_code", label: "Tracking Code" },
  { key: "duplicate_reason", label: "Reason" },
  { key: "epost_row_count", label: "ePost Rows" },
  { key: "shipping_row_count", label: "Invoice Rows" },
  { key: "order_count", label: "Linked Orders" },
  { key: "odoo_order_names", label: "Odoo Orders" },
  { key: "amazon_order_ids", label: "Amazon Orders" },
  { key: "statuses", label: "Statuses" },
  { key: "shipping_fee", label: "Shipping Fee" },
  { key: "fulfilment_fee", label: "Fulfilment Fee" },
  { key: "shipping_total", label: "Total Charges" },
  { key: "invoice_count", label: "Stored Invoices" },
  { key: "last_seen_at", label: "Last Seen" },
]

const missingExportColumns: ExportColumn[] = [
  { key: "odoo_order_name", label: "Odoo Order" },
  { key: "missing_asin", label: "Missing ASIN" },
  { key: "product_name", label: "Product" },
  { key: "quantity", label: "Qty" },
  { key: "last_error", label: "Error" },
  { key: "replacement_asin", label: "Replacement" },
]

const bulkExportColumns: ExportColumn[] = [
  { key: "asin", label: "ASIN" },
  { key: "quantity", label: "Qty" },
  { key: "order_names", label: "Orders" },
  { key: "product_names", label: "Products" },
  { key: "status", label: "Status" },
]

const costlyExportColumns: ExportColumn[] = [
  { key: "odoo_order_name", label: "Order" },
  { key: "asin", label: "ASIN" },
  { key: "product_name", label: "Product" },
  { key: "cost_review_loss", label: "Loss" },
  { key: "last_error", label: "Error" },
]

type PunchoutReturnUrl = {
  id: number
  label: string
  url: string
  is_default: number
}

type ServiceSettings = Record<string, string>
type DatabaseBackup = {
  key: string
  name: string
  size: number
  last_modified: string
}

type BackupProgress = {
  status: string
  percent: number
  message: string
  started_at: string
  updated_at: string
  completed_at: string
  error: string
  backup_name?: string
  backup_size?: number
}

type UiCopy = Record<string, { title?: string; description?: string; icon?: string }>

type ShopifyScriptConfig = {
  dtc?: Record<string, any>
  dtb?: Record<string, any>
  tracking?: Record<string, any>
}

type ReindexProgress = {
  status: string
  processed: number
  total: number
  percent: number
  message: string
  started_at: string
  updated_at: string
  completed_at: string
  error: string
  current_collection?: string
  current_processed?: number
  current_total?: number
  latest_record?: string
}

type ProfitLossOrder = {
  odoo_order_id: number
  odoo_order_name: string
  order_date: string
  odoo_order_value: number
  collected_delivery: number
  order_discounts: number
  amazon_order_value: number
  gross_profit: number
  shipping_fee: number
  fulfilment_fee: number
  shipping_total: number
  net_profit: number
  margin_percent: number
  package_count: number
  amazon_order_ids: string
}

type ProfitLossData = {
  summary: Record<string, number>
  period_rows: Array<Record<string, number | string>>
  orders: ProfitLossOrder[]
  manual_costs: Array<Record<string, string | number | null>>
  page: number
  per_page: number
  total: number
  imports: Array<Record<string, string | number>>
  start: string
  end: string
  month: string
}

type AccountingDocument = {
  id: number
  document_type: string
  odoo_order_name: string
  country_code: string
  tax_region: string
  invoice_date: string
  original_filename: string
  stored_filename: string
  storage_key: string
  storage_url: string
  file_size: number
  created_at: string
}

type AccountingData = {
  documents: AccountingDocument[]
  summary: Array<{ tax_region: string; document_type: string; document_count: number; total_bytes: number }>
  page?: number
  per_page?: number
  total?: number
}

type OrderColumnKey =
  | "store"
  | "odoo_order"
  | "odoo_order_date"
  | "destination_country"
  | "product"
  | "reference"
  | "pulled_at"
  | "ordered_at"
  | "asin"
  | "spaid"
  | "qty"
  | "odoo_status"
  | "inventory"
  | "state"
  | "engine"
  | "amazon_account"
  | "tracking"
  | "cancelled_earlier"
  | "amazon_order"
  | "shopify_order"
  | "shopify_fulfillment"
  | "shopify_fulfillment_at"
  | "comments"
  | "error"

type SortKey =
  | "odoo_order_name"
  | "odoo_order_date"
  | "destination_country"
  | "product_name"
  | "default_code"
  | "pulled_at"
  | "ordered_at"
  | "asin"
  | "supplier_part_auxiliary_id"
  | "quantity"
  | "odoo_status_label"
  | "state"
  | "order_engine"
  | "amazon_account_name"
  | "tracking_status"
  | "amazon_cancelled_order_id"
  | "amazon_order_id"
  | "fulfilment_note"
  | "last_error"
type SortDirection = "asc" | "desc"

type OrderColumn = {
  key: OrderColumnKey
  label: string
  width: string
  sortable?: SortKey
  visible?: boolean
}

const ORDER_TABLE_STORAGE_KEY = "fulfilment.orderTable.columns.v1"

const defaultOrderColumns: OrderColumn[] = [
  { key: "store", label: "Store", width: "w-36" },
  { key: "odoo_order", label: "Odoo Order", width: "w-32", sortable: "odoo_order_name" },
  { key: "odoo_order_date", label: "Order Date", width: "w-44", sortable: "odoo_order_date" },
  { key: "destination_country", label: "Country", width: "w-36", sortable: "destination_country" },
  { key: "product", label: "Product", width: "w-[520px]", sortable: "product_name" },
  { key: "reference", label: "Reference", width: "w-48", sortable: "default_code" },
  { key: "pulled_at", label: "Pulled At", width: "w-44", sortable: "pulled_at" },
  { key: "ordered_at", label: "Placed At", width: "w-44", sortable: "ordered_at" },
  { key: "asin", label: "ASIN", width: "w-36", sortable: "asin" },
  { key: "spaid", label: "SPAID", width: "w-44", sortable: "supplier_part_auxiliary_id" },
  { key: "qty", label: "Qty", width: "w-20", sortable: "quantity" },
  { key: "odoo_status", label: "Odoo Status", width: "w-32", sortable: "odoo_status_label" },
  { key: "inventory", label: "Inventory", width: "w-36" },
  { key: "state", label: "State", width: "w-28", sortable: "state" },
  { key: "engine", label: "Engine", width: "w-28", sortable: "order_engine" },
  { key: "amazon_account", label: "Amazon Account", width: "w-44", sortable: "amazon_account_name" },
  { key: "tracking", label: "Tracking", width: "w-36", sortable: "tracking_status" },
  { key: "cancelled_earlier", label: "Cancelled Earlier", width: "w-36", sortable: "amazon_cancelled_order_id" },
  { key: "amazon_order", label: "Amazon Order", width: "w-44", sortable: "amazon_order_id" },
  { key: "shopify_order", label: "Shopify Order", width: "w-44" },
  { key: "shopify_fulfillment", label: "Shopify Fulfilment", width: "w-40" },
  { key: "shopify_fulfillment_at", label: "Shopify Fulfilled At", width: "w-44" },
  { key: "comments", label: "Comments", width: "w-80", sortable: "fulfilment_note" },
  { key: "error", label: "Error", width: "w-64", sortable: "last_error" },
]

function loadOrderColumns() {
  if (typeof window === "undefined") return defaultOrderColumns
  try {
    const saved = JSON.parse(window.localStorage.getItem(ORDER_TABLE_STORAGE_KEY) || "[]") as { key: OrderColumnKey; visible: boolean }[]
    if (!Array.isArray(saved) || !saved.length) return defaultOrderColumns
    const byKey = new Map(defaultOrderColumns.map((column) => [column.key, column]))
    const savedColumns = saved.flatMap((item) => {
      const column = byKey.get(item.key)
      return column ? [{ ...column, visible: item.visible !== false }] : []
    })
    const missingColumns = defaultOrderColumns.filter((column) => !saved.some((item) => item.key === column.key)).map((column) => ({ ...column, visible: true }))
    return [...savedColumns, ...missingColumns]
  } catch {
    return defaultOrderColumns
  }
}

function formatDateTime(value?: string) {
  if (!value) return ""
  const normalized = value.includes("T") ? value : value.replace(" ", "T")
  const hasTime = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(normalized)
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(normalized)
  const date = new Date(hasTime && !hasTimezone ? `${normalized}Z` : normalized)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatFileSize(value?: number) {
  const bytes = Number(value || 0)
  if (!bytes) return "0 KB"
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString()} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatMoney(value?: number) {
  return Number(value || 0).toLocaleString(undefined, { style: "currency", currency: "USD" })
}

const emptyPunchoutReturnUrl: Omit<PunchoutReturnUrl, "id"> = {
  label: "",
  url: "",
  is_default: 0,
}

const emptyStore: Omit<Store, "id"> = {
  name: "",
  odoo_url: "",
  odoo_db: "",
  odoo_user: "",
  odoo_password: "",
  website_id: null,
}

const emptyAddress: Omit<Address, "id"> = {
  label: "",
  company_name: "Nutricity",
  phone_number: "",
  address_line1: "",
  address_line2: "",
  address_line3: "",
  city: "",
  state_or_region: "",
  postal_code: "",
  country_code: "US",
  is_default: 0,
}

const emptyAccount: Omit<AmazonAccount, "id"> = {
  name: "",
  api_base_url: "https://na.business-api.amazon.com",
  tracking_api_base_url: "https://na.business-api.amazon.com",
  lwa_token_url: "https://api.amazon.com/auth/o2/token",
  lwa_client_id: "",
  lwa_client_secret: "",
  lwa_refresh_token: "",
  api_access_token: "",
  buyer_email: "",
  buying_group_id: "",
  product_region: "US",
  locale: "en_US",
  cxml_from_identity: "",
  cxml_shared_secret: "",
  cxml_po_url: "",
  cxml_punchout_url: "",
  cxml_punchout_test_url: "",
  cxml_auth_mode: "header",
  cxml_cart_session_id: "",
  cxml_credential_domain: "NetworkId",
  cxml_to_identity: "Amazon",
  is_default: 0,
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const adminToken = savedAdminToken()
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(adminToken ? { "X-Admin-Token": adminToken } : {}), ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const text = await response.text()
    if (response.status === 401) {
      notifyAdminAuthRequired()
      throw new AdminAuthError(text || "Admin access code required.")
    }
    throw new Error(text || response.statusText)
  }
  return response.json()
}

async function apiWithAdminToken<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", "X-Admin-Token": token, ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const text = await response.text()
    if (response.status === 401) {
      throw new AdminAuthError(text || "Admin access code required.")
    }
    throw new Error(text || response.statusText)
  }
  return response.json()
}

async function uploadWithAdminToken(path: string, form: FormData) {
  const adminToken = savedAdminToken()
  const response = await fetch(path, {
    method: "POST",
    headers: adminToken ? { "X-Admin-Token": adminToken } : undefined,
    body: form,
  })
  if (!response.ok) {
    const text = await response.text()
    if (response.status === 401) {
      notifyAdminAuthRequired()
      throw new AdminAuthError(text || "Admin access code required.")
    }
    throw new Error(text || response.statusText)
  }
  return response.json()
}

function adminDownloadHref(path: string) {
  const token = savedAdminToken()
  if (!token) return path
  const separator = path.includes("?") ? "&" : "?"
  return `${path}${separator}admin_token=${encodeURIComponent(token)}`
}

function openExternalUrl(href?: string) {
  if (!href) return
  window.open(href, "_blank", "noopener,noreferrer")
}

function openExternalLink(event: MouseEvent<HTMLAnchorElement>, href?: string) {
  event.preventDefault()
  event.stopPropagation()
  openExternalUrl(href)
}

function openExternalCell(event: MouseEvent<HTMLElement>, href?: string) {
  if (!href) return
  const target = event.target
  if (target instanceof HTMLElement && target.closest("a,button,input,select,textarea")) return
  openExternalUrl(href)
}

function openExternalCellKey(event: ReactKeyboardEvent<HTMLElement>, href?: string) {
  if (!href || (event.key !== "Enter" && event.key !== " ")) return
  event.preventDefault()
  openExternalUrl(href)
}

function SelectField({
  label,
  value,
  onChange,
  children,
  className = "",
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: ReactNode
  className?: string
}) {
  return (
    <div className={`grid min-w-0 gap-1.5 ${className}`}>
      <Label>{label}</Label>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="form-select"
      >
        {children}
      </select>
    </div>
  )
}

function SearchBox({
  value,
  onChange,
  placeholder,
  className = "",
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  className?: string
}) {
  return (
    <div className={`input-icon ${className}`}>
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
      <span className="input-icon-addon">
        <Search className="icon icon-1" />
      </span>
    </div>
  )
}

function MultiStoreDropdown({
  stores,
  selected,
  onChange,
}: {
  stores: Store[]
  selected: string[]
  onChange: (value: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const selectedSet = new Set(selected)
  const selectedStores = stores.filter((store) => selectedSet.has(String(store.id)))
  const label = selectedStores.length === 0
    ? "Select stores"
    : selectedStores.length === 1
      ? selectedStores[0].name
      : `${selectedStores.length} stores selected`

  function toggleStore(id: string) {
    const next = selectedSet.has(id) ? selected.filter((value) => value !== id) : [...selected, id]
    onChange(next)
  }

  return (
    <div className="dropdown w-100">
      <Label>Stores to pull</Label>
      <button type="button" className="form-select text-start" onClick={() => setOpen((current) => !current)} aria-expanded={open}>
        {label}
      </button>
      {open && (
        <div className="dropdown-menu show w-100 p-2">
          <button type="button" className="dropdown-item rounded" onClick={() => onChange(stores.map((store) => String(store.id)))}>
            <CheckCircle2 className="size-4" />
            Select all stores
          </button>
          <button type="button" className="dropdown-item rounded" onClick={() => onChange([])}>
            <AlertCircle className="size-4" />
            Clear selection
          </button>
          <div className="dropdown-divider" />
          {stores.map((store) => {
            const id = String(store.id)
            return (
              <label key={store.id} className="dropdown-item rounded">
                <Checkbox checked={selectedSet.has(id)} onCheckedChange={() => toggleStore(id)} />
                <span>{store.name}</span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ value }: { value?: string }) {
  const status = value || "pending"
  if (status === "missing") {
    return <Badge variant="destructive">missing</Badge>
  }
  if (status === "ignored") {
    return <Badge variant="outline" className="border-slate-400 text-slate-600">ignored</Badge>
  }
  if (["cancelled", "refunded"].includes(status)) {
    return <Badge variant="destructive">{status}</Badge>
  }
  if (status === "lost") {
    return <Badge variant="destructive">lost</Badge>
  }
  if (["ordered", "dispatched", "delivered"].includes(status)) {
    return <Badge variant="secondary">{status}</Badge>
  }
  if (["available", "reserved", "inventory"].includes(status)) {
    return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">{status}</Badge>
  }
  if (status === "submitted") {
    return <Badge variant="outline">submitted</Badge>
  }
  if (status === "amazon_placed") {
    return <Badge variant="outline" className="border-amber-400 bg-amber-50 text-amber-800">placed on Amazon / not synced</Badge>
  }
  return <Badge variant="outline">{status}</Badge>
}

function copyPlainText(value: string) {
  const text = String(value || "").trim()
  if (!text || typeof navigator === "undefined") return Promise.resolve(false)
  return navigator.clipboard.writeText(text).then(() => true).catch(() => false)
}

function OdooOrderRef({
  name,
  url = "",
  className = "",
  linkClassName = "",
}: {
  name?: string | number
  url?: string
  className?: string
  linkClassName?: string
}) {
  const [copied, setCopied] = useState(false)
  const text = String(name || "").trim()
  if (!text) return <span className="text-muted-foreground">-</span>
  const content = url ? (
    <a className={cn("min-w-0 truncate text-primary underline-offset-4 hover:underline", linkClassName)} href={url} target="_blank" rel="noreferrer">
      {text}
    </a>
  ) : (
    <span className={cn("min-w-0 truncate", linkClassName)}>{text}</span>
  )
  return (
    <span className={cn("inline-flex max-w-full items-center gap-1.5", className)}>
      {content}
      <button
        type="button"
        className={cn(
          "inline-flex size-6 flex-none items-center justify-center rounded border bg-background transition-colors",
          copied ? "border-emerald-500 text-emerald-700" : "border-primary/25 text-primary hover:border-primary hover:bg-primary/10",
        )}
        title={`Copy ${text}`}
        aria-label={`Copy ${text}`}
        onClick={async (event) => {
          event.preventDefault()
          event.stopPropagation()
          const ok = await copyPlainText(text)
          setCopied(ok)
          window.setTimeout(() => setCopied(false), 1200)
        }}
      >
        <Copy className="size-3.5" />
      </button>
    </span>
  )
}

function OdooOrderRefs({ names, className = "" }: { names?: Array<string | number>; className?: string }) {
  const cleanNames = (names || []).map((name) => String(name || "").trim()).filter(Boolean)
  if (!cleanNames.length) return <span className="text-muted-foreground">No Odoo order linked</span>
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {cleanNames.map((name) => <OdooOrderRef key={name} name={name} />)}
    </div>
  )
}

function isLimitPurchaseLine(row: OrderLine) {
  return /limit purchase|limited purchase quantity|business has reached/i.test(`${row.last_error || ""} ${row.fulfilment_note || ""}`)
}

function isPartialQuantityLine(row: OrderLine) {
  return /less quantity|partial quantity|customer ordered|maximum allowable|only allows|did not allow the full quantity|could add only/i.test(`${row.last_error || ""} ${row.fulfilment_note || ""}`)
}

function isMissingOrderLine(row: OrderLine) {
  const hasCurrentAmazonOrder = Boolean(row.amazon_order_id)
  const fulfilmentState = String(row.state || "").toLowerCase()
  if (hasCurrentAmazonOrder && ["ordered", "dispatched", "delivered"].includes(fulfilmentState)) return false
  return (
    row.state === "missing" ||
    row.amazon_status === "missing" ||
    Boolean(row.missing_asin) ||
    /missing|unavailable on amazon|did not load product controls|out of stock/i.test(`${row.last_error || ""} ${row.fulfilment_note || ""}`)
  )
}

function isAmazonDeliveredLine(row: OrderLine) {
  return row.state === "delivered" || String(row.tracking_status || "").toLowerCase() === "delivered"
}

function isCurrentAmazonCancelledLine(row: OrderLine) {
  return Boolean(row.amazon_cancelled_at && !row.amazon_order_id)
}

function ErrorTooltip({ value, className = "" }: { value?: string; className?: string }) {
  const text = String(value || "").trim()
  if (!text) return null
  if (text.startsWith("Shopify OAuth token")) return null
  return (
    <Tooltip>
      <TooltipTrigger className={`block w-full cursor-help truncate text-left text-destructive ${className}`}>
        {text}
      </TooltipTrigger>
      <TooltipContent
        side="top"
        align="end"
        className="max-w-[calc(100vw-32px)]"
        innerClassName="max-h-[min(45vh,360px)] w-[min(680px,calc(100vw-56px))] overflow-auto whitespace-normal break-words text-left leading-relaxed [overflow-wrap:anywhere]"
      >
        <div className="grid min-w-0 gap-2">
          <span>{text}</span>
        </div>
      </TooltipContent>
    </Tooltip>
  )
}

function ResultDialog({ modal, onClose }: { modal: ModalState; onClose: () => void }) {
  useEffect(() => {
    if (!modal) return
    const timer = window.setTimeout(onClose, 2000)
    return () => window.clearTimeout(timer)
  }, [modal, onClose])

  if (!modal) return null

  return (
    <div className="alert-toast" role="status" aria-live="polite">
      <div className={`alert ${modal.ok ? "alert-success" : "alert-danger"} alert-dismissible`} role="alert">
        <div className="alert-icon">
          {modal.ok ? <CheckCircle2 className="icon alert-icon icon-2" /> : <AlertCircle className="icon alert-icon icon-2" />}
        </div>
        <div className="min-w-0">
          <h4 className="alert-title">{modal.title}</h4>
          <div className="alert-description text-secondary">{modal.message}</div>
        </div>
        <button type="button" className="btn-close" onClick={onClose} aria-label="Close notification" />
      </div>
    </div>
  )
}

function UiCopyDialog({
  copyKey,
  value,
  onClose,
  onSave,
}: {
  copyKey: string | null
  value: { title?: string; description?: string; icon?: string }
  onClose: () => void
  onSave: (value: { title: string; description: string; icon?: string }) => Promise<void>
}) {
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [icon, setIcon] = useState("")
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!copyKey) return
    setTitle(value.title || "")
    setDescription(value.description || "")
    setIcon(value.icon || "")
    setBusy(false)
  }, [copyKey, value.title, value.description, value.icon])
  return (
    <Dialog open={Boolean(copyKey)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit Text</DialogTitle>
          <DialogDescription>Change the title and short description shown in the app.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <TextField label="Title" value={title} onChange={setTitle} />
          <TextField label="Short Description" value={description} onChange={setDescription} />
          {copyKey === "app_header" && <TextField label="Icon Name" value={icon} onChange={setIcon} />}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onSave({ title, description, icon })
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ManualFulfilmentDialog({
  open,
  selectedCount,
  onClose,
  onSubmit,
}: {
  open: boolean
  selectedCount: number
  onClose: () => void
  onSubmit: (payload: { reference: string; url: string; third_party: boolean; total_cost: number }) => Promise<void>
}) {
  const [thirdParty, setThirdParty] = useState(false)
  const [reference, setReference] = useState("")
  const [url, setUrl] = useState("")
  const [totalCost, setTotalCost] = useState("")
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!open) return
    setThirdParty(false)
    setReference("")
    setUrl("")
    setTotalCost("")
    setBusy(false)
  }, [open])
  const costValue = Number(totalCost || 0)
  const canSave = thirdParty ? Boolean(reference.trim() || url.trim()) && costValue > 0 : Boolean(reference.trim())
  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Manual Fulfilment</DialogTitle>
          <DialogDescription>
            Mark the selected Odoo order line(s) as fulfilled without placing a new app order.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Alert>
            <CheckCircle2 className="size-4" />
            <AlertTitle>{selectedCount.toLocaleString()} selected line(s)</AlertTitle>
            <AlertDescription>The app will apply this to the open lines in the selected Odoo order(s).</AlertDescription>
          </Alert>
          <label className="form-check w-fit cursor-pointer">
            <Checkbox checked={thirdParty} onCheckedChange={(checked) => setThirdParty(Boolean(checked))} />
            <span className="form-check-label">Fulfilled at third party</span>
          </label>
          <TextField
            label={thirdParty ? "Third-party order number or reference" : "Amazon order ID"}
            value={reference}
            onChange={setReference}
          />
          <TextField
            label={thirdParty ? "Third-party order URL or notes" : "Amazon order URL (optional)"}
            value={url}
            onChange={setUrl}
          />
          {thirdParty && (
            <TextField
              label="Third-party total cost"
              type="number"
              value={totalCost}
              onChange={setTotalCost}
            />
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            disabled={!canSave || busy}
            onClick={async () => {
              setBusy(true)
              try {
                await onSubmit({ reference, url, third_party: thirdParty, total_cost: costValue })
              } finally {
                setBusy(false)
              }
            }}
          >
            {busy ? "Saving..." : "Save Manual Fulfilment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AdminAccessDialog({
  open,
  onSubmit,
  onClose,
  error,
  busy,
}: {
  open: boolean
  onSubmit: (token: string, remember: boolean) => void
  onClose: () => void
  error?: string
  busy?: boolean
}) {
  const [token, setToken] = useState(savedAdminToken())
  const [remember, setRemember] = useState(true)

  useEffect(() => {
    if (open) setToken(savedAdminToken())
  }, [open])

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Lock className="size-4" />
            Admin access
          </DialogTitle>
          <DialogDescription>
            Enter the admin code once. This browser will remember it on this PC.
          </DialogDescription>
        </DialogHeader>
        {error ? (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertTitle>Admin code rejected</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <form
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmit(token.trim(), remember)
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="admin-access-code">Admin code</Label>
            <Input
              id="admin-access-code"
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoFocus
            />
          </div>
          <label className="form-check w-fit cursor-pointer">
            <Checkbox checked={remember} onCheckedChange={(checked) => setRemember(Boolean(checked))} />
            <span className="form-check-label">Save token on this PC</span>
          </label>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!token.trim() || busy}>{busy ? "Checking..." : "Save code"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function AdminAccessScreen({ onSubmit, error, busy }: { onSubmit: (token: string, remember: boolean) => void; error?: string; busy?: boolean }) {
  const [token, setToken] = useState("")
  const [remember, setRemember] = useState(true)

  return (
    <div className="page tabler-admin-shell flex min-h-screen items-center justify-center px-4 py-10">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex size-12 items-center justify-center rounded-md bg-primary text-primary-fg">
            <Lock className="size-6" />
          </div>
          <CardTitle>Admin access</CardTitle>
          <CardDescription>Enter your admin token to open the control panel.</CardDescription>
        </CardHeader>
        <CardContent>
          {error ? (
            <Alert variant="destructive" className="mb-4">
              <AlertCircle className="size-4" />
              <AlertTitle>Admin code rejected</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          <form
            className="grid gap-4"
            onSubmit={(event) => {
              event.preventDefault()
              onSubmit(token.trim(), remember)
            }}
          >
            <div className="grid gap-2">
              <Label htmlFor="admin-token-login">Admin token</Label>
              <Input
                id="admin-token-login"
                type="password"
                autoComplete="current-password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoFocus
              />
            </div>
            <label className="form-check w-fit cursor-pointer">
              <Checkbox checked={remember} onCheckedChange={(checked) => setRemember(Boolean(checked))} />
              <span className="form-check-label">Save token on this PC</span>
            </label>
            <Button type="submit" disabled={!token.trim() || busy}>{busy ? "Checking..." : "Open Admin Panel"}</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

function PaginationControls({
  page,
  total,
  onPage,
  disabled = false,
  perPage = PAGE_SIZE,
  label = "rows",
}: {
  page: number
  total: number
  onPage: (page: number) => void
  disabled?: boolean
  perPage?: number
  label?: string
}) {
  const totalPages = Math.max(1, Math.ceil(total / perPage))
  const candidatePages = Array.from(new Set([
    1,
    page - 1,
    page,
    page + 1,
    totalPages,
  ])).filter((value) => value >= 1 && value <= totalPages)
  const visiblePages = candidatePages.reduce<Array<number | "ellipsis">>((items, value, index) => {
    const previous = candidatePages[index - 1]
    if (previous && value - previous > 1) items.push("ellipsis")
    items.push(value)
    return items
  }, [])

  const goToPage = (nextPage: number) => {
    if (disabled || nextPage === page || nextPage < 1 || nextPage > totalPages) return
    onPage(nextPage)
  }

  return (
    <div className="pagination-wrap">
      <p className="m-0 text-sm text-muted-foreground">
        Page {page} / {totalPages} - {total.toLocaleString()} {label}
      </p>
      <ul className="pagination pagination-outline m-0">
        <li className={`page-item ${disabled || page <= 1 ? "disabled" : ""}`}>
          <button className="page-link" type="button" disabled={disabled || page <= 1} onClick={() => goToPage(page - 1)} aria-label="Previous page">
            <ChevronLeft className="icon icon-1" />
          </button>
        </li>
        {visiblePages.map((value, index) => (
          value === "ellipsis" ? (
            <li key={`ellipsis-${index}`} className="page-item disabled">
              <span className="page-link disabled">...</span>
            </li>
          ) : (
            <li key={value} className={`page-item ${value === page ? "active" : ""}`}>
              <button className="page-link" type="button" disabled={disabled || value === page} onClick={() => goToPage(value)}>
                {value}
              </button>
            </li>
          )
        ))}
        <li className={`page-item ${disabled || page >= totalPages ? "disabled" : ""}`}>
          <button className="page-link" type="button" disabled={disabled || page >= totalPages} onClick={() => goToPage(page + 1)} aria-label="Next page">
            <ChevronRight className="icon icon-1" />
          </button>
        </li>
      </ul>
    </div>
  )
}

function ExportControls({
  view,
  storeId,
  columns,
  selectedIds,
  selectAll,
  total,
  filters = {},
  onSelectAll,
  onClear,
  onResult,
  onDownloads,
  extraAction,
}: {
  view: string
  storeId: string
  columns: ExportColumn[]
  selectedIds: Array<number | string>
  selectAll: boolean
  total: number
  filters?: Record<string, string | number>
  onSelectAll: () => void
  onClear: () => void
  onResult: (modal: ModalState) => void
  onDownloads: () => void
  extraAction?: ReactNode
}) {
  const count = selectAll ? total : selectedIds.length
  async function startExport() {
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/exports", {
        method: "POST",
        body: JSON.stringify({
          view,
          store_id: storeId ? Number(storeId) : null,
          columns,
          select_all: selectAll,
          selected_ids: selectAll ? [] : selectedIds,
          filters,
        }),
      })
      onResult({ ok: true, title: "CSV Export Started", message: result.message })
      onDownloads()
    } catch (error) {
      onResult({ ok: false, title: "CSV Export Failed", message: String(error) })
    }
  }
  return (
    <div className="card-actions btn-list justify-end">
      <span className="text-sm text-muted-foreground">{count.toLocaleString()} selected</span>
      <Button variant="outline" size="sm" disabled={!total || selectAll} onClick={onSelectAll}>
        Select all {total.toLocaleString()}
      </Button>
      <Button variant="outline" size="sm" disabled={!count} onClick={onClear}>
        Clear Selection
      </Button>
      {extraAction}
      <Button size="sm" disabled={!count} onClick={startExport}>
        <Download className="size-4" />
        Download CSV
      </Button>
    </div>
  )
}

function App() {
  const initialDashboard = cachedDashboardData()
  const [data, setData] = useState<DashboardData | null>(() => initialDashboard)
  const [orderRows, setOrderRows] = useState<OrderLine[]>(() => initialDashboard?.rows || [])
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [inventoryPage, setInventoryPage] = useState(1)
  const [inventoryTotal, setInventoryTotal] = useState(0)
  const [cancelledOrders, setCancelledOrders] = useState<CancelledOrderRow[]>([])
  const [cancelledOrdersPage, setCancelledOrdersPage] = useState(1)
  const [cancelledOrdersTotal, setCancelledOrdersTotal] = useState(0)
  const [ordersPage, setOrdersPage] = useState(1)
  const [ordersTotal, setOrdersTotal] = useState(() => initialDashboard?.total || 0)
  const [ordersLoading, setOrdersLoading] = useState(false)
  const [ordersTransitioning, setOrdersTransitioning] = useState(false)
  const [missingRows, setMissingRows] = useState<OrderLine[]>([])
  const [missingPage, setMissingPage] = useState(1)
  const [missingTotal, setMissingTotal] = useState(0)
  const [backInStockRows, setBackInStockRows] = useState<BackInStockRow[]>([])
  const [backInStockPage, setBackInStockPage] = useState(1)
  const [backInStockTotal, setBackInStockTotal] = useState(0)
  const [partialFulfilments, setPartialFulfilments] = useState<PartialFulfilment[]>([])
  const [partialFulfilmentsPage, setPartialFulfilmentsPage] = useState(1)
  const [partialFulfilmentsTotal, setPartialFulfilmentsTotal] = useState(0)
  const [fulfilmentPendingRows, setFulfilmentPendingRows] = useState<FulfilmentPendingRow[]>([])
  const [fulfilmentPendingPage, setFulfilmentPendingPage] = useState(1)
  const [fulfilmentPendingTotal, setFulfilmentPendingTotal] = useState(0)
  const [epostRows, setEpostRows] = useState<EpostTrackingRow[]>([])
  const [epostPage, setEpostPage] = useState(1)
  const [epostTotal, setEpostTotal] = useState(0)
  const [epostStatusFilter, setEpostStatusFilter] = useState("all")
  const [trackingOrders] = useState<TrackingOrder[]>([])
  const [trackingPage, setTrackingPage] = useState(1)
  const trackingTotal = 0
  const [paymentFailures, setPaymentFailures] = useState<PaymentFailure[]>([])
  const [paymentFailuresPage, setPaymentFailuresPage] = useState(1)
  const [paymentFailuresTotal, setPaymentFailuresTotal] = useState(0)
  const [bulkGroups, setBulkGroups] = useState<BulkGroup[]>([])
  const [bulkPage, setBulkPage] = useState(1)
  const [bulkTotal, setBulkTotal] = useState(0)
  const [bulkDays, setBulkDays] = useState("2")
  const [activeBulkDays, setActiveBulkDays] = useState(2)
  const [profitLossPeriod, setProfitLossPeriod] = useState(savedProfitLossPeriod)
  const [chromeQueueJobs, setChromeQueueJobs] = useState<ChromeQueueJob[]>([])
  const [chromeQueueCounts, setChromeQueueCounts] = useState<ChromeQueueCount[]>([])
  const [duplicateAsins, setDuplicateAsins] = useState<DuplicateAsin[]>([])
  const [duplicateAsinPage, setDuplicateAsinPage] = useState(1)
  const [duplicateAsinTotal, setDuplicateAsinTotal] = useState(0)
  const [duplicateAsinDays, setDuplicateAsinDays] = useState("2")
  const [activeDuplicateAsinDays, setActiveDuplicateAsinDays] = useState(2)
  const [costlyRows, setCostlyRows] = useState<OrderLine[]>([])
  const [costlyPage, setCostlyPage] = useState(1)
  const [costlyTotal, setCostlyTotal] = useState(0)
  const [page, setPage] = useState(appPageFromLocation)
  const [openNavGroup, setOpenNavGroup] = useState<string | null>(null)
  const navTabsRef = useRef<HTMLDivElement | null>(null)
  const ordersSelectionAnchor = useRef<number | null>(null)
  const ordersShiftKeyDown = useRef(false)
  const initialDashboardRefreshDone = useRef(false)
  const initialDashboardRefreshStarted = useRef(false)
  const [storeId, setStoreId] = useState(() => String(initialDashboard?.current_store_id || ""))
  const [addressId, setAddressId] = useState(() => String(initialDashboard?.addresses.find((address) => address.is_default)?.id || initialDashboard?.addresses[0]?.id || ""))
  const [amazonAccountId, setAmazonAccountId] = useState(() => String(initialDashboard?.amazon_accounts.find((account) => account.is_default)?.id || initialDashboard?.amazon_accounts[0]?.id || ""))
  const [orderingEngine, setOrderingEngine] = useState(() => initialDashboard?.default_ordering_engine || "rest")
  const [days, setDays] = useState(() => savedPullSetting(PULL_DAYS_STORAGE_KEY, "7"))
  const [limit, setLimit] = useState(() => savedPullSetting(PULL_LIMIT_STORAGE_KEY, "0"))
  const [pullStoreIds, setPullStoreIds] = useState<string[]>(savedPullStoreIds)
  const [pullOrderNames, setPullOrderNames] = useState("")
  const [search, setSearch] = useState("")
  const [ordersQuery, setOrdersQuery] = useState("")
  const [orderCondition, setOrderCondition] = useState("all")
  const [selected, setSelected] = useState<number[]>([])
  const [ordersSelectAll, setOrdersSelectAll] = useState(false)
  const [pendingSelected, setPendingSelected] = useState<number[]>([])
  const [pendingSelectAll, setPendingSelectAll] = useState(false)
  const [epostSelected, setEpostSelected] = useState<number[]>([])
  const [epostSelectAll, setEpostSelectAll] = useState(false)
  const [trackingSelected, setTrackingSelected] = useState<string[]>([])
  const [trackingSelectAll, setTrackingSelectAll] = useState(false)
  const [missingSelected, setMissingSelected] = useState<number[]>([])
  const [missingSelectAll, setMissingSelectAll] = useState(false)
  const [bulkSelected, setBulkSelected] = useState<string[]>([])
  const [bulkSelectAll, setBulkSelectAll] = useState(false)
  const [costlySelected, setCostlySelected] = useState<number[]>([])
  const [costlySelectAll, setCostlySelectAll] = useState(false)
  const [editingSpaid, setEditingSpaid] = useState<OrderLine | null>(null)
  const [editingReplacement, setEditingReplacement] = useState<OrderLine | null>(null)
  const [manualFulfilmentOpen, setManualFulfilmentOpen] = useState(false)
  const [resetFulfilmentConfirmOpen, setResetFulfilmentConfirmOpen] = useState(false)
  const [placeRecentConfirmOpen, setPlaceRecentConfirmOpen] = useState(false)
  const [placeRecentDays, setPlaceRecentDays] = useState(() => savedPullSetting(PULL_DAYS_STORAGE_KEY, "5"))
  const [allowMissingSpaid, setAllowMissingSpaid] = useState(false)
  const [resendMissingAsins, setResendMissingAsins] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("odoo_order_date")
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc")
  const [showColumnSettings, setShowColumnSettings] = useState(false)
  const [orderColumns, setOrderColumns] = useState<OrderColumn[]>(loadOrderColumns)
  const [draggedColumnKey, setDraggedColumnKey] = useState<OrderColumnKey | null>(null)
  const [columnDropTarget, setColumnDropTarget] = useState<{ key: OrderColumnKey; after: boolean } | null>(null)
  const [busy, setBusy] = useState("")
  const [modal, setModal] = useState<ModalState>(null)
  const [shopifyStatusForceSync, setShopifyStatusForceSync] = useState<ShopifyOrderStatusForceSyncProgress | null>(null)
  const [adminTokenSaved, setAdminTokenSaved] = useState(Boolean(savedAdminToken()))
  const [adminAccessOpen, setAdminAccessOpen] = useState(!savedAdminToken())
  const [adminAuthError, setAdminAuthError] = useState("")
  const [adminAuthBusy, setAdminAuthBusy] = useState(false)
  const [uiCopy, setUiCopy] = useState<UiCopy>({})
  const [editingCopyKey, setEditingCopyKey] = useState<string | null>(null)
  const shopifyStatusSyncKeyRef = useRef("")
  const ordersRequestSeqRef = useRef(0)
  const dashboardRequestSeqRef = useRef(0)
  const ordersAbortRef = useRef<AbortController | null>(null)
  const ordersInFlightKeyRef = useRef("")
  const ordersPageCacheRef = useRef<Map<string, { rows: OrderLine[]; page: number; per_page: number; total: number; cachedAt: number }>>(new Map())
  const selectedOrderRowsRef = useRef<Map<number, OrderLine>>(new Map())
  const ordersPageRef = useRef(ordersPage)
  const shopifyStatusCompletedRefreshRef = useRef("")
  const shopifyStatusForceSyncPollRef = useRef(false)

  function pagedQuery(nextStoreId: string, nextPage = 1, extra?: Record<string, string | number>) {
    const query = new URLSearchParams()
    if (nextStoreId) query.set("store_id", nextStoreId)
    query.set("page", String(nextPage))
    query.set("per_page", String(extra?.per_page || PAGE_SIZE))
    Object.entries(extra || {}).forEach(([key, value]) => {
      if (key !== "per_page") query.set(key, String(value))
    })
    return `?${query.toString()}`
  }

  function ordersCacheKey(nextStoreId = storeId, nextPage = ordersPage, term = ordersQuery.trim()) {
    return JSON.stringify({
      store: nextStoreId || "",
      page: nextPage,
      q: term,
      condition: orderCondition,
      sort: sortKey,
      direction: sortDirection,
      perPage: ORDERS_PAGE_SIZE,
    })
  }

  function resetPagination() {
      setOrdersPage(1)
      setOrdersSelectAll(false)
      setSelected([])
      setInventoryPage(1)
      setMissingPage(1)
      setMissingSelectAll(false)
      setMissingSelected([])
      setPartialFulfilmentsPage(1)
      setFulfilmentPendingPage(1)
      setPendingSelectAll(false)
      setPendingSelected([])
      setEpostPage(1)
      setEpostSelectAll(false)
      setEpostSelected([])
      setTrackingPage(1)
      setTrackingSelectAll(false)
      setTrackingSelected([])
      setPaymentFailuresPage(1)
      setBulkPage(1)
      setBulkSelectAll(false)
      setBulkSelected([])
      setDuplicateAsinPage(1)
      setCostlyPage(1)
      setCostlySelectAll(false)
      setCostlySelected([])
  }

  function updateOrdersSearch(value: string) {
    ordersRequestSeqRef.current += 1
    ordersAbortRef.current?.abort()
    setSearch(value)
    setOrdersPage(1)
    setOrdersSelectAll(false)
    setSelected([])
  }

  function updateOrderCondition(value: string) {
    ordersRequestSeqRef.current += 1
    ordersAbortRef.current?.abort()
    setOrderCondition(value)
    setOrdersPage(1)
    setOrdersSelectAll(false)
    setSelected([])
  }

  function updateStoreView(value: string) {
    ordersRequestSeqRef.current += 1
    ordersAbortRef.current?.abort()
    setStoreId(value)
    resetPagination()
  }

  function updateSortKey(value: SortKey) {
    ordersRequestSeqRef.current += 1
    ordersAbortRef.current?.abort()
    setSortKey(value)
    setOrdersPage(1)
    setOrdersSelectAll(false)
    setSelected([])
  }

  function updateSortDirection(value: SortDirection) {
    ordersRequestSeqRef.current += 1
    ordersAbortRef.current?.abort()
    setSortDirection(value)
    setOrdersPage(1)
    setOrdersSelectAll(false)
    setSelected([])
  }

  useEffect(() => {
    ordersPageRef.current = ordersPage
  }, [ordersPage])

  function applyDashboardData(next: DashboardData, nextPage = ordersPage, options: { updateOrders?: boolean } = {}) {
    const updateOrders = options.updateOrders !== false
    if (updateOrders) {
      setData(next)
      setOrderRows(next.rows || [])
      saveDashboardCache(next)
      setOrdersPage(next.page || nextPage)
      setOrdersTotal(next.total || 0)
    } else {
      setData((current) => ({
        ...next,
        rows: current?.rows || orderRows,
        page: ordersPageRef.current,
        total: current?.total || next.total || 0,
      }))
    }
    const resolvedStore = String(next.current_store_id || "")
    setStoreId(resolvedStore)
    setAddressId((current) => current || String(next.addresses.find((address) => address.is_default)?.id || next.addresses[0]?.id || ""))
    setAmazonAccountId((current) => current || String(next.amazon_accounts.find((account) => account.is_default)?.id || next.amazon_accounts[0]?.id || ""))
    setOrderingEngine(next.default_ordering_engine || "rest")
    if (next.pull_orders_days) setDays(String(next.pull_orders_days))
    if (next.pull_orders_limit !== undefined && next.pull_orders_limit !== null) setLimit(String(next.pull_orders_limit))
    setPullStoreIds((current) => {
      const valid = new Set(next.stores.map((store) => String(store.id)))
      const filtered = current.filter((id) => valid.has(id))
      if (filtered.length) return filtered
      return resolvedStore ? [resolvedStore] : next.stores[0]?.id ? [String(next.stores[0].id)] : []
    })
  }

  async function refresh(nextStoreId = storeId, nextPage = ordersPage) {
    const requestSeq = ++dashboardRequestSeqRef.current
    const query = pagedQuery(nextStoreId, nextPage, { per_page: ORDERS_PAGE_SIZE, sort_by: sortKey, sort_dir: sortDirection })
    const next = await api<DashboardData>(`/api/dashboard${query}`)
    if (requestSeq !== dashboardRequestSeqRef.current) return
    applyDashboardData(next, nextPage, { updateOrders: page !== "orders" || ordersPageRef.current === nextPage })
  }

  async function fetchOrdersPageData(nextStoreId: string, nextPage: number, term: string, signal?: AbortSignal) {
    const hasCondition = orderCondition !== "all"
    if (term || hasCondition) {
      return api<DashboardData>(
        `/api/search${pagedQuery(nextStoreId, nextPage, { per_page: ORDERS_PAGE_SIZE, condition: orderCondition, sort_by: sortKey, sort_dir: sortDirection })}&q=${encodeURIComponent(term)}`,
        signal ? { signal } : {},
      )
    }
    return api<{ rows: OrderLine[]; page: number; per_page: number; total: number }>(
      `/api/orders${pagedQuery(nextStoreId, nextPage, { per_page: ORDERS_PAGE_SIZE, condition: orderCondition, sort_by: sortKey, sort_dir: sortDirection })}`,
      signal ? { signal } : {},
    )
  }

  function cacheOrdersPage(cacheKey: string, result: { rows?: OrderLine[]; page?: number; per_page?: number; total?: number }, fallbackPage: number) {
    ordersPageCacheRef.current.set(cacheKey, {
      rows: result.rows || [],
      page: result.page || fallbackPage,
      per_page: result.per_page || ORDERS_PAGE_SIZE,
      total: result.total || 0,
      cachedAt: Date.now(),
    })
  }

  async function refreshOrdersPage(nextStoreId = storeId, nextPage = ordersPage) {
    const term = ordersQuery.trim()
    const cacheKey = ordersCacheKey(nextStoreId, nextPage, term)
    if (ordersInFlightKeyRef.current === cacheKey) return
    const requestSeq = ++ordersRequestSeqRef.current
    ordersAbortRef.current?.abort()
    const controller = new AbortController()
    ordersAbortRef.current = controller
    ordersInFlightKeyRef.current = cacheKey
    setOrdersPage(nextPage)
    setOrdersLoading(true)
    setOrdersTransitioning(true)
    const cachedPage = ordersPageCacheRef.current.get(cacheKey)
    if (cachedPage && Date.now() - cachedPage.cachedAt < 30_000) {
      setOrderRows(cachedPage.rows)
      setOrdersPage(cachedPage.page || nextPage)
      setOrdersTotal(cachedPage.total || 0)
      setData((current) => {
        if (!current) return current
        return { ...current, rows: cachedPage.rows, page: cachedPage.page || nextPage, per_page: cachedPage.per_page, total: cachedPage.total }
      })
    }
    try {
      const result = await fetchOrdersPageData(nextStoreId, nextPage, term, controller.signal)
      if (requestSeq !== ordersRequestSeqRef.current) return
      setOrderRows(result.rows || [])
      setOrdersPage(result.page || nextPage)
      setOrdersTotal(result.total || 0)
      cacheOrdersPage(cacheKey, result, nextPage)
      setData((current) => {
        if (!current) return current
        const next = { ...current, rows: result.rows, page: result.page || nextPage, per_page: result.per_page, total: result.total }
        saveDashboardCache(next)
        return next
      })
    } finally {
      if (requestSeq === ordersRequestSeqRef.current) {
        setOrdersLoading(false)
        setOrdersTransitioning(false)
        if (ordersAbortRef.current === controller) ordersAbortRef.current = null
        if (ordersInFlightKeyRef.current === cacheKey) ordersInFlightKeyRef.current = ""
      }
    }
  }

  async function refreshCurrentOrdersPage() {
    await refreshOrdersPage(storeId, ordersPage)
  }

  async function forceSyncShopifyStatuses() {
    if (!storeId || busy) return
    try {
      setBusy("Force Sync Shopify")
      const result = await api<{ ok: boolean; message: string; progress: ShopifyOrderStatusForceSyncProgress }>("/api/shopify/orders/status/force-sync", {
        method: "POST",
        body: JSON.stringify({ store_id: Number(storeId), batch_size: 25, pause_seconds: 1 }),
      })
      setShopifyStatusForceSync(result.progress)
      shopifyStatusForceSyncPollRef.current = result.progress?.status === "running"
      setModal({ ok: result.ok, title: "Shopify Status Sync", message: result.message || "Shopify status sync started." })
    } catch (error) {
      setModal({ ok: false, title: "Shopify Status Sync", message: String(error) })
    } finally {
      setBusy("")
    }
  }

  async function loadUiCopy() {
    const result = await api<{ copy: UiCopy }>("/api/settings/ui-copy")
    setUiCopy(result.copy || {})
  }

  function copyFor(key: string) {
    return { ...(defaultUiCopy[key] || {}), ...(uiCopy[key] || {}) }
  }

  async function saveUiCopy(key: string, value: { title: string; description: string; icon?: string }) {
    const result = await api<{ copy: UiCopy; message: string }>("/api/settings/ui-copy", {
      method: "POST",
      body: JSON.stringify({ key, title: value.title, description: value.description, icon: value.icon || "" }),
    })
    setUiCopy((current) => ({ ...current, ...(result.copy || {}) }))
    setEditingCopyKey(null)
    setModal({ ok: true, title: "Page Text", message: result.message || "Page text saved." })
  }

  async function handleAdminTokenSave(token: string, remember = true) {
    const nextToken = token.trim()
    if (!nextToken) return
    setAdminAuthBusy(true)
    setAdminAuthError("")
    setModal(null)
    try {
      const next = await apiWithAdminToken<DashboardData>(`/api/dashboard${pagedQuery(storeId, ordersPage, { per_page: ORDERS_PAGE_SIZE, sort_by: sortKey, sort_dir: sortDirection })}`, nextToken)
      window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      if (remember) {
        window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, nextToken)
      } else {
        window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, nextToken)
      }
      applyDashboardData(next, ordersPage)
      loadUiCopy().catch(() => undefined)
      setAdminTokenSaved(true)
      setAdminAccessOpen(false)
      setAdminAuthError("")
    } catch (error) {
      window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      setAdminTokenSaved(false)
      if (error instanceof AdminAuthError) {
        setAdminAuthError("That code did not work. Please check it and try again.")
        setAdminAccessOpen(Boolean(data))
        return
      }
      setAdminAuthError(String(error))
      setAdminAccessOpen(Boolean(data))
    } finally {
      setAdminAuthBusy(false)
    }
  }

  function clearAdminToken() {
    window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
    window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
    window.localStorage.removeItem(DASHBOARD_CACHE_STORAGE_KEY)
    setAdminAuthError("")
    setAdminTokenSaved(false)
    setAdminAccessOpen(true)
  }

  useEffect(() => {
    const listener = () => {
      setAdminAuthError("Admin code required. Please enter a valid code.")
      setAdminAccessOpen(true)
    }
    const savedListener = () => setAdminTokenSaved(Boolean(savedAdminToken()))
    window.addEventListener("admin-auth-required", listener)
    window.addEventListener("admin-token-saved", savedListener)
    return () => {
      window.removeEventListener("admin-auth-required", listener)
      window.removeEventListener("admin-token-saved", savedListener)
    }
  }, [])

  useEffect(() => {
    const onPopState = () => setPage(appPageFromLocation())
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [])

  useEffect(() => {
    const nextPath = pagePath(page)
    if (window.location.pathname !== nextPath) {
      window.history.pushState({ page }, "", nextPath)
    }
  }, [page])

  useEffect(() => {
    const timer = window.setTimeout(() => setOrdersQuery(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    if (!savedAdminToken()) {
      setAdminTokenSaved(false)
      return
    }
    const load = !initialDashboardRefreshStarted.current
      ? (() => {
        initialDashboardRefreshStarted.current = true
        return refresh(storeId, 1).finally(() => {
          initialDashboardRefreshDone.current = true
        })
      })()
      : page === "orders" && data
        ? refreshOrdersPage(storeId, ordersPage)
        : Promise.resolve()
    load.catch((error) => {
      if (!initialDashboardRefreshDone.current) initialDashboardRefreshStarted.current = false
      if ((error as { name?: string })?.name === "AbortError") return
      if (error instanceof AdminAuthError) return
      setModal({ ok: false, title: "Unable to load app", message: String(error) })
    })
    if (!initialDashboardRefreshDone.current) loadUiCopy().catch(() => undefined)
  }, [page, ordersPage, sortKey, sortDirection, storeId, ordersQuery, orderCondition])

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Shift") ordersShiftKeyDown.current = true
    }
    const handleKeyUp = (event: KeyboardEvent) => {
      if (event.key === "Shift") ordersShiftKeyDown.current = false
    }
    const handleBlur = () => {
      ordersShiftKeyDown.current = false
    }
    window.addEventListener("keydown", handleKeyDown)
    window.addEventListener("keyup", handleKeyUp)
    window.addEventListener("blur", handleBlur)
    return () => {
      window.removeEventListener("keydown", handleKeyDown)
      window.removeEventListener("keyup", handleKeyUp)
      window.removeEventListener("blur", handleBlur)
    }
  }, [])

  useEffect(() => {
    if (!selected.length) ordersSelectionAnchor.current = null
  }, [selected.length])

  useEffect(() => {
    window.localStorage.setItem(PULL_DAYS_STORAGE_KEY, days)
  }, [days])

  useEffect(() => {
    window.localStorage.setItem(PULL_LIMIT_STORAGE_KEY, limit)
  }, [limit])

  useEffect(() => {
    window.localStorage.setItem(PULL_STORE_IDS_STORAGE_KEY, JSON.stringify(pullStoreIds))
  }, [pullStoreIds])

  useEffect(() => {
    window.localStorage.setItem(PROFIT_LOSS_PERIOD_STORAGE_KEY, profitLossPeriod)
  }, [profitLossPeriod])

  useEffect(() => {
    if (!openNavGroup) return
    const closeOnOutsideClick = (event: PointerEvent) => {
      const navTabs = navTabsRef.current
      if (navTabs && event.target instanceof Node && !navTabs.contains(event.target)) {
        setOpenNavGroup(null)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenNavGroup(null)
    }
    document.addEventListener("pointerdown", closeOnOutsideClick)
    document.addEventListener("keydown", closeOnEscape)
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick)
      document.removeEventListener("keydown", closeOnEscape)
    }
  }, [openNavGroup])

  useEffect(() => {
    if (page !== "inventory") return
    const query = new URLSearchParams()
    if (storeId) query.set("store_id", storeId)
    query.set("page", String(inventoryPage))
    query.set("per_page", String(PAGE_SIZE))
    api<{ items: InventoryItem[]; total: number }>(`/api/inventory?${query.toString()}`)
      .then((result) => {
        setInventory(result.items)
        setInventoryTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Inventory load failed", message: String(error) }))
  }, [page, storeId, inventoryPage])

  useEffect(() => {
    if (page !== "cancelled-orders") return
    const query = new URLSearchParams()
    if (storeId) query.set("store_id", storeId)
    query.set("page", String(cancelledOrdersPage))
    query.set("per_page", String(PAGE_SIZE))
    api<{ rows: CancelledOrderRow[]; total: number }>(`/api/cancelled-orders?${query.toString()}`)
      .then((result) => {
        setCancelledOrders(result.rows || [])
        setCancelledOrdersTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Cancelled orders load failed", message: String(error) }))
  }, [page, storeId, cancelledOrdersPage])

  useEffect(() => {
    if (page !== "missing") return
    api<{ rows: OrderLine[]; total: number }>(`/api/missing${pagedQuery(storeId, missingPage)}`)
      .then((result) => {
        setMissingRows(result.rows)
        setMissingTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Missing orders load failed", message: String(error) }))
  }, [page, storeId, missingPage])

  useEffect(() => {
    if (page !== "back-in-stock") return
    api<{ rows: BackInStockRow[]; total: number }>(`/api/back-in-stock${pagedQuery(storeId, backInStockPage)}`)
      .then((result) => {
        setBackInStockRows(result.rows || [])
        setBackInStockTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Back in stock load failed", message: String(error) }))
  }, [page, storeId, backInStockPage])

  useEffect(() => {
    if (page !== "partial-fulfilments" && page !== "home") return
    api<{ rows: PartialFulfilment[]; total: number }>(`/api/partial-fulfilments${pagedQuery(storeId, partialFulfilmentsPage)}`)
      .then((result) => {
        setPartialFulfilments(result.rows || [])
        setPartialFulfilmentsTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Partial fulfilments load failed", message: String(error) }))
  }, [page, storeId, partialFulfilmentsPage])

  useEffect(() => {
    if (page !== "fulfilment-pending") return
    api<{ rows: FulfilmentPendingRow[]; total: number }>(`/api/tracking/fulfilment-pending${pagedQuery(storeId, fulfilmentPendingPage)}`)
      .then((result) => {
        setFulfilmentPendingRows(result.rows)
        setFulfilmentPendingTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Fulfilment pending load failed", message: String(error) }))
  }, [page, storeId, fulfilmentPendingPage])

  useEffect(() => {
    if (page !== "payment-failed") return
    api<{ rows: PaymentFailure[]; total: number }>(`/api/tracking/payment-failures${pagedQuery(storeId, paymentFailuresPage)}`)
      .then((result) => {
        setPaymentFailures(result.rows || [])
        setPaymentFailuresTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Payment failed load failed", message: String(error) }))
  }, [page, storeId, paymentFailuresPage])

  useEffect(() => {
    if (page !== "epost") return
    api<{ rows: EpostTrackingRow[]; total: number }>(`/api/epost/tracking${pagedQuery(storeId, epostPage, { status: epostStatusFilter })}`)
      .then((result) => {
        setEpostRows(result.rows)
        setEpostTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "ePost tracking load failed", message: String(error) }))
  }, [page, storeId, epostPage, epostStatusFilter])

  useEffect(() => {
    if (page !== "bulk" || !storeId) return
    const requestedDays = bulkDays || "2"
    api<{ groups: BulkGroup[]; total: number; days: number; search_engine?: string }>(`/api/bulk${pagedQuery(storeId, bulkPage, { days: requestedDays })}`)
      .then((result) => {
        setBulkGroups(result.groups)
        setBulkTotal(result.total || 0)
        setActiveBulkDays(result.days || Number(requestedDays) || 2)
      })
      .catch((error) => setModal({ ok: false, title: "Bulk opportunities load failed", message: String(error) }))
  }, [page, storeId, bulkPage, bulkDays])

  async function refreshChromeQueue(nextStoreId = storeId) {
    const query = new URLSearchParams()
    if (nextStoreId) query.set("store_id", nextStoreId)
    query.set("claim", "false")
    const result = await api<{ jobs: ChromeQueueJob[]; counts: ChromeQueueCount[] }>(`/api/chrome/jobs?${query.toString()}`)
    setChromeQueueJobs(result.jobs || [])
    setChromeQueueCounts(result.counts || [])
  }

  useEffect(() => {
    if (page !== "chrome-queue") return
    refreshChromeQueue().catch((error) => setModal({ ok: false, title: "Chrome queue load failed", message: String(error) }))
  }, [page, storeId])

  useEffect(() => {
    if (page !== "orders" || !storeId) return
    const query = new URLSearchParams()
    query.set("store_id", storeId)
    query.set("page", String(duplicateAsinPage))
    query.set("per_page", String(DUPLICATE_ASIN_PAGE_SIZE))
    const requestedDays = duplicateAsinDays || "2"
    query.set("days", requestedDays)
    api<{ groups: DuplicateAsin[]; total: number; days: number; search_engine?: string }>(`/api/duplicate-asins?${query.toString()}`)
      .then((result) => {
        setDuplicateAsins(result.groups)
        setDuplicateAsinTotal(result.total || 0)
        setActiveDuplicateAsinDays(result.days || Number(requestedDays) || 2)
      })
      .catch((error) => setModal({ ok: false, title: "Duplicate ASINs load failed", message: String(error) }))
  }, [page, storeId, duplicateAsinPage, duplicateAsinDays])

  useEffect(() => {
    if (page !== "costly") return
    api<{ rows: OrderLine[]; total: number }>(`/api/costly${pagedQuery(storeId, costlyPage)}`)
      .then((result) => {
        setCostlyRows(result.rows)
        setCostlyTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Costly orders load failed", message: String(error) }))
  }, [page, storeId, costlyPage])

  useEffect(() => {
    window.localStorage.setItem(
      ORDER_TABLE_STORAGE_KEY,
      JSON.stringify(orderColumns.map((column) => ({ key: column.key, visible: column.visible !== false }))),
    )
  }, [orderColumns])

  const rows = orderRows
  useEffect(() => {
    if (page !== "orders" || !storeId || !rows.length) return
    const orderNames = Array.from(new Set(rows.map((row) => row.odoo_order_name).filter(Boolean))).slice(0, 100)
    if (!orderNames.length) return
    const missingStatus = rows.some((row) => !row.shopify_order_id && !row.shopify_fulfillment_status)
      || rows.some((row) => String(row.shopify_fulfillment_status || "").toLowerCase().includes("fulfilled") && !row.shopify_fulfillment_at)
    if (!missingStatus) return
    const syncKey = `${storeId}:${orderNames.join(",")}`
    if (shopifyStatusSyncKeyRef.current === syncKey) return
    shopifyStatusSyncKeyRef.current = syncKey
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      api<{ ok: boolean; synced: number }>("/api/shopify/orders/status/sync", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({ store_id: Number(storeId), order_names: orderNames }),
      })
      .then((result) => {
        if (result.synced) ordersPageCacheRef.current.clear()
      })
      .catch(() => {
        // Shopify status is auxiliary on the Orders table; keep the page usable if OAuth is missing.
      })
    }, 1200)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [page, storeId, rows])
  useEffect(() => {
    if (page !== "orders") return
    let active = true
    const load = async () => {
      try {
        const result = await api<{ ok: boolean; progress: ShopifyOrderStatusForceSyncProgress }>("/api/shopify/orders/status/force-sync")
        if (!active) return
        const wasForceSyncPolling = shopifyStatusForceSyncPollRef.current
        setShopifyStatusForceSync(result.progress)
        shopifyStatusForceSyncPollRef.current = result.progress?.status === "running"
        if (result.progress?.status === "completed") {
          const completedKey = `${result.progress.completed_at || ""}:${result.progress.processed || 0}:${result.progress.total || 0}`
          if (shopifyStatusCompletedRefreshRef.current === completedKey) return
          shopifyStatusCompletedRefreshRef.current = completedKey
          if (wasForceSyncPolling) refreshOrdersPage(storeId, ordersPage).catch(() => undefined)
        }
      } catch {
        // Background progress is informational only.
      }
    }
    load()
    const timer = window.setInterval(() => {
      if (shopifyStatusForceSyncPollRef.current || shopifyStatusForceSync?.status === "running") load()
    }, 5000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [page, storeId, ordersPage, shopifyStatusForceSync?.status])
  const filteredRows = useMemo(() => {
    if (ordersTransitioning) return []
    return rows
  }, [rows, ordersTransitioning])
  const sortedRows = filteredRows
  const visibleOrderColumns = orderColumns.filter((column) => column.visible !== false)
  const orderExportColumns = visibleOrderColumns.map((column) => ({ key: column.key === "store" ? "store_name" : column.key === "odoo_order" ? "odoo_order_name" : column.key === "product" ? "product_name" : column.key === "reference" ? "default_code" : column.key === "qty" ? "quantity" : column.key === "odoo_status" ? "odoo_status_label" : column.key === "amazon_account" ? "amazon_account_name" : column.key === "tracking" ? "tracking_status" : column.key === "amazon_order" ? "amazon_order_id" : column.key === "shopify_order" ? "shopify_order_name" : column.key === "shopify_fulfillment" ? "shopify_fulfillment_status" : column.key === "shopify_fulfillment_at" ? "shopify_fulfillment_at" : column.key === "comments" ? "fulfilment_note" : column.key === "error" ? "last_error" : column.key, label: column.label }))
  rows.forEach((row) => {
    if (selected.includes(row.id)) selectedOrderRowsRef.current.set(row.id, row)
  })
  if (!selected.length && selectedOrderRowsRef.current.size) selectedOrderRowsRef.current.clear()
  const currentRowsById = useMemo(() => new Map(rows.map((row) => [row.id, row])), [rows])
  const selectedRows = selected
    .map((id) => currentRowsById.get(id) || selectedOrderRowsRef.current.get(id))
    .filter((row): row is OrderLine => Boolean(row))
  const selectedStoreIds = Array.from(new Set(selectedRows.map((row) => Number(row.store_id || 0)).filter(Boolean)))
  const selectedActionStoreId = selected.length > 0 && selectedRows.length === selected.length && selectedStoreIds.length === 1 ? selectedStoreIds[0] : null
  const canRunSelectedStoreAction = Boolean(selected.length && selectedActionStoreId)
  const selectedClubName = selectedRows.length
    ? `Nutricity ${Array.from(new Set(selectedRows.map((row) => row.odoo_order_name))).join(" ")}`
    : ""

  function isSelectionControlClick(event: MouseEvent<HTMLElement>) {
    const target = event.target
    return target instanceof Element && Boolean(target.closest("a, button, input, select, textarea, [role='button']"))
  }

  function selectOrderRow(row: OrderLine, shiftKey: boolean, checked?: boolean) {
    setOrdersSelectAll(false)
    selectedOrderRowsRef.current.set(row.id, row)
    const visibleIds = sortedRows.map((item) => item.id)
    const previousAnchor = ordersSelectionAnchor.current
    setSelected((current) => {
      const anchor = previousAnchor ?? current[current.length - 1] ?? null
      const shouldCheck = checked ?? !current.includes(row.id)
      const next = rangeSelection(visibleIds, current, row.id, shouldCheck, shiftKey, anchor)
      sortedRows.forEach((item) => {
        if (next.includes(item.id)) selectedOrderRowsRef.current.set(item.id, item)
        else if (visibleIds.includes(item.id)) selectedOrderRowsRef.current.delete(item.id)
      })
      if (!next.length) ordersSelectionAnchor.current = null
      return next
    })
    ordersSelectionAnchor.current = row.id
  }

  function selectOrderCheckbox(row: OrderLine, event: MouseEvent<HTMLButtonElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()
    const shiftKey = Boolean(event.shiftKey || event.getModifierState("Shift") || ordersShiftKeyDown.current)
    selectOrderRow(row, shiftKey)
  }

  function selectOrderCheckboxFromKeyboard(row: OrderLine, event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== " " && event.key !== "Enter") return
    event.preventDefault()
    event.stopPropagation()
    const shiftKey = Boolean(event.shiftKey || event.getModifierState("Shift") || ordersShiftKeyDown.current)
    selectOrderRow(row, shiftKey, !selected.includes(row.id))
  }

  function selectedStoreIdForAction(title: string) {
    if (selectedActionStoreId) return selectedActionStoreId
    setModal({
      ok: false,
      title,
      message: selected.length
        ? "Select order lines from one store only. Store-specific actions use the selected rows' store, not the global filter."
        : "Select at least one order line.",
    })
    return null
  }

  function placeSelectedOrders() {
    const actionStoreId = selectedStoreIdForAction("Place Selected")
    if (!actionStoreId) return
    return runAction("Place Selected", () =>
      api<DashboardData>("/api/place", {
        method: "POST",
        body: JSON.stringify({
          store_id: actionStoreId,
          address_id: Number(addressId),
          amazon_account_id: Number(amazonAccountId),
          line_ids: selected,
          ordering_engine: orderingEngine,
          allow_missing_spaid: allowMissingSpaid,
          include_missing_asins: resendMissingAsins,
        }),
      }),
    )
  }

  function clubPlaceSelectedOrders() {
    const actionStoreId = selectedStoreIdForAction("Club Place Selected")
    if (!actionStoreId) return
    return runAction("Club Place Selected", () =>
      api<DashboardData>("/api/place", {
        method: "POST",
        body: JSON.stringify({
          store_id: actionStoreId,
          address_id: Number(addressId),
          amazon_account_id: Number(amazonAccountId),
          line_ids: selected,
          club: true,
          ordering_engine: orderingEngine,
          allow_missing_spaid: allowMissingSpaid,
          include_missing_asins: resendMissingAsins,
        }),
      }),
    )
  }

  function placeRecentChromeOrders() {
    if (!storeId || busy) return
    setPlaceRecentDays(days || "5")
    setPlaceRecentConfirmOpen(true)
  }

  async function confirmPlaceRecentChromeOrders() {
    if (!storeId || busy) return
    const requestedDays = Math.max(1, Math.min(365, Number(placeRecentDays || 0)))
    if (!Number.isFinite(requestedDays) || requestedDays < 1) {
      setModal({ ok: false, title: "Place Recent Orders", message: "Enter a valid day count." })
      return
    }
    setPlaceRecentConfirmOpen(false)
    const title = "Place Recent Orders"
    try {
      setBusy(title)
      const result = await api<DashboardData & { queued?: number; pulled?: number; skipped?: number }>("/api/place/recent-chrome", {
        method: "POST",
        body: JSON.stringify({
          store_id: Number(storeId),
          address_id: Number(addressId || 0) || null,
          amazon_account_id: Number(amazonAccountId || 0) || null,
          days: requestedDays,
          include_missing_asins: resendMissingAsins,
        }),
      })
      applyDashboardData(result)
      if (Number(result.queued || 0) > 0) {
        setPage("chrome-queue")
        await refreshChromeQueue(String(result.current_store_id || storeId))
      }
      setModal({ ok: result.ok ?? true, title, message: result.message || "Done." })
    } catch (error) {
      setModal({ ok: false, title, message: String(error) })
    } finally {
      setBusy("")
    }
  }

  function resetSelectedOrders() {
    if (!selected.length || busy) return
    if (!selectedStoreIdForAction("Reset Selected")) return
    setResetFulfilmentConfirmOpen(true)
  }

  function confirmResetSelectedOrders() {
    const actionStoreId = selectedStoreIdForAction("Reset Selected")
    if (!actionStoreId) return
    setResetFulfilmentConfirmOpen(false)
    return runAction("Reset Selected", () =>
      api<DashboardData>("/api/lines/reset-fulfilment", {
        method: "POST",
        body: JSON.stringify({ store_id: actionStoreId, line_ids: selected }),
      }),
    )
  }

  function ignoreSelectedOrders() {
    const actionStoreId = selectedStoreIdForAction("Mark Do Not Process")
    if (!actionStoreId) return
    return runAction("Mark Do Not Process", () =>
      api<DashboardData>("/api/lines/ignore", {
        method: "POST",
        body: JSON.stringify({ store_id: actionStoreId, line_ids: selected }),
      }),
    )
  }

  function deleteSelectedOrders() {
    const actionStoreId = selectedStoreIdForAction("Delete Selected Lines")
    if (!actionStoreId) return
    return runAction("Delete Selected Lines", () =>
      api<DashboardData>("/api/lines/delete", {
        method: "POST",
        body: JSON.stringify({ store_id: actionStoreId, line_ids: selected }),
      }),
    )
  }

  async function runAction(title: string, fn: () => Promise<DashboardData | { message?: string; ok?: boolean; defer_refresh?: boolean }>) {
    try {
      setBusy(title)
      const result = await fn()
      if ("rows" in result) {
        await refreshCurrentOrdersPage()
        if (result.punchout_launch_url) {
          window.open(result.punchout_launch_url, "_blank")
        }
      } else if (result.defer_refresh) {
        window.setTimeout(() => {
          void refreshCurrentOrdersPage().catch((error) => setModal({ ok: false, title: "Refresh Failed", message: String(error) }))
        }, 2500)
      } else {
        await refreshCurrentOrdersPage()
      }
      setSelected([])
      setModal({ ok: "ok" in result ? result.ok ?? true : true, title, message: result.message || "Done." })
    } catch (error) {
      setModal({ ok: false, title, message: String(error) })
    } finally {
      setBusy("")
    }
  }

  async function runPullOrders() {
    const title = "Pull Orders"
    try {
      const selectedStoreIds = pullStoreIds.map(Number).filter(Boolean)
      if (!selectedStoreIds.length) {
        setModal({ ok: false, title, message: "Select at least one store to pull orders." })
        return
      }
      setBusy(title)
      const result = await api<{ ok?: boolean; message?: string }>("/api/pull", {
        method: "POST",
        body: JSON.stringify({ store_ids: selectedStoreIds, days: Number(days), limit: Number(limit), refresh: false }),
      })
      setSelected([])
      setPage("pull-jobs")
      setModal({ ok: result.ok ?? true, title, message: result.message || "Started background pull jobs." })
    } catch (error) {
      setModal({ ok: false, title, message: String(error) })
    } finally {
      setBusy("")
    }
  }

  async function runPullOrderNames() {
    const title = "Pull Odoo Orders"
    const orderRefs = Array.from(new Set((pullOrderNames.match(/\bNC\d+\b/gi) || []).map((value) => value.toUpperCase())))
    if (!storeId) {
      setModal({ ok: false, title, message: "Select a store first." })
      return
    }
    if (!orderRefs.length) {
      setModal({ ok: false, title, message: "Enter at least one Odoo order number, for example NC10216." })
      return
    }
    try {
      setBusy(title)
      const result = await api<{ ok?: boolean; message?: string; pulled?: number }>("/api/pull/order-names", {
        method: "POST",
        body: JSON.stringify({ store_id: Number(storeId), order_names: orderRefs }),
      })
      await refreshCurrentOrdersPage()
      setModal({ ok: result.ok ?? true, title, message: result.message || "Pulled requested Odoo orders." })
    } catch (error) {
      setModal({ ok: false, title, message: String(error) })
    } finally {
      setBusy("")
    }
  }

  async function savePullDefaults() {
    const title = "Save Pull Defaults"
    try {
      setBusy(title)
      const result = await api<{ ok: boolean; message: string; days: number; limit: number }>("/api/settings/pull-defaults", {
        method: "POST",
        body: JSON.stringify({ store_ids: pullStoreIds.map(Number).filter(Boolean), days: Number(days), limit: Number(limit), refresh: false }),
      })
      setDays(String(result.days))
      setLimit(String(result.limit))
      setModal({ ok: result.ok, title, message: result.message })
    } catch (error) {
      setModal({ ok: false, title, message: String(error) })
    } finally {
      setBusy("")
    }
  }

  function setColumnVisibility(key: OrderColumnKey, visible: boolean) {
    setOrderColumns((current) => current.map((column) => (column.key === key ? { ...column, visible } : column)))
  }

  function moveColumn(dragKey: OrderColumnKey, targetKey: OrderColumnKey, after = false) {
    if (dragKey === targetKey) return
    setOrderColumns((current) => {
      const next = [...current]
      const from = next.findIndex((column) => column.key === dragKey)
      const to = next.findIndex((column) => column.key === targetKey)
      if (from < 0 || to < 0) return current
      const [moved] = next.splice(from, 1)
      const targetIndex = next.findIndex((column) => column.key === targetKey)
      if (targetIndex < 0) return current
      next.splice(targetIndex + (after ? 1 : 0), 0, moved)
      return next
    })
  }

  function updateColumnDropTarget(event: DragEvent<HTMLElement>, key: OrderColumnKey) {
    event.preventDefault()
    const rect = event.currentTarget.getBoundingClientRect()
    setColumnDropTarget({ key, after: event.clientX > rect.left + rect.width / 2 })
  }

  function applySort(nextSortKey: SortKey) {
    if (sortKey === nextSortKey) {
      updateSortDirection(sortDirection === "asc" ? "desc" : "asc")
      return
    }
    updateSortKey(nextSortKey)
    setSortDirection(nextSortKey === "odoo_order_name" ? "asc" : "desc")
  }

  async function copyText(value: string, label: string) {
    const text = String(value || "").trim()
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setModal({ ok: true, title: "Copied", message: `${label} copied: ${text}` })
    } catch (error) {
      setModal({ ok: false, title: "Copy Failed", message: String(error) })
    }
  }

  function renderOrderCell(row: OrderLine, column: OrderColumn) {
    switch (column.key) {
      case "store":
        return row.store_name ? <Badge variant="outline">{row.store_name}</Badge> : <span className="text-muted-foreground">-</span>
      case "odoo_order":
        return <OdooOrderRef name={row.odoo_order_name} url={row.odoo_order_url} linkClassName={row.state === "missing" ? "text-destructive" : ""} />
      case "product":
        return (
          <Tooltip>
            <TooltipTrigger className="block w-full cursor-help truncate text-left">
              {row.product_name}
            </TooltipTrigger>
            <TooltipContent side="top" align="start" className="max-w-xl whitespace-normal leading-relaxed">
              {row.product_name}
            </TooltipContent>
          </Tooltip>
        )
      case "reference":
        return (
          <button
            type="button"
            className="block max-w-full truncate text-left font-mono text-xs"
            onDoubleClick={() => copyText(row.default_code, "Reference number")}
            data-bs-toggle="tooltip"
            data-bs-placement="top"
            title="Double-click to copy"
          >
            {row.default_code}
          </button>
        )
      case "odoo_order_date":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.odoo_order_date)}</span>
      case "destination_country":
        return row.destination_country ? (
          <Badge variant="outline">{row.destination_country}</Badge>
        ) : (
          <span className="text-muted-foreground">-</span>
        )
      case "pulled_at":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.pulled_at || row.created_at)}</span>
      case "ordered_at":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.ordered_at)}</span>
      case "asin":
        return row.asin ? (
          row.replacement_asin ? (
            <Tooltip>
              <TooltipTrigger
                className="inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 font-mono text-xs font-semibold text-amber-900 underline-offset-4 hover:underline"
                onClick={() => window.open(`https://www.amazon.com/dp/${row.asin}`, "_blank")}
              >
                {row.asin}
              </TooltipTrigger>
              <TooltipContent side="top" align="start" className="max-w-sm whitespace-normal leading-relaxed">
                Original ASIN: {row.original_asin || "unknown"}<br />
                Replacement ASIN: {row.replacement_asin}
                {row.replacement_note ? <><br />Note: {row.replacement_note}</> : null}
              </TooltipContent>
            </Tooltip>
          ) : (
            <a className="text-primary underline-offset-4 hover:underline" href={`https://www.amazon.com/dp/${row.asin}`} target="_blank">
              {row.asin}
            </a>
          )
        ) : ""
      case "spaid":
        return (
          <button
            type="button"
            className="max-w-[160px] truncate font-mono text-xs text-primary underline-offset-4 hover:underline"
            onClick={() => setEditingSpaid(row)}
          >
            {row.supplier_part_auxiliary_id || "Add SPAID"}
          </button>
        )
      case "qty":
        return row.quantity
      case "odoo_status":
        return <StatusBadge value={row.odoo_status_label} />
      case "inventory":
        return Number(row.inventory_quantity || 0) > 0 ? <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">use inventory: {row.inventory_quantity}</Badge> : ""
      case "state":
        return <StatusBadge value={row.state} />
      case "engine":
        return row.amazon_order_id || !["pulled", "error"].includes(row.state) ? <Badge variant="outline">{row.order_engine}</Badge> : ""
      case "amazon_account":
        return row.amazon_account_name
      case "tracking":
        return row.tracking_status
      case "cancelled_earlier":
        return row.amazon_cancelled_at ? <Badge className="bg-red-100 text-red-700 hover:bg-red-100">Yes</Badge> : <Badge variant="outline">No</Badge>
      case "amazon_order":
        return row.amazon_order_url || row.amazon_cancelled_order_id ? (
          <a className="text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
            {row.amazon_order_id || row.amazon_cancelled_order_id}
          </a>
        ) : (
          row.amazon_order_id
        )
      case "shopify_order":
        return row.shopify_order_id || row.shopify_order_name ? (
          row.shopify_order_url ? (
            <a className="text-primary underline-offset-4 hover:underline" href={row.shopify_order_url} target="_blank">
              {row.shopify_order_name || row.shopify_order_id}
            </a>
          ) : (
            <span className="font-mono text-xs">{row.shopify_order_name || row.shopify_order_id}</span>
          )
        ) : (
          ""
        )
      case "shopify_fulfillment":
        return row.shopify_fulfillment_status ? <StatusBadge value={row.shopify_fulfillment_status} /> : ""
      case "shopify_fulfillment_at":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.shopify_fulfillment_at)}</span>
      case "comments":
        return row.fulfilment_note ? (
          <Tooltip>
            <TooltipTrigger className="block w-full cursor-help truncate text-left text-muted-foreground">
              {row.fulfilment_note}
            </TooltipTrigger>
            <TooltipContent side="top" align="end" className="max-w-xl whitespace-normal break-words leading-relaxed">
              {row.fulfilment_note}
            </TooltipContent>
          </Tooltip>
        ) : ""
      case "error":
        return row.last_error ? (
          <Tooltip>
            <TooltipTrigger className="block w-full cursor-help truncate text-left text-destructive">
              {row.missing_asin ? (
                <>
                  ASIN{" "}
                  <a className="underline underline-offset-4" href={`https://www.amazon.com/dp/${row.missing_asin}`} target="_blank">
                    {row.missing_asin}
                  </a>{" "}
                  missing; skipped.
                </>
              ) : row.last_error}
            </TooltipTrigger>
            <TooltipContent side="top" align="end" className="max-w-xl whitespace-normal break-words leading-relaxed">
              {row.last_error}
            </TooltipContent>
          </Tooltip>
        ) : ""
      default:
        return ""
    }
  }

  const stores = data?.stores || []
  const addresses = data?.addresses || []
  const accounts = data?.amazon_accounts || []
  const punchoutReturnUrls = data?.punchout_return_urls || []
  const selectedAmazonAccount = accounts.find((account) => String(account.id) === amazonAccountId)
  const engineLabel = orderingEngine === "rest" ? "REST Ordering API" : orderingEngine === "cxml" ? "cXML Punchout" : "Chrome Extension"
  const currentPageCopy = copyFor(page)
  const headerCopy = copyFor("app_header")
  const pageTitle = currentPageCopy.title || "Dashboard"
  const profitLossPeriodLabel = profitLossPeriod.charAt(0).toUpperCase() + profitLossPeriod.slice(1)
  const HeaderIcon = uiIconComponents[(headerCopy.icon || "package") as keyof typeof uiIconComponents] || PackageCheck
  const navGroups = [
    {
      key: "operations",
      label: "Operations",
      icon: ShoppingCart,
      items: [
        ["orders", "Orders", ShoppingCart],
        ["pull-jobs", "Pull Jobs", RefreshCw],
        ["chrome-queue", "Chrome Queue", Lock],
        ["bulk", "Bulk Ordering", PackageCheck],
        ["shopify-fulfilment", "Shopify Fulfilment", StoreIcon],
        ["missing", "Missing ASINs", AlertCircle],
        ["back-in-stock", "Back In Stock", CheckCircle2],
        ["partial-fulfilments", "Partial Fulfilments", AlertCircle],
        ["costly", "Cost Review", AlertCircle],
        ["fulfilment-pending", "Pending Dispatch", AlertCircle],
        ["inventory", "Inventory", PackageCheck],
        ["cancelled-orders", "Cancelled Orders", AlertCircle],
      ],
    },
    {
      key: "tracking",
      label: "Tracking",
      icon: PackageCheck,
      items: [
        ["tracking", "Amazon Tracking", PackageCheck],
        ["payment-failed", "Payment Failed", AlertCircle],
        ["amazon-otp", "Amazon OTP", Bell],
        ["epost", "ePost Global", PackageCheck],
        ["duplicate-tracking", "Duplicate Tracking", AlertCircle],
        ["shopify-tracking", "Shopify Tracking", StoreIcon],
      ],
    },
    {
      key: "finance",
      label: "Finance",
      icon: Database,
      items: [
        ["profit-loss", "Profit / Loss", Database],
        ["accounting", "Accounting", Database],
        ["downloads", "Downloads", Download],
      ],
    },
  ] as const
  const activeNavGroup = navGroups.find((group) => group.items.some((item) => item[0] === page))

  if (!adminTokenSaved && !data) {
    return (
      <TooltipProvider>
        <AdminAccessScreen onSubmit={handleAdminTokenSave} error={adminAuthError} busy={adminAuthBusy} />
        <ResultDialog modal={modal} onClose={() => setModal(null)} />
      </TooltipProvider>
    )
  }

  return (
    <TooltipProvider>
    <div className="page tabler-admin-shell min-h-screen bg-muted/30 text-foreground">
      <ResultDialog modal={adminAccessOpen ? null : modal} onClose={() => setModal(null)} />
      <UiCopyDialog
        copyKey={editingCopyKey}
        value={editingCopyKey ? copyFor(editingCopyKey) : {}}
        onClose={() => setEditingCopyKey(null)}
        onSave={(value) => editingCopyKey ? saveUiCopy(editingCopyKey, value) : Promise.resolve()}
      />
      <ManualFulfilmentDialog
        open={manualFulfilmentOpen}
        selectedCount={selected.length}
        onClose={() => setManualFulfilmentOpen(false)}
	        onSubmit={async (payload) => {
	          const actionStoreId = selectedStoreIdForAction("Manual Fulfilment")
	          if (!actionStoreId) return
	          await runAction("Manual Fulfilment", () =>
	            api<DashboardData>("/api/lines/manual-fulfilment", {
	              method: "POST",
	              body: JSON.stringify({ store_id: actionStoreId, line_ids: selected, ...payload }),
	            }),
	          )
          setManualFulfilmentOpen(false)
          setSelected([])
        }}
      />
      <Dialog open={placeRecentConfirmOpen} onOpenChange={(open) => !open && setPlaceRecentConfirmOpen(false)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShoppingCart className="size-5" />
              Place recent orders?
            </DialogTitle>
            <DialogDescription>
              Pull and queue recent Chrome orders for the selected store, address, and Amazon account.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-1.5">
              <Label>Days</Label>
              <Input
                type="number"
                min="1"
                max="365"
                value={placeRecentDays}
                onChange={(event) => setPlaceRecentDays(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">Today is 1; enter 5 for today plus the previous 4 days.</p>
            </div>
            <label className="flex w-fit cursor-pointer items-center gap-2 text-sm">
              <Checkbox checked={resendMissingAsins} onCheckedChange={(checked) => setResendMissingAsins(Boolean(checked))} />
              <span className="ml-2">Retry missing/out-of-stock ASINs too</span>
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={Boolean(busy)} onClick={() => setPlaceRecentConfirmOpen(false)}>
              No, cancel
            </Button>
            <Button disabled={Boolean(busy) || !storeId || !addressId || !amazonAccountId} onClick={confirmPlaceRecentChromeOrders}>
              {busy === "Place Recent Orders" ? "Pulling and queueing..." : "Place Recent"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={resetFulfilmentConfirmOpen} onOpenChange={(open) => !open && setResetFulfilmentConfirmOpen(false)}>
        <DialogContent className="border-destructive/50 sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertCircle className="size-5" />
              Reset selected fulfilment?
            </DialogTitle>
            <DialogDescription>
              This will clear Amazon order IDs, Chrome job state, tracking, pricing, and errors for {selected.length.toLocaleString()} selected line{selected.length === 1 ? "" : "s"}. Replacement ASINs are kept and must be removed manually from the orders page.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            Do you really want to reset these orders, or was this accidental?
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={Boolean(busy)} onClick={() => setResetFulfilmentConfirmOpen(false)}>
              No, cancel
            </Button>
            <Button variant="destructive" disabled={Boolean(busy) || !selected.length} onClick={confirmResetSelectedOrders}>
              Yes, reset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <AdminAccessDialog open={adminAccessOpen} onSubmit={handleAdminTokenSave} onClose={() => setAdminAccessOpen(false)} error={adminAuthError} busy={adminAuthBusy} />
      <header className="navbar navbar-expand-md d-print-none">
        <div className="container-xl flex items-center justify-between gap-4 py-3">
          <button className="navbar-brand" onClick={() => setPage("home")}>
            <div className="avatar avatar-sm bg-primary text-primary-fg">
              <HeaderIcon className="size-4" />
            </div>
            <div>
              <h1 className="navbar-brand-title">{headerCopy.title}</h1>
              <p className="navbar-brand-subtitle">{headerCopy.description}</p>
            </div>
          </button>
          <Button variant="ghost" size="icon-sm" onClick={() => setEditingCopyKey("app_header")} title="Edit header text">
            <Edit className="size-4" />
          </Button>
          <div className="flex items-center gap-2">
            <button className="btn btn-icon" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Day mode"><Sun className="size-5" /></button>
            <button className="btn btn-icon" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Dark mode"><Moon className="size-5" /></button>
            <button className="btn btn-icon relative" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Notifications">
              <Bell className="size-5" />
              {(missingRows.length + partialFulfilments.length + costlyRows.length + fulfilmentPendingRows.length) > 0 && <span className="absolute right-1 top-1 size-2 rounded-full bg-red" />}
            </button>
            <button className={`btn btn-icon ${adminTokenSaved ? "text-green" : "text-yellow"}`} data-bs-toggle="tooltip" data-bs-placement="bottom" title={adminTokenSaved ? "Admin code saved on this PC" : "Enter admin code"} onClick={() => { setAdminAuthError(""); setAdminAccessOpen(true) }}>
              <Lock className="size-5" />
            </button>
            <button className="nav-link d-flex lh-1 text-reset" onClick={() => setPage("settings")}>
              <div className="avatar avatar-sm bg-blue-lt text-blue"><UserCircle className="size-5" /></div>
              <div className="hidden text-left text-sm md:block">
                <div className="font-medium">Admin Team</div>
                <div className="text-xs text-muted-foreground">{adminTokenSaved ? "Access saved" : "Code required"}</div>
              </div>
            </button>
            {adminTokenSaved && (
              <button className="btn btn-icon" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Forget admin code on this PC" onClick={clearAdminToken}>
                <Logout className="size-5" />
              </button>
            )}
          </div>
        </div>
      </header>
      <nav className="navbar-tabs d-print-none" ref={navTabsRef}>
        <div className="container-xl flex items-center justify-between gap-4">
          <ul className="navbar-nav nav-tabs">
            <li className={`nav-item ${page === "home" ? "active" : ""}`}>
              <button className={`nav-link ${page === "home" ? "active" : ""}`} onClick={() => { setPage("home"); setOpenNavGroup(null) }}>
                <span className="nav-link-icon"><Home className="size-4" /></span>
                <span className="nav-link-title">Home</span>
              </button>
            </li>
            {navGroups.map((group) => {
              const Icon = group.icon
              const groupActive = activeNavGroup?.key === group.key
              return (
                <li key={group.key} className={`nav-item dropdown ${groupActive ? "active" : ""}`}>
                  <button
                    className={`nav-link dropdown-toggle ${groupActive ? "active" : ""}`}
                    onClick={() => setOpenNavGroup(openNavGroup === group.key ? null : group.key)}
                    aria-expanded={openNavGroup === group.key}
                  >
                    <span className="nav-link-icon"><Icon className="size-4" /></span>
                    <span className="nav-link-title">{group.label}</span>
                    <ChevronDown className={`size-4 transition-transform ${openNavGroup === group.key ? "rotate-180" : ""}`} />
                  </button>
                  {openNavGroup === group.key && (
                    <div className="dropdown-menu show">
                      {group.items.map(([key, label, ItemIcon]) => (
                        <button
                          key={key}
                          className={`dropdown-item ${page === key ? "active" : ""}`}
                          onClick={() => {
                            setPage(key)
                            setOpenNavGroup(null)
                          }}
                        >
                          <ItemIcon className="size-4" />
                          <span>{label}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          <button className={`nav-link ms-auto ${page === "settings" ? "active" : ""}`} onClick={() => { setPage("settings"); setOpenNavGroup(null) }}>
              <span className="nav-link-icon"><Settings className="size-4" /></span>
              <span className="nav-link-title">Settings</span>
            </button>
        </div>
      </nav>

      <main className="page-wrapper">
      <div className="page-header d-print-none">
        <div className="container-xl">
          <div className="page-header-row">
            <div>
              <div className="page-pretitle">Control panel</div>
              <div className="flex items-center gap-2">
                <h2 className="page-title">{pageTitle}</h2>
                <Button variant="ghost" size="icon-sm" onClick={() => setEditingCopyKey(page)} title="Edit page text">
                  <Edit className="size-4" />
                </Button>
              </div>
              {currentPageCopy.description && <p className="mt-1 text-sm text-muted-foreground">{currentPageCopy.description}</p>}
              {page === "profit-loss" && (
                <p className="mt-1 text-sm font-medium text-primary">Current view: {profitLossPeriodLabel}</p>
              )}
            </div>
            <div className="page-actions">
              {storeId && <span className="badge bg-blue-lt text-blue">{stores.find((store) => String(store.id) === storeId)?.name || "Store selected"}</span>}
              {busy && <span className="badge bg-yellow-lt text-yellow">{busy}</span>}
            </div>
          </div>
        </div>
      </div>
      <div className="page-body">
      <div className="container-xl page-content">
        {data?.message && (
          <Alert>
            <CheckCircle2 className="size-4" />
            <AlertTitle>Last result</AlertTitle>
            <AlertDescription>{data.message}</AlertDescription>
          </Alert>
        )}

        {page === "home" && data && (
          <HomeDashboard
            data={data}
            missingRows={missingRows}
            partialFulfilments={partialFulfilments}
            costlyRows={costlyRows}
            bulkGroups={bulkGroups}
            fulfilmentPendingRows={fulfilmentPendingRows}
            epostRows={epostRows}
            trackingOrders={trackingOrders}
            stores={stores}
            storeId={storeId}
            addresses={addresses}
            accounts={accounts}
            orderingEngine={orderingEngine}
            onNavigate={setPage}
            onRefresh={() => refresh()}
          />
        )}

        {page === "orders" && (
          <>
            <section className="grid gap-3 lg:grid-cols-[1.1fr_1fr]">
              <Card>
                <CardHeader>
                  <CardTitle>Order Controls</CardTitle>
                </CardHeader>
                <CardContent className="grid gap-4">
                    <div className="grid gap-4">
                    <div className="grid gap-3 xl:grid-cols-[minmax(260px,1fr)_auto] xl:items-end">
                      <SelectField label="Ordering engine" value={orderingEngine} onChange={setOrderingEngine}>
                        <option value="rest">REST Ordering API</option>
                        <option value="cxml">cXML Punchout / PO Request</option>
                        <option value="chrome">Chrome Extension (later)</option>
                      </SelectField>
                      <Button
                        className="xl:mb-0"
                        variant="outline"
                        onClick={async () => {
                          try {
                            setBusy("Save Ordering Engine")
                            const selectedEngine = orderingEngine
                            const result = await api<{ ok: boolean; message: string; default_ordering_engine: string }>("/api/settings/ordering-engine", {
                              method: "POST",
                              body: JSON.stringify({ ordering_engine: selectedEngine }),
                            })
                            setOrderingEngine(result.default_ordering_engine || selectedEngine)
                            setData((current) => current ? { ...current, default_ordering_engine: result.default_ordering_engine || selectedEngine } : current)
                            setModal({ ok: result.ok, title: "Save Ordering Engine", message: result.message })
                          } catch (error) {
                            setModal({ ok: false, title: "Save Ordering Engine", message: String(error) })
                          } finally {
                            setBusy("")
                          }
                        }}
                      >
                        Save Engine
                      </Button>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-[1fr_0.5fr_0.5fr_auto] lg:items-end">
                    <MultiStoreDropdown stores={stores} selected={pullStoreIds} onChange={setPullStoreIds} />
                    <div className="grid gap-1.5">
                      <Label>Days</Label>
                      <Input type="number" min="1" max="365" value={days} onChange={(event) => setDays(event.target.value)} />
                    </div>
                    <div className="grid gap-1.5">
                      <Label>Limit</Label>
                      <Input type="number" min="0" max="50000" value={limit} onChange={(event) => setLimit(event.target.value)} />
                      <p className="text-xs text-muted-foreground">0 pulls every matching order in the selected day window.</p>
                    </div>
                    <Button variant="outline" onClick={savePullDefaults} disabled={Boolean(busy)}>
                      Save Pull Defaults
                    </Button>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-[minmax(280px,1fr)_auto] lg:items-end">
                      <TextField
                        label="Pull specific Odoo orders"
                        value={pullOrderNames}
                        onChange={setPullOrderNames}
                        placeholder="NC10216, NC10212 or one per line"
                      />
                      <Button variant="outline" onClick={runPullOrderNames} disabled={!storeId || Boolean(busy)}>
                        <RefreshCw className="size-4" />
                        Pull These Orders
                      </Button>
                    </div>
                    <SelectField className="max-w-md" label="Store filter" value={storeId} onChange={updateStoreView}>
                      <option value="">All stores</option>
                      {stores.map((store) => (
                        <option key={store.id} value={store.id}>
                          {store.name}
                        </option>
                      ))}
                    </SelectField>
                    <div className="grid gap-3 lg:grid-cols-2">
                    <SelectField className="min-w-[260px]" label="Ship to" value={addressId} onChange={setAddressId}>
                      {addresses.map((address) => (
                        <option key={address.id} value={address.id}>
                          {address.label}
                        </option>
                      ))}
                    </SelectField>
                    <SelectField className="min-w-[280px]" label="Amazon account" value={amazonAccountId} onChange={setAmazonAccountId}>
                      {accounts.map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.name} {account.api_base_url.includes("sandbox.") ? "(Sandbox)" : "(Production)"}
                        </option>
                      ))}
                    </SelectField>
                    </div>
                  </div>
                  {selectedAmazonAccount && (
                    <div className="alert">
                      Selected Amazon account:{" "}
                      <span className="font-medium text-foreground">{selectedAmazonAccount.name}</span>{" "}
                      <Badge variant="secondary">{engineLabel}</Badge>{" "}
                      <Badge variant={selectedAmazonAccount.api_base_url.includes("sandbox.") ? "outline" : "secondary"}>
                        {selectedAmazonAccount.api_base_url.includes("sandbox.") ? "Sandbox" : "Production"}
                      </Badge>{" "}
                      <span className="font-mono text-xs">{selectedAmazonAccount.api_base_url}</span>
                    </div>
                  )}
                  <div className="btn-list">
                    <Button
                      disabled={!pullStoreIds.length || Boolean(busy)}
                      onClick={runPullOrders}
                    >
                      <RefreshCw className="size-4" />
                      Pull Orders
                    </Button>
                    <Button variant="outline" onClick={() => setPage("pull-jobs")}>
                      Running Jobs
                    </Button>
                    <Button
                      disabled={!storeId || !addressId || !amazonAccountId || Boolean(busy)}
                      onClick={() =>
                        runAction("Place All Orders", () =>
                          api<DashboardData>("/api/place", {
                            method: "POST",
                            body: JSON.stringify({
                              store_id: Number(storeId),
                              address_id: Number(addressId),
                              amazon_account_id: Number(amazonAccountId),
                              ordering_engine: orderingEngine,
                              allow_missing_spaid: allowMissingSpaid,
                              include_missing_asins: resendMissingAsins,
                            }),
                          }),
                        )
                      }
                    >
                      <ShoppingCart className="size-4" />
                      Place Orders
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!storeId || !addressId || !amazonAccountId || Boolean(busy)}
                      onClick={placeRecentChromeOrders}
                    >
                      <ShoppingCart className="size-4" />
                      {busy === "Place Recent Orders" ? "Pulling and queueing..." : "Place Recent"}
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!storeId || Boolean(busy)}
                      onClick={() =>
                        runAction("Check Delivered and Dispatch", () =>
                          api<DashboardData>("/api/delivery-check", {
                            method: "POST",
                            body: JSON.stringify({ store_id: Number(storeId) }),
                          }),
                        )
                      }
                    >
                      <PackageCheck className="size-4" />
                      Check Delivered
                    </Button>
                    <Button variant="outline" disabled={!storeId} onClick={() => window.open(`/reports/latest?store_id=${storeId}`, "_blank")}>
                      <Download className="size-4" />
                      Download CSV
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
                    <div>
                      <CardTitle>Duplicate ASINs Across Recent Orders</CardTitle>
                      <CardDescription>Filtered by Odoo order date. Showing the last {activeDuplicateAsinDays.toLocaleString()} day{activeDuplicateAsinDays === 1 ? "" : "s"}.</CardDescription>
                    </div>
                    <div className="w-full md:w-40">
                      <TextField
                        label="Days"
                        type="number"
                        value={duplicateAsinDays}
                        onChange={(value) => {
                          setDuplicateAsinDays(value)
                          setDuplicateAsinPage(1)
                          setSelected([])
                          setOrdersSelectAll(false)
                        }}
                      />
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {duplicateAsins.length ? (
                    <div className="list-group">
                      {duplicateAsins.map((group) => (
                        <div
                          key={group.asin}
                          role="button"
                          tabIndex={0}
                          onClick={() => {
                            updateOrdersSearch(group.asin)
                            setSelected(rows.filter((row) => row.asin === group.asin && !row.amazon_order_id).map((row) => row.id))
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault()
                              updateOrdersSearch(group.asin)
                              setSelected(rows.filter((row) => row.asin === group.asin && !row.amazon_order_id).map((row) => row.id))
                            }
                          }}
                          className="list-group-item cursor-pointer"
                        >
                          <a
                            className="font-mono font-medium text-primary underline-offset-4 hover:underline"
                            href={group.asin_url || `https://www.amazon.com/dp/${encodeURIComponent(group.asin)}`}
                            target="_blank"
                            rel="noreferrer"
                            onClick={(event) => event.stopPropagation()}
                          >
                            {group.asin}
                          </a>
                          <span className="text-muted-foreground">
                            {group.order_count} orders, qty {group.total_quantity}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No duplicate ASIN groups waiting to order.</p>
                  )}
                </CardContent>
                <CardFooter>
                  <PaginationControls
                    page={duplicateAsinPage}
                    total={duplicateAsinTotal}
                    perPage={DUPLICATE_ASIN_PAGE_SIZE}
                    onPage={setDuplicateAsinPage}
                    disabled={Boolean(busy)}
                  />
                </CardFooter>
              </Card>
            </section>

            <Card>
              <CardHeader className="gap-4">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                  <CardTitle>Odoo Order Lines</CardTitle>
                  <div className="btn-list">
                    <SelectField className="w-[190px]" label="Sort by" value={sortKey} onChange={(value) => updateSortKey(value as SortKey)}>
                      <option value="odoo_order_date">Order date</option>
                      <option value="pulled_at">Pulled date</option>
                      <option value="ordered_at">Placed date</option>
                      <option value="odoo_order_name">Order number</option>
                      <option value="product_name">Product</option>
                      <option value="default_code">Reference</option>
                      <option value="asin">ASIN</option>
                      <option value="supplier_part_auxiliary_id">SPAID</option>
                      <option value="quantity">Quantity</option>
                      <option value="state">State</option>
                      <option value="amazon_order_id">Amazon order</option>
                    </SelectField>
                    <div className="grid gap-1.5">
                      <Label>Direction</Label>
                      <Button variant="outline" onClick={() => updateSortDirection(sortDirection === "asc" ? "desc" : "asc")}>
                        {sortDirection === "asc" ? "Ascending" : "Descending"}
                      </Button>
                    </div>
                    <SelectField className="w-[210px]" label="Filter" value={orderCondition} onChange={updateOrderCondition}>
                      {orderConditionOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </SelectField>
                    <SearchBox className="w-full sm:w-[360px]" value={search} onChange={updateOrdersSearch} placeholder="Search order, product, ASIN, status..." />
                    <Button variant="outline" onClick={() => setShowColumnSettings((current) => !current)}>
                      <Columns3 className="size-4" />
                      Columns
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!storeId || Boolean(busy) || shopifyStatusForceSync?.status === "running"}
                      onClick={forceSyncShopifyStatuses}
                    >
                      <RefreshCw className="size-4" />
                      Force Sync Shopify
                    </Button>
	                    <Button
	                      variant="outline"
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={placeSelectedOrders}
	                    >
                      Place Selected
                    </Button>
	                    <Button
	                      variant="outline"
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={() => setManualFulfilmentOpen(true)}
	                    >
                      <CheckCircle2 className="size-4" />
                      Manually Fulfilled
                    </Button>
	                    <Button
	                      variant="outline"
	                      disabled={!canRunSelectedStoreAction || selected.length !== 1 || Boolean(busy)}
	                      onClick={() => {
                        const line = rows.find((row) => row.id === selected[0])
                        if (line) setEditingReplacement(line)
                      }}
                    >
                      Replace ASIN
                    </Button>
	                    <Button
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={clubPlaceSelectedOrders}
	                    >
                      Club Place
                    </Button>
	                    <Button
	                      variant="outline"
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={ignoreSelectedOrders}
	                    >
                      <AlertCircle className="size-4" />
                      Do Not Process
                    </Button>
	                    <Button
	                      variant="outline"
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={resetSelectedOrders}
	                    >
                      <RefreshCw className="size-4" />
                      Reset Selected
                    </Button>
	                    <Button
	                      variant="destructive"
	                      disabled={!canRunSelectedStoreAction || Boolean(busy)}
	                      onClick={deleteSelectedOrders}
	                    >
                      <Trash2 className="size-4" />
                      Delete
                    </Button>
                  </div>
                </div>
                {shopifyStatusForceSync?.status === "running" && (
                  <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span>{shopifyStatusForceSync.message || "Syncing Shopify order statuses..."}</span>
                      <span className="font-mono">
                        {Number(shopifyStatusForceSync.processed || 0).toLocaleString()} / {Number(shopifyStatusForceSync.total || 0).toLocaleString()}
                      </span>
                    </div>
                    <div className="mt-2 h-2 overflow-hidden rounded bg-blue-100">
                      <div
                        className="h-full bg-blue-600 transition-all"
                        style={{ width: `${Math.min(100, Math.round((Number(shopifyStatusForceSync.processed || 0) / Math.max(1, Number(shopifyStatusForceSync.total || 0))) * 100))}%` }}
                      />
                    </div>
                  </div>
                )}
                <PaginationControls page={ordersPage} total={ordersTotal} perPage={ORDERS_PAGE_SIZE} onPage={setOrdersPage} disabled={Boolean(busy)} label="rows" />
                {selectedClubName && <p className="text-sm text-muted-foreground">Clubbed recipient: {selectedClubName}</p>}
                {orderingEngine === "cxml" && (
                  <label className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Checkbox checked={allowMissingSpaid} onCheckedChange={(checked) => setAllowMissingSpaid(Boolean(checked))} />
                    Allow cXML submit without SPAID
                  </label>
                )}
                <label className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Checkbox checked={resendMissingAsins} onCheckedChange={(checked) => setResendMissingAsins(Boolean(checked))} />
                  <span className="ml-2">Resend missing/out-of-stock ASINs</span>
                </label>
                <ExportControls
                  view="orders"
                  storeId={storeId}
                  columns={orderExportColumns}
                  selectedIds={selected}
                  selectAll={ordersSelectAll}
                  total={ordersTotal}
                  filters={{ q: search.trim(), condition: orderCondition }}
                  onSelectAll={() => setOrdersSelectAll(true)}
                  onClear={() => { setOrdersSelectAll(false); setSelected([]) }}
                  onResult={setModal}
                  onDownloads={() => setPage("downloads")}
                  extraAction={
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!selected.length || Boolean(busy)}
                      onClick={resetSelectedOrders}
                    >
                      <RefreshCw className="size-4" />
                      Reset Fulfilment
                    </Button>
                  }
                />
                {showColumnSettings && (
                  <div className="form-fieldset">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <p className="text-sm font-medium">Visible columns</p>
                      <Button variant="outline" size="sm" onClick={() => setOrderColumns(defaultOrderColumns.map((column) => ({ ...column, visible: true })))}>
                        Reset
                      </Button>
                    </div>
                    <div className="btn-list">
                      {orderColumns.map((column) => (
                        <div
                          key={column.key}
                          draggable
                          onDragStart={() => {
                            setDraggedColumnKey(column.key)
                            setColumnDropTarget({ key: column.key, after: false })
                          }}
                          onDragEnd={() => {
                            setDraggedColumnKey(null)
                            setColumnDropTarget(null)
                          }}
                          onDragOver={(event) => updateColumnDropTarget(event, column.key)}
                          onDrop={(event) => {
                            event.preventDefault()
                            if (draggedColumnKey) moveColumn(draggedColumnKey, column.key, columnDropTarget?.key === column.key ? columnDropTarget.after : false)
                            setDraggedColumnKey(null)
                            setColumnDropTarget(null)
                          }}
                          className={[
                            "badge badge-outline cursor-grab border-l-4 border-r-4 border-l-transparent border-r-transparent active:cursor-grabbing",
                            draggedColumnKey === column.key ? "opacity-60" : "",
                            columnDropTarget?.key === column.key && !columnDropTarget.after ? "border-l-primary" : "",
                            columnDropTarget?.key === column.key && columnDropTarget.after ? "border-r-primary" : "",
                          ].filter(Boolean).join(" ")}
                        >
                          <GripVertical className="size-3.5 text-muted-foreground" />
                          <Checkbox
                            checked={column.visible !== false}
                            onCheckedChange={(checked) => setColumnVisibility(column.key, Boolean(checked))}
                          />
                          <span>{column.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardHeader>
              <CardContent className="p-0">
                <div className="orders-table-wrap">
                  {ordersTransitioning && (
                    <div className="orders-table-loader" role="status" aria-live="polite" aria-label="Loading orders">
                      <div className="spinner-border text-primary" role="status"></div>
                    </div>
                  )}
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">
                        <Checkbox
                          checked={filteredRows.length > 0 && filteredRows.every((row) => selected.includes(row.id))}
                          onCheckedChange={(checked) => {
                            setOrdersSelectAll(false)
                            const pageIds = filteredRows.map((row) => row.id)
                            filteredRows.forEach((row) => {
                              if (checked) selectedOrderRowsRef.current.set(row.id, row)
                              else selectedOrderRowsRef.current.delete(row.id)
                            })
                            setSelected((current) => checked ? Array.from(new Set([...current, ...pageIds])) : current.filter((id) => !pageIds.includes(id)))
                          }}
                        />
                      </TableHead>
                      {visibleOrderColumns.map((column) => (
                        <TableHead key={column.key} className={column.width}>
                          {column.sortable ? (
                            <button className="flex items-center gap-1 hover:text-foreground" type="button" onClick={() => applySort(column.sortable!)}>
                              {column.label}
                              {sortKey === column.sortable ? <span className="text-xs text-muted-foreground">{sortDirection === "asc" ? "up" : "down"}</span> : null}
                            </button>
                          ) : (
                            column.label
                          )}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {!ordersTransitioning && sortedRows.length ? sortedRows.map((row) => (
                      <TableRow
                        key={row.id}
                        onClick={(event) => {
                          if (isSelectionControlClick(event)) return
                          selectOrderRow(row, event.shiftKey)
                        }}
                        className={
                          [
                            "cursor-pointer",
                            selected.includes(row.id) ? "outline outline-1 -outline-offset-1 outline-primary/35" : "",
                            row.state === "ignored"
                              ? "bg-slate-50 opacity-75 [&_td:not(:first-child)]:line-through [&_td:not(:first-child)]:text-muted-foreground"
                              : "",
                            isLimitPurchaseLine(row)
                            ? "bg-[#f5f0ff]"
                            : isCurrentAmazonCancelledLine(row)
                            ? "bg-red-50"
                            : isMissingOrderLine(row)
                            ? "order-row-missing"
                            : isAmazonDeliveredLine(row)
                            ? ""
                            : ["cancelled", "refunded"].includes(row.odoo_status_label)
                            ? "bg-destructive/5"
                            : Number(row.odoo_order_distinct_asin_count || 0) > 1
                              ? "bg-parrot-green-lt"
                              : "",
                          ].filter(Boolean).join(" ")
                        }
                      >
                        <TableCell>
                          <button
                            type="button"
                            role="checkbox"
                            aria-checked={selected.includes(row.id)}
                            aria-label={`Select order ${row.odoo_order_name}`}
                            data-checked={selected.includes(row.id) ? "true" : undefined}
                            className={cn(
                              "flex size-4 items-center justify-center rounded border border-input bg-background text-primary-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                              selected.includes(row.id) ? "border-primary bg-primary" : "hover:border-primary/60",
                            )}
                            onClick={(event) => selectOrderCheckbox(row, event)}
                            onKeyDown={(event) => selectOrderCheckboxFromKeyboard(row, event)}
                          >
                            {selected.includes(row.id) ? <Check className="size-3" strokeWidth={3} /> : null}
                          </button>
                        </TableCell>
                        {visibleOrderColumns.map((column) => (
                          <TableCell key={column.key} className="overflow-hidden">
                            {renderOrderCell(row, column)}
                          </TableCell>
                        ))}
                      </TableRow>
                    )) : ordersLoading || ordersTransitioning ? (
                      <TableRow>
                        <TableCell colSpan={visibleOrderColumns.length + 1} className="h-32 text-center text-muted-foreground">
                          <span className="inline-flex items-center gap-2">
                            <span className="spinner-border spinner-border-sm text-primary" role="status"></span>
                            Loading orders...
                          </span>
                        </TableCell>
                      </TableRow>
                    ) : (
                      <TableRow>
                        <TableCell colSpan={visibleOrderColumns.length + 1} className="h-32 text-center text-muted-foreground">
                          No orders match this view.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
                </div>
              </CardContent>
            </Card>
            {selected.length ? (
              <>
                <div className="h-28" />
                <div className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/95 px-4 py-3 shadow-[0_-10px_30px_rgba(24,36,51,0.12)] backdrop-blur supports-[backdrop-filter]:bg-background/85">
                  <div className="mx-auto flex max-w-6xl flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="flex items-center gap-3">
                      <Badge variant="secondary" className="px-3 py-1 text-sm">{selected.length.toLocaleString()} selected</Badge>
                      {selectedClubName ? <span className="hidden max-w-xl truncate text-sm text-muted-foreground lg:block">{selectedClubName}</span> : null}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
	                      <Button variant="outline" disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={placeSelectedOrders}>
                        Place Selected
                      </Button>
	                      <Button variant="outline" disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={() => setManualFulfilmentOpen(true)}>
                        <CheckCircle2 className="size-4" />
                        Manually Fulfilled
                      </Button>
	                      <Button disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={clubPlaceSelectedOrders}>
                        Club Place
                      </Button>
	                      <Button variant="outline" disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={ignoreSelectedOrders}>
                        <AlertCircle className="size-4" />
                        Do Not Process
                      </Button>
	                      <Button variant="outline" disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={resetSelectedOrders}>
                        <RefreshCw className="size-4" />
                        Reset
                      </Button>
                      <Button variant="ghost" disabled={Boolean(busy)} onClick={() => { setOrdersSelectAll(false); setSelected([]) }}>
                        Clear
                      </Button>
	                      <Button variant="destructive" disabled={!canRunSelectedStoreAction || Boolean(busy)} onClick={deleteSelectedOrders}>
                        <Trash2 className="size-4" />
                        Delete
                      </Button>
                    </div>
                  </div>
                </div>
              </>
            ) : null}
            {editingSpaid && (
              <SpaidDialog
                line={editingSpaid}
                storeId={Number(storeId)}
                onClose={() => setEditingSpaid(null)}
                onSaved={(next) => {
                  setData(next)
                  setEditingSpaid(null)
                  setModal({ ok: Boolean(next.ok), title: "SupplierPartAuxiliaryID", message: next.message || "Saved." })
                }}
                onResult={setModal}
              />
            )}
          </>
        )}

        {page === "settings" && (
          <SettingsPage
            stores={stores}
            addresses={addresses}
            accounts={accounts}
            urls={punchoutReturnUrls}
            onChanged={refresh}
            onResult={setModal}
          />
        )}
        {page === "tracking" && (
          <TrackingPage
            storeId={storeId}
            page={trackingPage}
            total={trackingTotal}
            onPage={setTrackingPage}
            selected={trackingSelected}
            selectAll={trackingSelectAll}
            onSelected={setTrackingSelected}
            onSelectAll={setTrackingSelectAll}
            onNavigate={setPage}
            onResult={setModal}
          />
        )}
        {page === "amazon-otp" && (
          <AmazonOtpPage onResult={setModal} />
        )}
        {page === "payment-failed" && (
          <PaymentFailedPage
            rows={paymentFailures}
            storeId={storeId}
            page={paymentFailuresPage}
            total={paymentFailuresTotal}
            onPage={setPaymentFailuresPage}
            onResult={setModal}
            onRefresh={async () => {
              const result = await api<{ rows: PaymentFailure[]; total: number }>(`/api/tracking/payment-failures${pagedQuery(storeId, paymentFailuresPage)}`)
              setPaymentFailures(result.rows || [])
              setPaymentFailuresTotal(result.total || 0)
            }}
          />
        )}
        {page === "shopify-fulfilment" && (
          <ShopifyFulfilmentPage storeId={storeId} onResult={setModal} />
        )}
        {page === "chrome-queue" && (
          <ChromeQueuePage
            rows={chromeQueueJobs}
            counts={chromeQueueCounts}
            storeId={storeId}
            onResult={setModal}
            onRefresh={() => refreshChromeQueue()}
          />
        )}
        {page === "shopify-tracking" && (
          <ShopifyTrackingSyncPage onResult={setModal} />
        )}
        {page === "fulfilment-pending" && (
          <FulfilmentPendingPage
            rows={fulfilmentPendingRows}
            storeId={storeId}
            page={fulfilmentPendingPage}
            total={fulfilmentPendingTotal}
            onPage={setFulfilmentPendingPage}
            selected={pendingSelected}
            selectAll={pendingSelectAll}
            onSelected={setPendingSelected}
            onSelectAll={setPendingSelectAll}
            onNavigate={setPage}
            onResult={setModal}
            onRefresh={async () => {
              const result = await api<{ rows: FulfilmentPendingRow[]; total: number }>(`/api/tracking/fulfilment-pending${pagedQuery(storeId, fulfilmentPendingPage)}`)
              setFulfilmentPendingRows(result.rows)
              setFulfilmentPendingTotal(result.total || 0)
            }}
          />
        )}
        {page === "epost" && (
          <EpostTrackingPage
            rows={epostRows}
            storeId={storeId}
            page={epostPage}
            total={epostTotal}
            statusFilter={epostStatusFilter}
            onPage={setEpostPage}
            onStatusFilter={(value) => { setEpostStatusFilter(value); setEpostPage(1) }}
            selected={epostSelected}
            selectAll={epostSelectAll}
            onSelected={setEpostSelected}
            onSelectAll={setEpostSelectAll}
            onResult={setModal}
            onNavigate={setPage}
            onRows={(rows, total) => {
              setEpostRows(rows)
              setEpostTotal(total)
            }}
          />
        )}
        {page === "duplicate-tracking" && (
          <DuplicateTrackingPage storeId={storeId} onResult={setModal} onNavigate={setPage} />
        )}
        {page === "inventory" && (
          <InventoryPage
            rows={inventory}
            storeId={storeId}
            page={inventoryPage}
            total={inventoryTotal}
            onPage={setInventoryPage}
            onRows={(items, total) => {
              setInventory(items)
              setInventoryTotal(total)
            }}
            onResult={setModal}
          />
        )}
        {page === "cancelled-orders" && (
          <CancelledOrdersPage
            rows={cancelledOrders}
            storeId={storeId}
            page={cancelledOrdersPage}
            total={cancelledOrdersTotal}
            onPage={setCancelledOrdersPage}
            onRows={(rows, total) => {
              setCancelledOrders(rows)
              setCancelledOrdersTotal(total)
            }}
            onResult={setModal}
          />
        )}
        {page === "missing" && (
          <MissingPage
            rows={missingRows}
            storeId={Number(storeId)}
            page={missingPage}
            total={missingTotal}
            onPage={setMissingPage}
            selected={missingSelected}
            selectAll={missingSelectAll}
            onSelected={setMissingSelected}
            onSelectAll={setMissingSelectAll}
            onNavigate={setPage}
            onResult={setModal}
            onAssign={setEditingReplacement}
            onRefresh={async () => {
              const result = await api<{ rows: OrderLine[]; total: number }>(`/api/missing${pagedQuery(storeId, missingPage)}`)
              setMissingRows(result.rows)
              setMissingTotal(result.total || 0)
              await refresh()
            }}
          />
        )}
        {page === "back-in-stock" && (
          <BackInStockPage
            rows={backInStockRows}
            page={backInStockPage}
            total={backInStockTotal}
            onPage={setBackInStockPage}
            onResult={setModal}
            onRefresh={async () => {
              const result = await api<{ rows: BackInStockRow[]; total: number }>(`/api/back-in-stock${pagedQuery(storeId, backInStockPage)}`)
              setBackInStockRows(result.rows || [])
              setBackInStockTotal(result.total || 0)
            }}
          />
        )}
        {page === "partial-fulfilments" && (
          <PartialFulfilmentsPage
            rows={partialFulfilments}
            page={partialFulfilmentsPage}
            total={partialFulfilmentsTotal}
            onPage={setPartialFulfilmentsPage}
            onResult={setModal}
            onRefresh={async () => {
              const result = await api<{ rows: PartialFulfilment[]; total: number }>(`/api/partial-fulfilments${pagedQuery(storeId, partialFulfilmentsPage)}`)
              setPartialFulfilments(result.rows || [])
              setPartialFulfilmentsTotal(result.total || 0)
              await refresh()
            }}
          />
        )}
        {page === "bulk" && (
          <BulkPage
            groups={bulkGroups}
            storeId={Number(storeId)}
            addressId={Number(addressId)}
            amazonAccountId={Number(amazonAccountId)}
            orderingEngine={orderingEngine}
            page={bulkPage}
            total={bulkTotal}
            days={bulkDays}
            activeDays={activeBulkDays}
            onDays={setBulkDays}
            onPage={setBulkPage}
            selected={bulkSelected}
            selectAll={bulkSelectAll}
            onSelected={setBulkSelected}
            onSelectAll={setBulkSelectAll}
            onResult={setModal}
            onNavigate={setPage}
            onRefresh={async () => {
              const requestedDays = bulkDays || "2"
              const result = await api<{ groups: BulkGroup[]; total: number; days: number }>(`/api/bulk${pagedQuery(storeId, bulkPage, { days: requestedDays })}`)
              setBulkGroups(result.groups)
              setBulkTotal(result.total || 0)
              setActiveBulkDays(result.days || Number(requestedDays) || 2)
              await refresh()
            }}
          />
        )}
        {page === "costly" && (
          <CostlyPage
            rows={costlyRows}
            storeId={Number(storeId)}
            page={costlyPage}
            total={costlyTotal}
            onPage={setCostlyPage}
            selected={costlySelected}
            selectAll={costlySelectAll}
            onSelected={setCostlySelected}
            onSelectAll={setCostlySelectAll}
            onResult={setModal}
            onNavigate={setPage}
            onRefresh={async () => {
              const result = await api<{ rows: OrderLine[]; total: number }>(`/api/costly${pagedQuery(storeId, costlyPage)}`)
              setCostlyRows(result.rows)
              setCostlyTotal(result.total || 0)
              await refresh()
            }}
          />
        )}
        {page === "profit-loss" && (
          <ProfitLossPage stores={stores} storeId={storeId} period={profitLossPeriod} onPeriod={setProfitLossPeriod} onResult={setModal} />
        )}
        {page === "accounting" && (
          <AccountingPage storeId={storeId} onResult={setModal} />
        )}
        {page === "pull-jobs" && (
          <PullJobsPage onResult={setModal} />
        )}
        {page === "downloads" && (
          <DownloadsPage onResult={setModal} />
        )}
        {editingReplacement && (
          <ReplacementDialog
            line={editingReplacement}
            storeId={Number(storeId)}
            onClose={() => setEditingReplacement(null)}
            onSaved={async (message) => {
              setEditingReplacement(null)
              const result = await api<{ rows: OrderLine[]; total: number }>(`/api/missing${pagedQuery(storeId, missingPage)}`)
              setMissingRows(result.rows)
              setMissingTotal(result.total || 0)
	              await refresh()
	              setModal({ ok: true, title: message.toLowerCase().includes("reset") ? "Replacement Reset" : "Replacement Assigned", message })
	            }}
            onResult={setModal}
          />
        )}
      </div>
      </div>
      </main>
    </div>
    </TooltipProvider>
  )
}

function HomeDashboard({
  data,
  missingRows,
  partialFulfilments,
  costlyRows,
  bulkGroups,
  fulfilmentPendingRows,
  epostRows,
  trackingOrders,
  stores,
  storeId,
  addresses,
  accounts,
  orderingEngine,
  onNavigate,
  onRefresh,
}: {
  data: DashboardData
  missingRows: OrderLine[]
  partialFulfilments: PartialFulfilment[]
  costlyRows: OrderLine[]
  bulkGroups: BulkGroup[]
  fulfilmentPendingRows: FulfilmentPendingRow[]
  epostRows: EpostTrackingRow[]
  trackingOrders: TrackingOrder[]
  stores: Store[]
  storeId: string
  addresses: Address[]
  accounts: AmazonAccount[]
  orderingEngine: string
  onNavigate: (page: string) => void
  onRefresh: () => Promise<void>
}) {
  const stateCount = (state: string) => Number(data.counts.find((item) => item.state === state)?.count || 0)
  const odooReady = ["pulled", "error", "missing", "costly"].reduce((total, state) => total + stateCount(state), 0)
  const orderedCount = stateCount("ordered") + stateCount("dispatched")
  const deliveredCount = stateCount("delivered")
  const pendingTracking = trackingOrders.filter((order) => (order.tracking_status || "").toLowerCase() !== "delivered").length
  const epostLost = epostRows.filter((row) => row.epost_status === "lost").length
  const epostPending = epostRows.filter((row) => row.epost_status !== "delivered" && row.epost_status !== "lost").length
  const selectedStore = stores.find((store) => String(store.id) === storeId)
  const defaultAccount = accounts.find((account) => account.is_default) || accounts[0]
  const defaultAddress = addresses.find((address) => address.is_default) || addresses[0]
  const duplicateCount = data.duplicate_asins.length
  const missingCount = stateCount("missing") || missingRows.length
  const partialCount = partialFulfilments.length
  const costlyCount = stateCount("costly") || costlyRows.length
  const bulkCount = bulkGroups.length || duplicateCount
  const reviewCount = missingCount + partialCount + costlyCount + fulfilmentPendingRows.length
  const completionRate = data.total ? Math.round((deliveredCount / data.total) * 100) : 0
  const epostDelivered = epostRows.filter((row) => row.epost_status === "delivered").length
  const epostRate = epostRows.length ? Math.round((epostDelivered / epostRows.length) * 100) : 0
  const orderSpark = data.counts.map((item, index) => ({
    label: item.state,
    value: Number(item.count || 0),
    height: Math.max(12, Math.min(96, Number(item.count || 0) * 8 + 18 + index * 3)),
  }))
  const trackingBars = [
    ["Amazon pending", pendingTracking, "bg-blue"],
    ["ePost pending", epostPending, "bg-yellow"],
    ["ePost lost", epostLost, "bg-red"],
    ["Odoo pending", fulfilmentPendingRows.length, "bg-green"],
  ] as const

  const panels = [
    {
      title: "Orders",
      page: "orders",
      primary: data.total,
      primaryLabel: "recent lines",
      details: [`${odooReady} ready/review`, `${orderedCount} ordered`, `${deliveredCount} delivered`],
      tone: odooReady ? "bg-primary/5" : "",
    },
    {
      title: "Missing",
      page: "missing",
      primary: missingCount,
      primaryLabel: "need replacement",
      details: missingRows.slice(0, 2).map((row) => `${row.odoo_order_name} ${row.missing_asin || row.asin}`),
      tone: missingCount ? "bg-destructive/10" : "",
    },
    {
      title: "Partial",
      page: "partial-fulfilments",
      primary: partialCount,
      primaryLabel: "split orders",
      details: partialFulfilments.slice(0, 2).map((row) => `${row.odoo_order_name} ${row.missing_asins.join(", ")}`),
      tone: partialCount ? "bg-destructive/10" : "",
    },
    {
      title: "Costly",
      page: "costly",
      primary: costlyCount,
      primaryLabel: "need approval",
      details: costlyRows.slice(0, 2).map((row) => `${row.odoo_order_name} ${row.asin}`),
      tone: costlyCount ? "bg-destructive/10" : "",
    },
    {
      title: "Bulk",
      page: "bulk",
      primary: bulkCount,
      primaryLabel: "opportunities",
      details: [`${duplicateCount} duplicate ASIN groups`, ...bulkGroups.slice(0, 1).map((group) => `${group.asin} qty ${group.quantity}`)],
      tone: bulkCount ? "bg-secondary/50" : "",
    },
    {
      title: "Amazon Tracking",
      page: "tracking",
      primary: trackingOrders.length,
      primaryLabel: "Amazon orders",
      details: [`${pendingTracking} still moving`, `${fulfilmentPendingRows.length} delivered but Odoo pending`],
      tone: fulfilmentPendingRows.length ? "bg-destructive/10" : "",
    },
    {
      title: "ePost",
      page: "epost",
      primary: epostRows.length,
      primaryLabel: "EPG codes",
      details: [`${epostPending} pending`, `${epostLost} lost`, `${epostRows.filter((row) => row.epost_status === "delivered").length} delivered`],
      tone: epostLost ? "bg-destructive/10" : "",
    },
  ]

  return (
    <div className="grid gap-5">
      <section className="grid gap-3 xl:grid-cols-[1.15fr_0.85fr_0.85fr]">
        <Card>
          <CardContent className="grid min-h-56 gap-4 p-6 md:grid-cols-[1fr_220px] md:items-center">
            <div>
              <div className="page-pretitle">Overview</div>
              <h2 className="mt-1 text-2xl font-semibold">Welcome back, operations team</h2>
              <p className="mt-2 max-w-xl text-muted-foreground">
                {selectedStore?.name || "Selected store"} has {reviewCount} review item(s), {orderedCount} ordered line(s), and {epostPending} ePost shipment(s) still moving.
              </p>
              <div className="mt-8 grid gap-4 sm:grid-cols-3">
                <div>
                  <div className="text-xs font-bold uppercase text-muted-foreground">Ready / Review</div>
                  <div className="mt-1 text-2xl font-semibold">{odooReady}</div>
                  <div className="progress mt-2"><div className="progress-bar bg-primary" style={{ width: `${Math.min(100, odooReady * 4)}%` }} /></div>
                </div>
                <div>
                  <div className="text-xs font-bold uppercase text-muted-foreground">Completion</div>
                  <div className="mt-1 text-2xl font-semibold">{completionRate}%</div>
                  <div className="progress mt-2"><div className="progress-bar bg-green" style={{ width: `${completionRate}%` }} /></div>
                </div>
                <div>
                  <div className="text-xs font-bold uppercase text-muted-foreground">ePost Delivery</div>
                  <div className="mt-1 text-2xl font-semibold">{epostRate}%</div>
                  <div className="progress mt-2"><div className="progress-bar bg-blue" style={{ width: `${epostRate}%` }} /></div>
                </div>
              </div>
            </div>
            <div className="hidden h-40 items-end gap-2 md:flex">
              {(orderSpark.length ? orderSpark : [{ label: "empty", value: 1, height: 20 }]).map((bar) => (
                <div key={bar.label} className="flex flex-1 flex-col items-center gap-2">
                  <div className="w-full rounded-t bg-primary/80" style={{ height: `${bar.height}px` }} />
                  <span className="max-w-16 truncate text-[10px] text-muted-foreground">{bar.label}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="text-xs font-bold uppercase text-muted-foreground">Tracking Performance</div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-3xl font-semibold">{epostRate}%</span>
              <span className={epostLost ? "text-red" : "text-green"}>{epostLost ? `${epostLost} lost` : "healthy"}</span>
            </div>
            <div className="mt-6 grid gap-3">
              {trackingBars.map(([label, value, color]) => (
                <div key={label}>
                  <div className="mb-1 flex justify-between text-sm"><span>{label}</span><span>{value}</span></div>
                  <div className="progress"><div className={`progress-bar ${color}`} style={{ width: `${Math.min(100, Number(value || 0) * 8)}%` }} /></div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-6">
            <div className="text-xs font-bold uppercase text-muted-foreground">Attention Queue</div>
            <div className="mt-2 text-3xl font-semibold">{reviewCount}</div>
            <div className="list-group mt-5 text-sm">
              <button className="list-group-item" onClick={() => onNavigate("missing")}><span>Missing ASINs</span><b>{missingCount}</b></button>
              <button className="list-group-item" onClick={() => onNavigate("partial-fulfilments")}><span>Partial Fulfilments</span><b>{partialCount}</b></button>
              <button className="list-group-item" onClick={() => onNavigate("costly")}><span>Cost Review</span><b>{costlyCount}</b></button>
              <button className="list-group-item" onClick={() => onNavigate("fulfilment-pending")}><span>Odoo Pending</span><b>{fulfilmentPendingRows.length}</b></button>
            </div>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-3 lg:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <CardTitle>Operations Dashboard</CardTitle>
              <CardDescription>Summary for the selected store with review queues, tracking health, and fulfilment readiness.</CardDescription>
            </div>
            <Button variant="outline" onClick={onRefresh}>
              <RefreshCw className="size-4" />
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {panels.map((panel) => (
              <button
                key={panel.title}
                type="button"
                onClick={() => onNavigate(panel.page)}
                className={`card card-sm text-left transition ${panel.tone}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-muted-foreground">{panel.title}</div>
                    <div className="mt-2 flex items-baseline gap-2">
                      <span className="text-3xl font-semibold tracking-tight">{panel.primary}</span>
                      <span className="text-sm text-muted-foreground">{panel.primaryLabel}</span>
                    </div>
                  </div>
                  {panel.primary > 0 ? <Badge variant={panel.tone.includes("destructive") ? "destructive" : "secondary"}>open</Badge> : <Badge variant="outline">clear</Badge>}
                </div>
                <div className="grid gap-1 text-sm text-muted-foreground">
                  {panel.details.length ? panel.details.map((detail) => <span key={detail} className="truncate">{detail}</span>) : <span>No current items.</span>}
                </div>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Setup Snapshot</CardTitle>
            <CardDescription>Current routing choices used by order placement and tracking pages.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <div className="datagrid-item">
              <div className="datagrid-title">Store</div>
              <div className="datagrid-content">{selectedStore?.name || "No store selected"}</div>
            </div>
            <div className="datagrid-item">
              <div className="datagrid-title">Ordering engine</div>
              <div className="datagrid-content">{orderingEngine === "rest" ? "Amazon REST" : orderingEngine === "cxml" ? "cXML Punchout" : "Chrome Extension"}</div>
            </div>
            <div className="datagrid-item">
              <div className="datagrid-title">Ship to</div>
              <div className="datagrid-content">{defaultAddress?.label || "No address configured"}</div>
            </div>
            <div className="datagrid-item">
              <div className="datagrid-title">Amazon account</div>
              <div className="datagrid-content">{defaultAccount?.name || "No Amazon account configured"}</div>
            </div>
            <Button variant="outline" onClick={() => onNavigate("settings")}>
              <Settings className="size-4" />
              Open Settings
            </Button>
          </CardContent>
        </Card>
      </section>

      <section className="grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Next Best Actions</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <div className="list-group">
            {missingRows.length ? <button className="list-group-item" onClick={() => onNavigate("missing")}>Resolve {missingRows.length} missing ASIN order(s).</button> : null}
            {partialFulfilments.length ? <button className="list-group-item text-destructive" onClick={() => onNavigate("partial-fulfilments")}>Review {partialFulfilments.length} partial fulfilment order(s).</button> : null}
            {costlyRows.length ? <button className="list-group-item" onClick={() => onNavigate("costly")}>Approve or reject {costlyRows.length} costly order(s).</button> : null}
            {epostLost ? <button className="list-group-item text-destructive" onClick={() => onNavigate("epost")}>Review {epostLost} lost ePost shipment(s).</button> : null}
            {fulfilmentPendingRows.length ? <button className="list-group-item text-destructive" onClick={() => onNavigate("fulfilment-pending")}>Fix {fulfilmentPendingRows.length} Amazon delivered/Odoo pending order(s).</button> : null}
            </div>
            {!missingRows.length && !partialFulfilments.length && !costlyRows.length && !epostLost && !fulfilmentPendingRows.length ? <p className="text-muted-foreground">No urgent review queues right now.</p> : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent Order Mix</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <div className="list-group">
            {data.counts.map((item) => (
              <div key={item.state} className="list-group-item">
                <span>{item.state}</span>
                <Badge variant="outline">{item.count}</Badge>
              </div>
            ))}
            </div>
            {!data.counts.length && <p className="text-muted-foreground">No order lines loaded yet.</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tracking Health</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <div className="list-group">
            <div className="list-group-item">
              <span>Amazon not delivered</span>
              <Badge variant="outline">{pendingTracking}</Badge>
            </div>
            <div className="list-group-item">
              <span>ePost pending</span>
              <Badge variant="outline">{epostPending}</Badge>
            </div>
            <div className="list-group-item">
              <span>ePost lost</span>
              <Badge variant={epostLost ? "destructive" : "outline"}>{epostLost}</Badge>
            </div>
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  )
}

function StoresPage({ stores, onChanged, onResult }: { stores: Store[]; onChanged: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [editing, setEditing] = useState<Store | null>(null)
  const [creating, setCreating] = useState(false)
  return (
    <SettingsTable<Store>
      title="Odoo Stores"
      description="Add, edit, test, or remove Odoo websites."
      rows={stores}
      columns={["Name", "URL", "Database", "User", "Website ID"]}
      renderRow={(store) => [store.name, store.odoo_url, store.odoo_db, store.odoo_user, store.website_id || ""]}
      onAdd={() => setCreating(true)}
      onEdit={setEditing}
      onDelete={async (store) => {
        try {
          await api(`/api/stores/${store.id}`, { method: "DELETE" })
          await onChanged()
          onResult({ ok: true, title: "Store Deleted", message: `${store.name} was removed.` })
        } catch (error) {
          onResult({ ok: false, title: "Store Delete Failed", message: String(error) })
        }
      }}
      onTest={async (store) => onResult({ title: "Test Store", ...(await api<{ ok: boolean; message: string }>(`/api/stores/${store.id}/test`, { method: "POST" })) })}
    >
      <StoreDialog open={creating} value={emptyStore} onClose={() => setCreating(false)} onSaved={onChanged} onResult={onResult} />
      {editing && <StoreDialog open value={editing} id={editing.id} onClose={() => setEditing(null)} onSaved={onChanged} onResult={onResult} />}
    </SettingsTable>
  )
}

function TrackingPage({
  storeId,
  page,
  total,
  onPage,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onNavigate,
  onResult,
}: {
  storeId: string
  page: number
  total: number
  onPage: (page: number) => void
  selected: string[]
  selectAll: boolean
  onSelected: (ids: string[]) => void
  onSelectAll: (value: boolean) => void
  onNavigate: (page: string) => void
  onResult: (modal: ModalState) => void
}) {
  const [orders, setOrders] = useState<TrackingOrder[]>([])
  const [localTotal, setLocalTotal] = useState(total)
  const [loading, setLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState("active")
  const [queryText, setQueryText] = useState("")
  const selectionAnchor = useRef<string | null>(null)

  async function refreshTracking() {
    setLoading(true)
    try {
      const query = new URLSearchParams()
      if (storeId) query.set("store_id", storeId)
      query.set("page", String(page))
      query.set("per_page", String(PAGE_SIZE))
      query.set("status", statusFilter)
      if (queryText.trim()) query.set("q", queryText.trim())
      const result = await api<{ ok: boolean; orders: TrackingOrder[]; total: number }>(`/api/tracking/orders?${query.toString()}`)
      setOrders(result.orders || [])
      setLocalTotal(result.total || 0)
    } catch (error) {
      onResult({ ok: false, title: "Tracking Load Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshTracking()
  }, [storeId, page, statusFilter, queryText])

  useEffect(() => setLocalTotal(total), [total])

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Package Tracking</CardTitle>
          <CardDescription>Chrome tracking extension updates package status, carrier, tracking ID, and latest scan until delivered.</CardDescription>
        </div>
        <div className="flex flex-col gap-2 md:min-w-[540px] md:flex-row md:items-end">
          <SearchBox
            className="w-full md:flex-1"
            value={queryText}
            onChange={(value) => { setQueryText(value); onPage(1) }}
            placeholder="Search tracking code, Amazon order, Odoo order..."
          />
          <SelectField className="w-44" label="Filter" value={statusFilter} onChange={(value) => { setStatusFilter(value); onPage(1) }}>
            <option value="active">Active</option>
            <option value="recent">Recently checked</option>
            <option value="delivered">Delivered</option>
            <option value="cancelled">Cancelled</option>
            <option value="all">All</option>
          </SelectField>
          <Button variant="outline" onClick={refreshTracking} disabled={loading}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <div className="border-t px-6 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <ExportControls view="tracking" storeId={storeId} columns={trackingExportColumns} selectedIds={selected} selectAll={selectAll} total={localTotal} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
          <PaginationControls page={page} total={localTotal} onPage={onPage} disabled={loading} />
        </div>
      </div>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  checked={orders.length > 0 && orders.every((order) => selected.includes(order.amazon_order_id))}
                  onCheckedChange={(checked) => {
                    onSelectAll(false)
                    const ids = orders.map((order) => order.amazon_order_id)
                    onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                  }}
                />
              </TableHead>
              <TableHead>Odoo Order</TableHead>
              <TableHead>Amazon Order</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Cancelled Earlier</TableHead>
              <TableHead>Carrier / Tracking</TableHead>
              <TableHead>Latest Update</TableHead>
              <TableHead>Checked</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orders.map((order) => {
              const payloads = order.lines.flatMap((line) => {
                try {
                  return JSON.parse(line.tracking_payload || "[]")
                } catch {
                  return []
                }
              })
              const firstPackage = payloads[0] || {}
              const latest = firstPackage.latest_event || {}
              return (
                <TableRow key={order.amazon_order_id} className={order.amazon_cancelled_at ? "bg-red-50" : ""}>
                  <TableCell>
                    <Checkbox
                      checked={selected.includes(order.amazon_order_id)}
                      onCheckedChange={(checked, event) => {
                        onSelectAll(false)
                        const visibleIds = orders.map((item) => item.amazon_order_id)
                        onSelected(rangeSelection(visibleIds, selected, order.amazon_order_id, Boolean(checked), event.shiftKey, selectionAnchor.current))
                        selectionAnchor.current = order.amazon_order_id
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1">
                      {order.lines.slice(0, 4).map((line) => (
                        <OdooOrderRef key={line.id} name={line.odoo_order_name} url={line.odoo_order_url} />
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={order.amazon_order_url} target="_blank">
                      {order.amazon_order_id}
                    </a>
                  </TableCell>
                  <TableCell><StatusBadge value={order.tracking_status || firstPackage.status || "Unknown"} /></TableCell>
                  <TableCell>{order.amazon_cancelled_at ? <Badge className="bg-red-100 text-red-700 hover:bg-red-100">Yes</Badge> : <Badge variant="outline">No</Badge>}</TableCell>
                  <TableCell className="max-w-[320px]">
                    {payloads.length ? (
                      <div className="grid gap-1 text-sm">
                        {payloads.map((pkg: any, index: number) => (
                          <a key={`${pkg.tracking_id || index}`} className="truncate text-primary underline-offset-4 hover:underline" href={pkg.tracking_url} target="_blank">
                            {pkg.carrier || "Carrier"} {pkg.tracking_id || "tracking pending"}
                          </a>
                        ))}
                      </div>
                    ) : <span className="text-muted-foreground">Pending extension scan</span>}
                  </TableCell>
                  <TableCell className="max-w-[360px] truncate">
                    {latest.message ? `${latest.date || ""} ${latest.time || ""} ${latest.message}`.trim() : firstPackage.promise || ""}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(order.tracking_checked_at)}</TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function PaymentFailedPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  onResult,
  onRefresh,
}: {
  rows: PaymentFailure[]
  storeId: string
  page: number
  total: number
  onPage: (page: number) => void
  onResult: (modal: ModalState) => void
  onRefresh: () => Promise<void>
}) {
  const [loading, setLoading] = useState(false)

  async function markResolved(orderId: string) {
    setLoading(true)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/tracking/payment-failures/${encodeURIComponent(orderId)}/resolve`, { method: "POST" })
      onResult({ ok: result.ok, title: "Payment Issue Updated", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Payment Issue Update Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Payment Failed Orders</CardTitle>
          <CardDescription>Amazon orders where the tracking extension saw payment revision needed.</CardDescription>
        </div>
        <div className="btn-list">
          <Button variant="outline" onClick={onRefresh} disabled={loading}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <div className="border-t px-6 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="text-sm text-muted-foreground">{total.toLocaleString()} open payment issue{total === 1 ? "" : "s"}{storeId ? " for this store" : ""}.</div>
          <PaginationControls page={page} total={total} onPage={onPage} disabled={loading} />
        </div>
      </div>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Amazon Order</TableHead>
              <TableHead>Odoo Orders</TableHead>
              <TableHead>Message</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Detected</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.amazon_order_id}>
                <TableCell>
                  <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.action_url || row.amazon_order_url} target="_blank">
                    {row.amazon_order_id}
                  </a>
                </TableCell>
                <TableCell className="max-w-[260px]">
                  <div className="flex flex-wrap gap-1">
                    {(row.odoo_order_names || []).map((name) => (
                      <OdooOrderRef key={name} name={name} />
                    ))}
                  </div>
                </TableCell>
                <TableCell className="max-w-[420px]"><ErrorTooltip value={row.message || "Payment revision needed. Please update your payment method."} /></TableCell>
                <TableCell><StatusBadge value={row.status || "open"} /></TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatDateTime(row.detected_at)}</TableCell>
                <TableCell className="text-right">
                  <div className="btn-list justify-end">
                    <Button variant="outline" size="sm" onClick={() => window.open(row.action_url || row.amazon_order_url, "_blank")}>
                      <Link className="size-4" />
                      Revise
                    </Button>
                    <Button size="sm" onClick={() => markResolved(row.amazon_order_id)} disabled={loading}>
                      <CheckCircle2 className="size-4" />
                      Mark Resolved
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-sm text-muted-foreground">No Amazon payment failures are open.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function AmazonOtpPage({ onResult }: { onResult: (modal: ModalState) => void }) {
  const [rows, setRows] = useState<AmazonOtpRow[]>([])
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)

  async function refreshOtp(nextQuery = query, nextPage = page) {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (nextQuery.trim()) params.set("q", nextQuery.trim())
      params.set("page", String(nextPage))
      params.set("per_page", String(PAGE_SIZE))
      const result = await api<{ ok: boolean; rows: AmazonOtpRow[]; total: number }>(`/api/amazon-otp${params.toString() ? `?${params.toString()}` : ""}`)
      setRows(result.rows || [])
      setTotal(result.total || 0)
      setPage(nextPage)
    } catch (error) {
      onResult({ ok: false, title: "Amazon OTP Load Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  async function syncOtp() {
    setLoading(true)
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/settings/amazon-otp/sync", { method: "POST" })
      onResult({ ok: result.ok, title: "Amazon OTP Sync", message: result.message })
      await refreshOtp()
    } catch (error) {
      onResult({ ok: false, title: "Amazon OTP Sync Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshOtp(query, page)
  }, [page])

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Amazon OTP Lookup</CardTitle>
          <CardDescription>Matches Amazon OTP emails to dispatch emails and Chrome tracking captures by Amazon order number.</CardDescription>
        </div>
        <div className="btn-list">
          <Button variant="outline" onClick={() => refreshOtp()} disabled={loading}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
          <Button onClick={syncOtp} disabled={loading}>
            <Bell className="size-4" />
            Sync Email Now
          </Button>
        </div>
      </CardHeader>
      <div className="border-t px-6 py-3">
        <form className="flex flex-col gap-2 md:flex-row" onSubmit={(event) => { event.preventDefault(); setPage(1); void refreshOtp(query, 1) }}>
          <SearchBox className="w-full md:w-[520px]" value={query} onChange={setQuery} placeholder="Search OTP, tracking number, Amazon order, Odoo order, product" />
          <Button type="submit" variant="outline" disabled={loading}>
            <Search className="size-4" />
            Search
          </Button>
          <a className="btn btn-outline-secondary" href="/public/amazon-otp" target="_blank">Open Public Page</a>
        </form>
      </div>
      <div className="border-t px-6 py-3">
        <PaginationControls page={page} total={total} onPage={setPage} disabled={loading} />
      </div>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>OTP</TableHead>
              <TableHead>Tracking</TableHead>
              <TableHead>Amazon Order</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Product</TableHead>
              <TableHead>Recipient</TableHead>
              <TableHead>Emails</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.amazon_order_id}>
                <TableCell>
                  <div className="grid gap-1">
                    <span className="font-mono text-lg font-semibold">{row.otp || "Pending"}</span>
                    <StatusBadge value={row.match_status} />
                  </div>
                </TableCell>
                <TableCell className="max-w-[280px]">
                  <div className="grid gap-1 text-sm">
                    <span className="font-mono">{row.tracking_numbers || "Pending Chrome scan"}</span>
                    <span className="text-muted-foreground">{row.carriers}</span>
                    {row.tracking_url ? <a className="text-primary underline-offset-4 hover:underline" href={row.tracking_url} target="_blank">Amazon tracking email link</a> : null}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="grid gap-1">
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
                      {row.amazon_order_id}
                    </a>
                    <OdooOrderRefs names={String(row.odoo_order_names || "").split(/\s*,\s*/)} className="text-xs text-muted-foreground" />
                  </div>
                </TableCell>
                <TableCell><StatusBadge value={row.tracking_status || "Unknown"} /></TableCell>
                <TableCell className="max-w-[420px]">
                  <div className="truncate">{row.product_summary}</div>
                  <div className="text-xs text-muted-foreground">{row.store_names}</div>
                </TableCell>
                <TableCell>{row.recipient}</TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  <div>OTP: {formatDateTime(row.otp_email_date)}</div>
                  <div>Dispatch: {formatDateTime(row.dispatch_email_date)}</div>
                  <div>Updated: {formatDateTime(row.updated_at)}</div>
                </TableCell>
              </TableRow>
            ))}
            {!rows.length ? (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                  No Amazon OTP records found.
                </TableCell>
              </TableRow>
            ) : null}
          </TableBody>
          </Table>
      </CardContent>
      <CardFooter>
        <PaginationControls page={page} total={total} onPage={setPage} disabled={loading} />
      </CardFooter>
    </Card>
  )
}

function FulfilmentPendingPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onNavigate,
  onResult,
  onRefresh,
}: {
  rows: FulfilmentPendingRow[]
  storeId: string
  page: number
  total: number
  onPage: (page: number) => void
  selected: number[]
  selectAll: boolean
  onSelected: (ids: number[]) => void
  onSelectAll: (value: boolean) => void
  onNavigate: (page: string) => void
  onResult: (modal: ModalState) => void
  onRefresh: () => Promise<void>
}) {
  const [loading, setLoading] = useState(false)
  const selectionAnchor = useRef<number | null>(null)
  function rowPackages(row: FulfilmentPendingRow) {
    try {
      const parsed = JSON.parse(row.tracking_payload || "[]")
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }
  async function refresh() {
    setLoading(true)
    try {
      await onRefresh()
    } finally {
      setLoading(false)
    }
  }
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Amazon Delivered, Odoo Fulfilment Pending</CardTitle>
          <CardDescription>Delivered Amazon orders whose related Odoo pickings are not done or cancelled in the selected store.</CardDescription>
        </div>
        <Button variant="outline" onClick={refresh} disabled={loading || !storeId}>
          <RefreshCw className="size-4" />
          Check Odoo
        </Button>
      </CardHeader>
      <div className="border-t px-6 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <ExportControls view="fulfilment_pending" storeId={storeId} columns={fulfilmentPendingExportColumns} selectedIds={selected} selectAll={selectAll} total={total} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
          <PaginationControls page={page} total={total} onPage={onPage} disabled={loading} />
        </div>
      </div>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  checked={rows.length > 0 && rows.every((row) => selected.includes(row.id))}
                  onCheckedChange={(checked) => {
                    onSelectAll(false)
                    const ids = rows.map((row) => row.id)
                    onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                  }}
                />
              </TableHead>
              <TableHead>Store</TableHead>
              <TableHead>Odoo Order</TableHead>
              <TableHead>Amazon Order</TableHead>
              <TableHead>Carrier / Tracking</TableHead>
              <TableHead>Tracking</TableHead>
              <TableHead>Odoo Pickings</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Message</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const packages = rowPackages(row)
              return (
                <TableRow key={row.id} className="bg-destructive/5">
                    <TableCell>
                      <Checkbox
                        checked={selected.includes(row.id)}
                        onCheckedChange={(checked, event) => {
                          onSelectAll(false)
                          const visibleIds = rows.map((item) => item.id)
                          onSelected(rangeSelection(visibleIds, selected, row.id, Boolean(checked), event.shiftKey, selectionAnchor.current))
                          selectionAnchor.current = row.id
                        }}
                      />
                    </TableCell>
                    <TableCell>{row.store_name}</TableCell>
                    <TableCell>
                      <OdooOrderRef name={row.odoo_order_name} url={row.odoo_order_url} linkClassName="font-medium" />
                      <div className="text-xs text-muted-foreground">
                        {row.odoo_sale_state || "state unknown"} / {row.odoo_invoice_status || "invoice unknown"}
                      </div>
                    </TableCell>
                    <TableCell>
                      <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
                        {row.amazon_order_id}
                      </a>
                    </TableCell>
                    <TableCell className="max-w-[320px]">
                      {packages.length ? (
                        <div className="grid gap-1 text-sm">
                          {packages.map((pkg: any, index: number) => (
                            <a key={`${row.id}-${pkg.tracking_id || index}`} className="truncate font-mono text-primary underline-offset-4 hover:underline" href={pkg.tracking_url || row.amazon_order_url} target="_blank">
                              {pkg.carrier || "Amazon shipment"}: {pkg.tracking_id || "tracking page"}
                            </a>
                          ))}
                        </div>
                      ) : (
                        <a className="text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
                          Amazon tracking page
                        </a>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="grid gap-1">
                        <StatusBadge value={row.tracking_status || "Delivered"} />
                        <span className="text-xs text-muted-foreground">{formatDateTime(row.tracking_checked_at)}</span>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[320px]">
                      <div className="grid gap-1 text-sm">
                        {(row.picking_names || []).map((name, index) => (
                          <span key={`${row.id}-${name}-${index}`} className={row.open_picking_names?.includes(name) ? "font-medium text-destructive" : "text-muted-foreground"}>
                            {name}: {row.picking_states?.[index] || "unknown"}
                          </span>
                        ))}
                        {!row.picking_names?.length && <span className="text-destructive">No pickings found</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="destructive">Fulfilment pending</Badge>
                    </TableCell>
                    <TableCell className="max-w-[360px] truncate text-destructive">{row.message}</TableCell>
                </TableRow>
              )
            })}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                  No delivered Amazon orders are pending Odoo fulfilment.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function EpostTrackingPage({
  rows,
  storeId,
  page,
  total,
  statusFilter,
  onPage,
  onStatusFilter,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onResult,
  onNavigate,
  onRows,
}: {
  rows: EpostTrackingRow[]
  storeId: string
  page: number
  total: number
  statusFilter: string
  onPage: (page: number) => void
  onStatusFilter: (value: string) => void
  selected: number[]
  selectAll: boolean
  onSelected: (ids: number[]) => void
  onSelectAll: (value: boolean) => void
  onResult: (modal: ModalState) => void
  onNavigate: (page: string) => void
  onRows: (rows: EpostTrackingRow[], total: number) => void
}) {
  const [loading, setLoading] = useState(false)
  const [syncDays, setSyncDays] = useState("2")
  const selectionAnchor = useRef<number | null>(null)
  async function refresh() {
    setLoading(true)
    try {
      const query = new URLSearchParams()
      if (storeId) query.set("store_id", storeId)
      query.set("page", String(page))
      query.set("per_page", String(PAGE_SIZE))
      query.set("status", statusFilter)
      const result = await api<{ rows: EpostTrackingRow[]; total: number }>(`/api/epost/tracking?${query.toString()}`)
      onRows(result.rows, result.total || 0)
    } catch (error) {
      onResult({ ok: false, title: "ePost Tracking Load Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }
  async function syncFromOdoo() {
    setLoading(true)
    try {
      const result = await api<{ ok: boolean; message: string; rows: EpostTrackingRow[] }>("/api/epost/sync", {
        method: "POST",
        body: JSON.stringify({ store_id: Number(storeId), days: Number(syncDays || 2) }),
      })
      onPage(1)
      const query = new URLSearchParams()
      if (storeId) query.set("store_id", storeId)
      query.set("page", "1")
      query.set("per_page", String(PAGE_SIZE))
      query.set("status", statusFilter)
      const refreshed = await api<{ rows: EpostTrackingRow[]; total: number }>(`/api/epost/tracking?${query.toString()}`)
      onRows(refreshed.rows, refreshed.total || 0)
      onResult({ ok: true, title: "ePost Sync", message: result.message })
    } catch (error) {
      onResult({ ok: false, title: "ePost Sync Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>ePost Global Tracking</CardTitle>
          <CardDescription>Syncs EPG tracking codes from fulfilled Odoo pickings. Use the ePost tracking extension to refresh portal status in batches of 25.</CardDescription>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="w-40">
            <Label htmlFor="epost-sync-days">Odoo fulfilled since days</Label>
            <Input
              id="epost-sync-days"
              type="number"
              min={1}
              max={30}
              value={syncDays}
              onChange={(event) => setSyncDays(event.target.value)}
            />
          </div>
          <SelectField className="w-36" label="Status" value={statusFilter} onChange={onStatusFilter}>
            <option value="all">All</option>
            <option value="pending">Pending</option>
            <option value="lost">Lost</option>
            <option value="delivered">Delivered</option>
          </SelectField>
          <Button variant="outline" onClick={refresh} disabled={loading}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
          <Button onClick={syncFromOdoo} disabled={loading || !storeId}>
            <PackageCheck className="size-4" />
            Sync from Odoo
          </Button>
        </div>
      </CardHeader>
      <div className="border-t px-6 py-3">
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <ExportControls view="epost" storeId={storeId} columns={epostExportColumns} selectedIds={selected} selectAll={selectAll} total={total} filters={{ status: statusFilter }} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
          <PaginationControls page={page} total={total} onPage={onPage} disabled={loading} />
        </div>
      </div>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <Checkbox
                  checked={rows.length > 0 && rows.every((row) => selected.includes(row.id))}
                  onCheckedChange={(checked) => {
                    onSelectAll(false)
                    const ids = rows.map((row) => row.id)
                    onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                  }}
                />
              </TableHead>
              <TableHead>Store</TableHead>
              <TableHead>Odoo Order</TableHead>
              <TableHead>Amazon Order</TableHead>
              <TableHead>ePost Tracking</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Shipping Charges</TableHead>
              <TableHead>Last Update</TableHead>
              <TableHead>Destination</TableHead>
              <TableHead>AWB</TableHead>
              <TableHead>Checked</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.id} className={row.epost_status === "lost" ? "bg-destructive/10" : row.epost_status === "delivered" ? "" : "bg-muted/30"}>
                <TableCell>
                  <Checkbox
                    checked={selected.includes(row.id)}
                    onCheckedChange={(checked, event) => {
                      onSelectAll(false)
                      const visibleIds = rows.map((item) => item.id)
                      onSelected(rangeSelection(visibleIds, selected, row.id, Boolean(checked), event.shiftKey, selectionAnchor.current))
                      selectionAnchor.current = row.id
                    }}
                  />
                </TableCell>
                <TableCell>{row.store_name}</TableCell>
                <TableCell>
                  <OdooOrderRef name={row.odoo_order_name} url={row.odoo_order_url} />
                  <div className="text-xs text-muted-foreground">{row.picking_name}</div>
                </TableCell>
                <TableCell>
                  {row.amazon_order_id ? (
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
                      {row.amazon_order_id}
                    </a>
                  ) : <span className="text-muted-foreground">Not linked</span>}
                </TableCell>
                <TableCell>
                  <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.tracking_url || `https://epgtrack.com/${row.tracking_code}`} target="_blank">
                    {row.tracking_code}
                  </a>
                </TableCell>
                <TableCell>
                  <StatusBadge value={row.epost_status === "lost" ? "lost" : row.status || row.epost_status || "pending"} />
                </TableCell>
                <TableCell>
                  {Number(row.shipping_total || 0) > 0 ? (
                    <div className="grid gap-1 text-sm">
                      <span className="font-semibold">{formatMoney(Number(row.shipping_total || 0))}</span>
                      <span className="text-xs text-muted-foreground">
                        Ship {formatMoney(Number(row.shipping_fee || 0))} · Fulfil {formatMoney(Number(row.fulfilment_fee || 0))}
                      </span>
                      <Badge variant="outline">{row.shipping_match_type === "tracking" ? "tracking matched" : "order matched"}</Badge>
                    </div>
                  ) : (
                    <span className="text-muted-foreground">Not synced</span>
                  )}
                </TableCell>
                <TableCell>{row.last_update_at || ""}</TableCell>
                <TableCell>{row.destination || ""}</TableCell>
                <TableCell>{row.awb || ""}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatDateTime(row.last_checked_at)}</TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={11} className="py-8 text-center text-muted-foreground">
                  No ePost Global tracking codes match this filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function DuplicateTrackingPage({
  storeId,
  onResult,
  onNavigate,
}: {
  storeId: string
  onResult: (modal: ModalState) => void
  onNavigate: (page: string) => void
}) {
  const [rows, setRows] = useState<DuplicateTrackingRow[]>([])
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [selectAll, setSelectAll] = useState(false)
  const selectionAnchor = useRef<string | null>(null)

  async function load(nextPage = page) {
    setLoading(true)
    try {
      const params = new URLSearchParams({ q: query })
      if (storeId) params.set("store_id", storeId)
      params.set("page", String(nextPage))
      params.set("per_page", String(PAGE_SIZE))
      const result = await api<{ rows: DuplicateTrackingRow[]; total: number }>(`/api/duplicate-tracking?${params.toString()}`)
      setRows(result.rows || [])
      setTotal(result.total || 0)
      setPage(nextPage)
      setSelected([])
      setSelectAll(false)
    } catch (error) {
      onResult({ ok: false, title: "Duplicate Tracking Load Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Duplicate Tracking Load Failed", message: String(error) }))
  }, [storeId, page])

  function applyDuplicateTrackingFilter() {
    if (page === 1) {
      void load(1)
      return
    }
    setPage(1)
  }

  const totals = rows.reduce(
    (acc, row) => {
      acc.codes += 1
      acc.orders += Number(row.order_count || 0)
      acc.shipping += Number(row.shipping_total || 0)
      acc.invoices += Number(row.invoice_count || 0)
      return acc
    },
    { codes: 0, orders: 0, shipping: 0, invoices: 0 },
  )

  return (
    <div className="grid gap-3">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {[
          ["Duplicate Codes", totals.codes.toLocaleString()],
          ["Linked Orders", totals.orders.toLocaleString()],
          ["Duplicate Charges", formatMoney(totals.shipping)],
          ["Stored Invoices", totals.invoices.toLocaleString()],
        ].map(([label, value]) => (
          <Card key={label}>
            <CardContent>
              <div className="text-secondary text-xs font-bold uppercase">{label}</div>
              <div className="mt-2 text-2xl font-semibold">{value}</div>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Duplicate Tracking Review</CardTitle>
            <CardDescription>Compares ePost Global tracking, imported shipping invoices, linked Odoo orders, and stored accounting files.</CardDescription>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-80 max-w-full">
              <Label>Search</Label>
              <SearchBox value={query} onChange={setQuery} placeholder="Tracking, Odoo order, invoice file" />
            </div>
            <Button variant="outline" onClick={applyDuplicateTrackingFilter} disabled={loading}>
              <Search className="size-4" />
              Filter
            </Button>
            <Button variant="outline" onClick={() => load(page)} disabled={loading}>
              <RefreshCw className="size-4" />
              Refresh
            </Button>
          </div>
        </CardHeader>
        <div className="border-t px-6 py-3">
          <ExportControls
            view="duplicate_tracking"
            storeId={storeId}
            columns={duplicateTrackingExportColumns}
            selectedIds={selected}
            selectAll={selectAll}
            total={total}
            filters={{ q: query }}
            onSelectAll={() => setSelectAll(true)}
            onClear={() => {
              setSelectAll(false)
              setSelected([])
            }}
            onResult={onResult}
            onDownloads={() => onNavigate("downloads")}
          />
        </div>
        <div className="border-t px-6 py-3">
          <PaginationControls page={page} total={total} onPage={setPage} disabled={loading} />
        </div>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={rows.length > 0 && rows.every((row) => selected.includes(row.tracking_code))}
                    onCheckedChange={(checked) => {
                      setSelectAll(false)
                      setSelected(checked ? rows.map((row) => row.tracking_code) : [])
                    }}
                  />
                </TableHead>
                <TableHead>Tracking Code</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Linked Orders</TableHead>
                <TableHead>Source Rows</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Charges</TableHead>
                <TableHead>Invoices</TableHead>
                <TableHead>Last Seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.tracking_code}>
                  <TableCell>
                    <Checkbox
                      checked={selected.includes(row.tracking_code)}
                      onCheckedChange={(checked, event) => {
                        setSelectAll(false)
                        const visibleIds = rows.map((item) => item.tracking_code)
                        setSelected(rangeSelection(visibleIds, selected, row.tracking_code, Boolean(checked), event.shiftKey, selectionAnchor.current))
                        selectionAnchor.current = row.tracking_code
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.tracking_url} target="_blank">
                      {row.tracking_code}
                    </a>
                    <div className="text-xs text-muted-foreground">{row.destinations.join(", ") || "No destination yet"}</div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {row.duplicate_reason.split(", ").map((reason) => (
                        <Badge key={reason} variant={reason.includes("mismatch") ? "destructive" : "secondary"}>{reason}</Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="min-w-[220px]">
                    <div className="grid gap-1">
                      {row.odoo_order_names.map((order) => (
                        <OdooOrderRef key={order} name={order} linkClassName="font-medium" />
                      ))}
                      {!row.odoo_order_names.length && <span className="text-muted-foreground">No Odoo order linked</span>}
                      {!!row.amazon_order_ids.length && <span className="text-xs text-muted-foreground">Amazon {row.amazon_order_ids.join(", ")}</span>}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1 text-sm">
                      <span>ePost: {row.epost_row_count}</span>
                      <span>Invoices: {row.shipping_row_count}</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {(row.statuses.length ? row.statuses : ["pending"]).map((status) => (
                        <StatusBadge key={status} value={status} />
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1 text-sm">
                      <span className="font-semibold">{formatMoney(Number(row.shipping_total || 0))}</span>
                      <span className="text-xs text-muted-foreground">
                        Ship {formatMoney(Number(row.shipping_fee || 0))} · Fulfil {formatMoney(Number(row.fulfilment_fee || 0))}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    {row.documents.length ? (
                      <div className="grid gap-1">
                        {row.documents.slice(0, 3).map((doc) => (
                          doc.storage_url ? (
                            <a key={doc.id} className="text-primary underline-offset-4 hover:underline" href={doc.storage_url} target="_blank">
                              {doc.stored_filename}
                            </a>
                          ) : (
                            <span key={doc.id}>{doc.stored_filename}</span>
                          )
                        ))}
                        {row.documents.length > 3 && <span className="text-xs text-muted-foreground">+{row.documents.length - 3} more</span>}
                      </div>
                    ) : (
                      <span className="text-muted-foreground">No stored invoice</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(row.last_seen_at)}</TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow>
                  <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">
                    No duplicate tracking charges found for this filter.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
        <CardFooter>
          <PaginationControls page={page} total={total} onPage={setPage} disabled={loading} />
        </CardFooter>
      </Card>
    </div>
  )
}

function InventoryPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  onRows,
  onResult,
}: {
  rows: InventoryItem[]
  storeId: string
  page: number
  total: number
  onPage: (page: number) => void
  onRows: (rows: InventoryItem[], total: number) => void
  onResult: (modal: ModalState) => void
}) {
  const [form, setForm] = useState({ asin: "", quantity: "1", product_name: "", odoo_order_name: "", amazon_order_id: "", amazon_order_url: "", notes: "" })
  async function addManualInventory() {
    try {
      const result = await api<{ ok: boolean; message: string; items: InventoryItem[]; total: number }>("/api/inventory", {
        method: "POST",
        body: JSON.stringify({ ...form, store_id: Number(storeId), quantity: Number(form.quantity || 1) }),
      })
      setForm({ asin: "", quantity: "1", product_name: "", odoo_order_name: "", amazon_order_id: "", amazon_order_url: "", notes: "" })
      onPage(1)
      onRows(result.items, result.total || 0)
      onResult({ ok: true, title: "Inventory Added", message: result.message })
    } catch (error) {
      onResult({ ok: false, title: "Inventory Add Failed", message: String(error) })
    }
  }
  return (
    <div className="grid gap-5">
      <Card>
        <CardHeader>
          <CardTitle>Inventory</CardTitle>
          <CardDescription>Available warehouse stock from delivered Amazon orders whose Odoo order was cancelled/refunded, plus manually added stock. Inventory-matched orders are not auto-bought by Chrome.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_120px_1.2fr_1fr_1fr_1.2fr_auto]">
          <div className="grid gap-1.5">
            <Label>ASIN</Label>
            <Input value={form.asin} onChange={(event) => setForm({ ...form, asin: event.target.value.toUpperCase() })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Qty</Label>
            <Input type="number" min={1} value={form.quantity} onChange={(event) => setForm({ ...form, quantity: event.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Product</Label>
            <Input value={form.product_name} onChange={(event) => setForm({ ...form, product_name: event.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Odoo Ref</Label>
            <Input value={form.odoo_order_name} onChange={(event) => setForm({ ...form, odoo_order_name: event.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Amazon Order</Label>
            <Input value={form.amazon_order_id} onChange={(event) => setForm({ ...form, amazon_order_id: event.target.value })} />
          </div>
          <div className="grid gap-1.5">
            <Label>Notes</Label>
            <Input value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
          </div>
          <div className="flex items-end">
            <Button disabled={!storeId || (!form.asin.trim() && !form.odoo_order_name.trim() && !form.amazon_order_id.trim())} onClick={addManualInventory}>
              <Plus className="size-4" />
              Add
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Inventory Items</CardTitle>
            <CardDescription>Showing max 100 rows per page. Available rows can be reserved against future Odoo orders with matching ASIN and quantity.</CardDescription>
          </div>
          <PaginationControls page={page} total={total} onPage={onPage} />
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ASIN</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Source</TableHead>
                <TableHead>Amazon Order</TableHead>
                <TableHead>Reserved For</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={`https://www.amazon.com/dp/${item.asin}`} target="_blank">{item.asin}</a>
                  </TableCell>
                  <TableCell>{item.quantity}</TableCell>
                  <TableCell className="max-w-[360px] truncate">{item.product_name}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{item.source_type || "amazon_cancelled"}</Badge>
                    {item.source_odoo_order_name ? <OdooOrderRef name={item.source_odoo_order_name} className="text-xs text-muted-foreground" /> : <div className="text-xs text-muted-foreground">{item.notes || ""}</div>}
                  </TableCell>
                  <TableCell>
                    {item.amazon_order_id ? <a className="font-mono text-xs text-primary underline-offset-4 hover:underline" href={item.amazon_order_url} target="_blank">{item.amazon_order_id}</a> : <span className="text-muted-foreground">Manual</span>}
                    <div className="text-xs text-muted-foreground">{item.amazon_account_name || ""}</div>
                  </TableCell>
                  <TableCell>{item.reserved_order_line_id ? <Badge variant="secondary">Line {item.reserved_order_line_id}</Badge> : ""}</TableCell>
                  <TableCell><StatusBadge value={item.status} /></TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(item.updated_at)}</TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">No inventory rows on this page.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function CancelledOrdersPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  onRows,
  onResult,
}: {
  rows: CancelledOrderRow[]
  storeId: string
  page: number
  total: number
  onPage: (page: number) => void
  onRows: (rows: CancelledOrderRow[], total: number) => void
  onResult: (modal: ModalState) => void
}) {
  const [syncing, setSyncing] = useState(false)
  async function syncCancelledOrders() {
    setSyncing(true)
    try {
      const result = await api<{ ok: boolean; message: string; rows: CancelledOrderRow[]; total: number }>("/api/cancelled-orders/sync", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId ? Number(storeId) : null }),
      })
      onPage(1)
      onRows(result.rows || [], result.total || 0)
      onResult({ ok: result.ok, title: "Cancelled Orders Sync", message: result.message })
    } catch (error) {
      onResult({ ok: false, title: "Cancelled Orders Sync Failed", message: String(error) })
    } finally {
      setSyncing(false)
    }
  }
  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Cancelled Orders</h2>
          <p className="text-sm text-muted-foreground">Cancelled or refunded Odoo orders with an Amazon order reference are tracked here and added to inventory when delivered.</p>
        </div>
        <div className="btn-list">
          <Button variant="outline" onClick={syncCancelledOrders} disabled={syncing}>
            <RefreshCw className="size-4" />
            {syncing ? "Syncing..." : "Check Odoo"}
          </Button>
          <PaginationControls page={page} total={total} onPage={onPage} disabled={syncing} />
        </div>
      </section>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Odoo Order</TableHead>
                <TableHead>Amazon Order</TableHead>
                <TableHead>ASIN</TableHead>
                <TableHead>Item</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Odoo Status</TableHead>
                <TableHead>Amazon Status</TableHead>
                <TableHead>Inventory</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    <OdooOrderRef name={row.odoo_order_name} url={row.odoo_order_url} linkClassName="font-semibold" />
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-xs text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">{row.amazon_order_id}</a>
                    <div className="text-xs text-muted-foreground">{row.amazon_account_name || ""}</div>
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.asin_url || `https://www.amazon.com/dp/${row.asin}`} target="_blank">{row.asin}</a>
                  </TableCell>
                  <TableCell className="max-w-[360px] truncate">{row.product_name}</TableCell>
                  <TableCell>{row.quantity}</TableCell>
                  <TableCell><StatusBadge value={row.odoo_status_label} /></TableCell>
                  <TableCell>
                    <StatusBadge value={row.tracking_status || row.state || "ordered"} />
                  </TableCell>
                  <TableCell>
                    {row.inventory_item_id ? (
                      <Badge variant={row.inventory_status === "available" ? "default" : "secondary"}>{row.inventory_status || "inventory"}</Badge>
                    ) : (
                      <span className="text-muted-foreground">Not added</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDateTime(row.updated_at)}</TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow>
                  <TableCell colSpan={9} className="py-8 text-center text-muted-foreground">No cancelled ordered rows found for this filter.</TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function PartialFulfilmentsPage({
  rows,
  page,
  total,
  onPage,
  onResult,
  onRefresh,
}: {
  rows: PartialFulfilment[]
  page: number
  total: number
  onPage: (page: number) => void
  onResult: (modal: ModalState) => void
  onRefresh: () => Promise<void>
}) {
  async function markProcessed(row: PartialFulfilment) {
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/partial-fulfilments/${row.id}/processed`, { method: "POST" })
      onResult({ ok: true, title: "Processed", message: result.message || "Partial fulfilment removed from the queue." })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Could Not Mark Processed", message: String(error) })
    }
  }

  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Partial Fulfilments</h2>
          <p className="text-sm text-muted-foreground">Orders where one line moved to Missing ASINs while the remaining line(s) continue through Amazon fulfilment.</p>
        </div>
        <div className="btn-list">
          <Button variant="outline" onClick={onRefresh}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>
      </section>
      <div className="flex justify-end">
        <PaginationControls page={page} total={total} onPage={onPage} />
      </div>
      {rows.length ? (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Order</TableHead>
                  <TableHead>Amazon Order</TableHead>
                  <TableHead>Missing ASINs</TableHead>
                  <TableHead>Remaining Lines</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>
                      <OdooOrderRef name={row.odoo_order_name} linkClassName="font-medium" />
                      <div className="text-xs text-muted-foreground">{row.amazon_group_key || "No Chrome group key"}</div>
                    </TableCell>
                    <TableCell>
                      <div className="grid gap-1">
                        {row.amazon_orders?.length ? row.amazon_orders.map((order) => (
                          <a
                            key={order.amazon_order_id}
                            className="font-mono text-xs text-primary underline-offset-4 hover:underline"
                            href={order.amazon_order_url || `https://www.amazon.com/your-orders/order-details?orderID=${encodeURIComponent(order.amazon_order_id)}`}
                            target="_blank"
                          >
                            {order.amazon_order_id}
                          </a>
                        )) : <span className="text-muted-foreground">Pending</span>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-2">
                        {row.missing_asins.length ? row.missing_asins.map((asin) => (
                          <a key={asin} className="font-mono text-destructive underline-offset-4 hover:underline" href={row.missing_asin_urls?.[asin] || `https://www.amazon.com/dp/${asin}`} target="_blank">
                            {asin}
                          </a>
                        )) : <span className="text-muted-foreground">Unknown</span>}
                      </div>
                    </TableCell>
                    <TableCell>{row.remaining_line_ids.length}</TableCell>
                    <TableCell className="max-w-[420px]"><ErrorTooltip value={row.message || ""} /></TableCell>
                    <TableCell>{formatDateTime(row.updated_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        {row.odoo_order_url ? (
                          <Button size="sm" variant="outline" onClick={() => window.open(row.odoo_order_url, "_blank")}>
                            Open Odoo
                          </Button>
                        ) : null}
                        <Button size="sm" onClick={() => markProcessed(row)}>
                          Processed
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">No partial fulfilments are waiting for review.</CardContent>
        </Card>
      )}
    </div>
  )
}

function MissingPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onNavigate,
  onResult,
  onAssign,
  onRefresh,
}: {
  rows: OrderLine[]
  storeId: number
  page: number
  total: number
  onPage: (page: number) => void
  selected: number[]
  selectAll: boolean
  onSelected: (ids: number[]) => void
  onSelectAll: (value: boolean) => void
  onNavigate: (page: string) => void
  onResult: (modal: ModalState) => void
  onAssign: (line: OrderLine) => void
  onRefresh: () => Promise<void>
}) {
  const selectionAnchor = useRef<number | null>(null)
  const groups = useMemo(() => {
    const grouped = new Map<string, OrderLine[]>()
    rows.forEach((row) => {
      const key = row.odoo_order_name || String(row.id)
      grouped.set(key, [...(grouped.get(key) || []), row])
    })
    return [...grouped.entries()]
  }, [rows])
  async function requeueLines(lineIds: number[]) {
    if (!lineIds.length) return
    try {
      const result = await api<DashboardData>("/api/lines/reset-fulfilment", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, line_ids: lineIds }),
      })
      onResult({ ok: true, title: "Requeued", message: result.message || "Selected missing line(s) were requeued for normal fulfilment." })
      onSelected([])
      onSelectAll(false)
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Requeue Failed", message: String(error) })
    }
  }
  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Missing Orders</h2>
          <p className="text-sm text-muted-foreground">Orders paused because one or more Amazon ASINs were unavailable during fulfilment.</p>
        </div>
        <div className="btn-list">
          <Button variant="outline" disabled={!selected.length} onClick={() => requeueLines(selected)}>
            <RefreshCw className="size-4" />
            Requeue Selected
          </Button>
          <Button variant="outline" onClick={onRefresh}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>
      </section>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <ExportControls view="missing" storeId={String(storeId || "")} columns={missingExportColumns} selectedIds={selected} selectAll={selectAll} total={total} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
        <PaginationControls page={page} total={total} onPage={onPage} />
      </div>
      {groups.length ? groups.map(([orderName, groupRows]) => (
        <Card key={orderName}>
          <CardHeader className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <CardTitle className="text-destructive">{orderName}</CardTitle>
              <CardDescription>{groupRows.length} line item{groupRows.length === 1 ? "" : "s"} paused for replacement review.</CardDescription>
            </div>
            {groupRows[0].odoo_order_url && (
              <Button variant="outline" onClick={() => window.open(groupRows[0].odoo_order_url, "_blank")}>
                Open Odoo
              </Button>
            )}
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={groupRows.length > 0 && groupRows.every((row) => selected.includes(row.id))}
                      onCheckedChange={(checked) => {
                        onSelectAll(false)
                        const ids = groupRows.map((row) => row.id)
                        onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                      }}
                    />
                  </TableHead>
                  <TableHead>Missing ASIN</TableHead>
                  <TableHead>Product</TableHead>
                  <TableHead>Qty</TableHead>
                  <TableHead>Error</TableHead>
                  <TableHead>Replacement</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groupRows.map((row) => (
                  <TableRow
                    key={row.id}
                    className={isLimitPurchaseLine(row) ? "bg-red-50" : isPartialQuantityLine(row) ? "bg-orange-50" : ""}
                  >
                    <TableCell>
                      <Checkbox
                        checked={selected.includes(row.id)}
                        onCheckedChange={(checked, event) => {
                          onSelectAll(false)
                          const visibleIds = rows.map((item) => item.id)
                          onSelected(rangeSelection(visibleIds, selected, row.id, Boolean(checked), event.shiftKey, selectionAnchor.current))
                          selectionAnchor.current = row.id
                        }}
                      />
                    </TableCell>
                    <TableCell className="font-mono">
                      <a className="text-destructive underline-offset-4 hover:underline" href={row.missing_asin_url || `https://www.amazon.com/dp/${row.missing_asin || row.asin}`} target="_blank">
                        {row.missing_asin || row.original_asin || row.asin}
                      </a>
                    </TableCell>
                    <TableCell className="max-w-[460px] truncate">{row.product_name}</TableCell>
                    <TableCell>{row.quantity}</TableCell>
                    <TableCell className="max-w-[360px]"><ErrorTooltip value={row.last_error} /></TableCell>
                    <TableCell>
                      {row.replacement_asin ? (
                        <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.replacement_asin_url || `https://www.amazon.com/dp/${row.replacement_asin}`} target="_blank">
                          {row.replacement_asin}
                        </a>
                      ) : (
                        <span className="text-muted-foreground">Not assigned</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="outline" onClick={() => requeueLines([row.id])} disabled={!storeId}>
                          <RefreshCw className="size-4" />
                          Requeue
                        </Button>
                        <Button size="sm" onClick={() => onAssign(row)} disabled={!storeId}>
                          Assign ASIN
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )) : (
        <Card>
          <CardContent className="py-8 text-sm text-muted-foreground">No missing orders for this store.</CardContent>
        </Card>
      )}
    </div>
  )
}

function BackInStockPage({
  rows,
  page,
  total,
  onPage,
  onResult,
  onRefresh,
}: {
  rows: BackInStockRow[]
  page: number
  total: number
  onPage: (page: number) => void
  onResult: (modal: ModalState) => void
  onRefresh: () => Promise<void>
}) {
  const [approving, setApproving] = useState<number | null>(null)
  const [removing, setRemoving] = useState<number | null>(null)
  async function approve(row: BackInStockRow) {
    const confirmed = window.confirm(`Approve ${row.asin} for ${row.odoo_order_name || row.odoo_order_id}? The app will check Odoo first and only queue Chrome if the Odoo order is not cancelled.`)
    if (!confirmed) return
    setApproving(row.id)
    try {
      const result = await api<{ ok: boolean; message: string; queued: number }>(`/api/back-in-stock/${row.id}/approve`, { method: "POST" })
      onResult({ ok: result.ok, title: result.ok ? "Back In Stock Approved" : "Approval Skipped", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Approval Failed", message: String(error) })
    } finally {
      setApproving(null)
    }
  }
  async function remove(row: BackInStockRow) {
    const confirmed = window.confirm(`Remove ${row.asin} for ${row.odoo_order_name || row.odoo_order_id} from Back In Stock review? No fulfilment action will be taken.`)
    if (!confirmed) return
    setRemoving(row.id)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/back-in-stock/${row.id}`, { method: "DELETE" })
      onResult({ ok: result.ok, title: "Back In Stock Removed", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Remove Failed", message: String(error) })
    } finally {
      setRemoving(null)
    }
  }
  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Back In Stock</h2>
          <p className="text-sm text-muted-foreground">Missing ASINs the Chrome extension checked in the background and found available again.</p>
        </div>
        <Button variant="outline" onClick={onRefresh}>
          <RefreshCw className="size-4" />
          Refresh
        </Button>
      </section>
      <div className="flex justify-end">
        <PaginationControls page={page} total={total} onPage={onPage} />
      </div>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Odoo Order</TableHead>
                <TableHead>ASIN</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Price</TableHead>
                <TableHead>Queue</TableHead>
                <TableHead>Checked</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.id}
                  className={row.status === "back_in_stock" ? "bg-emerald-50" : row.queued_at ? "bg-blue-50" : row.status === "replacement_assigned" ? "bg-amber-50" : row.status === "odoo_cancelled" || row.status === "odoo_refunded" ? "bg-red-50" : ""}
                >
                  <TableCell>
                    <OdooOrderRef name={row.odoo_order_name || row.odoo_order_id} url={row.odoo_order_url} linkClassName="font-medium" />
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={row.asin_url || `https://www.amazon.com/dp/${row.asin}`} target="_blank">
                      {row.asin}
                    </a>
                  </TableCell>
                  <TableCell className="max-w-[420px] truncate">{row.product_name || "-"}</TableCell>
                  <TableCell>
                    <div className="grid gap-1">
                      <StatusBadge value={(row.status || "unknown").replaceAll("_", " ")} />
                      {row.status === "replacement_assigned" ? (
                        <span className="text-xs text-muted-foreground">Skipped because a replacement ASIN is assigned.</span>
                      ) : null}
                      {row.status === "back_in_stock" && !row.queued_at ? (
                        <span className="text-xs text-muted-foreground">Needs manual approval before Chrome queue.</span>
                      ) : null}
                      {row.status === "odoo_cancelled" || row.status === "odoo_refunded" ? (
                        <span className="text-xs text-muted-foreground">Odoo check blocked fulfilment.</span>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell>{row.price ? formatMoney(row.price) : "-"}</TableCell>
                  <TableCell>
                    <div className="grid gap-1 text-sm">
                      {row.queued_at ? <Badge variant="secondary">Queued for Chrome</Badge> : <span className="text-muted-foreground">Not queued</span>}
                      {row.amazon_group_key ? <span className="font-mono text-xs text-muted-foreground">{row.amazon_group_key}</span> : null}
                    </div>
                  </TableCell>
                  <TableCell className="max-w-[260px]">
                    <div className="grid gap-1 text-xs text-muted-foreground">
                      <span>{formatDateTime(row.last_checked_at)}</span>
                      {row.availability_message ? <span className="truncate" title={row.availability_message}>{row.availability_message}</span> : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        disabled={approving === row.id || removing === row.id || Boolean(row.queued_at) || row.status !== "back_in_stock"}
                        onClick={() => approve(row)}
                      >
                        {approving === row.id ? "Approving..." : "Approve"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={approving === row.id || removing === row.id}
                        onClick={() => remove(row)}
                      >
                        {removing === row.id ? "Removing..." : "Remove"}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!rows.length && (
                <TableRow>
                  <TableCell colSpan={8} className="py-8 text-center text-sm text-muted-foreground">
                    No back-in-stock ASINs reported yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function BulkPage({
  groups,
  storeId,
  addressId,
  amazonAccountId,
  orderingEngine,
  page,
  total,
  days,
  activeDays,
  onDays,
  onPage,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onRefresh,
  onResult,
  onNavigate,
}: {
  groups: BulkGroup[]
  storeId: number
  addressId: number
  amazonAccountId: number
  orderingEngine: string
  page: number
  total: number
  days: string
  activeDays: number
  onDays: (days: string) => void
  onPage: (page: number) => void
  selected: string[]
  selectAll: boolean
  onSelected: (ids: string[]) => void
  onSelectAll: (value: boolean) => void
  onRefresh: () => Promise<void>
  onResult: (modal: ModalState) => void
  onNavigate: (page: string) => void
}) {
  const selectionAnchor = useRef<string | null>(null)

  async function placeBulk(group: BulkGroup) {
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/bulk/place", {
        method: "POST",
        body: JSON.stringify({
          store_id: storeId,
          address_id: addressId,
          amazon_account_id: amazonAccountId,
          ordering_engine: orderingEngine,
          line_ids: group.line_ids,
        }),
      })
      onResult({ ok: Boolean(result.ok), title: "Bulk Order", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Bulk Order Failed", message: String(error) })
    }
  }
  return (
    <div className="grid gap-5">
      <section>
        <h2 className="text-lg font-semibold tracking-tight">Bulk Buying Opportunities</h2>
        <p className="text-sm text-muted-foreground">Same ASIN demand combined across pulled orders by Odoo order date. Showing the last {activeDays.toLocaleString()} day{activeDays === 1 ? "" : "s"}.</p>
      </section>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div className="w-full md:w-40">
          <TextField
            label="Days"
            type="number"
            value={days}
            onChange={(value) => {
              onDays(value)
              onPage(1)
              onSelectAll(false)
              onSelected([])
              selectionAnchor.current = null
            }}
          />
        </div>
        <div className="flex flex-col gap-2 md:flex-row md:items-center">
          <ExportControls view="bulk" storeId={String(storeId || "")} columns={bulkExportColumns} selectedIds={selected} selectAll={selectAll} total={total} filters={{ days: days || "2" }} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
          <PaginationControls page={page} total={total} onPage={onPage} />
        </div>
      </div>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={groups.length > 0 && groups.every((group) => selected.includes(group.asin))}
                    onCheckedChange={(checked) => {
                      onSelectAll(false)
                      const ids = groups.map((group) => group.asin)
                      onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                    }}
                  />
                </TableHead>
                <TableHead>ASIN</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Orders</TableHead>
                <TableHead>Products</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => (
                <TableRow key={group.asin}>
                  <TableCell>
                    <Checkbox
                      checked={selected.includes(group.asin)}
                      onCheckedChange={(checked, event) => {
                        onSelectAll(false)
                        const visibleIds = groups.map((item) => item.asin)
                        onSelected(rangeSelection(visibleIds, selected, group.asin, Boolean(checked), event.shiftKey, selectionAnchor.current))
                        selectionAnchor.current = group.asin
                      }}
                    />
                  </TableCell>
                  <TableCell className="font-mono">
                    <a className="text-primary underline-offset-4 hover:underline" href={group.asin_url} target="_blank">{group.asin}</a>
                  </TableCell>
                  <TableCell>{group.quantity}</TableCell>
                  <TableCell><OdooOrderRefs names={group.order_names} /></TableCell>
                  <TableCell className="max-w-[520px] truncate">{group.product_names.join(" / ")}</TableCell>
                  <TableCell>{group.has_missing_order ? <Badge variant="destructive">missing review</Badge> : <Badge variant="secondary">ready</Badge>}</TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" onClick={() => placeBulk(group)} disabled={!storeId || !addressId || !amazonAccountId}>
                      Place Bulk
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function CostlyPage({
  rows,
  storeId,
  page,
  total,
  onPage,
  selected,
  selectAll,
  onSelected,
  onSelectAll,
  onRefresh,
  onResult,
  onNavigate,
}: {
  rows: OrderLine[]
  storeId: number
  page: number
  total: number
  onPage: (page: number) => void
  selected: number[]
  selectAll: boolean
  onSelected: (ids: number[]) => void
  onSelectAll: (value: boolean) => void
  onRefresh: () => Promise<void>
  onResult: (modal: ModalState) => void
  onNavigate: (page: string) => void
}) {
  const selectionAnchor = useRef<number | null>(null)

  async function approve(lineIds: number[]) {
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/costly/approve", {
        method: "POST",
        body: JSON.stringify({ store_id: storeId, line_ids: lineIds }),
      })
      onResult({ ok: true, title: "Costly Fulfilment Approved", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Approval Failed", message: String(error) })
    }
  }
  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Costly Orders</h2>
          <p className="text-sm text-muted-foreground">Orders paused because Amazon cost is higher than the store sale value.</p>
        </div>
        <Button variant="outline" onClick={onRefresh}><RefreshCw className="size-4" />Refresh</Button>
      </section>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <ExportControls view="costly" storeId={String(storeId || "")} columns={costlyExportColumns} selectedIds={selected} selectAll={selectAll} total={total} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
        <PaginationControls page={page} total={total} onPage={onPage} />
      </div>
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={rows.length > 0 && rows.every((row) => selected.includes(row.id))}
                    onCheckedChange={(checked) => {
                      onSelectAll(false)
                      const ids = rows.map((row) => row.id)
                      onSelected(checked ? Array.from(new Set([...selected, ...ids])) : selected.filter((id) => !ids.includes(id)))
                    }}
                  />
                </TableHead>
                <TableHead>Order</TableHead>
                <TableHead>ASIN</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>Loss</TableHead>
                <TableHead>Error</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell>
                    <Checkbox
                      checked={selected.includes(row.id)}
                      onCheckedChange={(checked, event) => {
                        onSelectAll(false)
                        const visibleIds = rows.map((item) => item.id)
                        onSelected(rangeSelection(visibleIds, selected, row.id, Boolean(checked), event.shiftKey, selectionAnchor.current))
                        selectionAnchor.current = row.id
                      }}
                    />
                  </TableCell>
                  <TableCell><OdooOrderRef name={row.odoo_order_name} url={row.odoo_order_url} /></TableCell>
                  <TableCell className="font-mono"><a className="text-primary underline-offset-4 hover:underline" href={row.asin_url || `https://www.amazon.com/dp/${row.asin}`} target="_blank">{row.asin}</a></TableCell>
                  <TableCell className="max-w-[420px] truncate">{row.product_name}</TableCell>
                  <TableCell className="text-destructive">{Number(row.cost_review_loss || 0).toFixed(2)}</TableCell>
                  <TableCell className="max-w-[420px]"><ErrorTooltip value={row.last_error} /></TableCell>
                  <TableCell className="text-right"><Button size="sm" onClick={() => approve([row.id])}>Approve Fulfilment</Button></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function ProfitLossPage({
  storeId,
  period,
  onPeriod,
  onResult,
}: {
  stores: Store[]
  storeId: string
  period: string
  onPeriod: (period: string) => void
  onResult: (modal: ModalState) => void
}) {
  const [data, setData] = useState<ProfitLossData | null>(null)
  const [loading, setLoading] = useState(false)
  const loadSeqRef = useRef(0)
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7))
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10))
  const [weekStart, setWeekStart] = useState(() => {
    const now = new Date()
    const dayOfWeek = now.getDay() || 7
    now.setDate(now.getDate() - dayOfWeek + 1)
    return now.toISOString().slice(0, 10)
  })
  const [query, setQuery] = useState("")
  const [profitScope, setProfitScope] = useState("all")
  const [page, setPage] = useState(1)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [manualCostId, setManualCostId] = useState<number | null>(null)
  const [manualCostLabel, setManualCostLabel] = useState("Google Ads")
  const [manualCostAmount, setManualCostAmount] = useState("")
  const [manualCostMonth, setManualCostMonth] = useState(month)
  const [manualCostNote, setManualCostNote] = useState("")

  async function load(nextPage = page) {
    const requestSeq = loadSeqRef.current + 1
    loadSeqRef.current = requestSeq
    setLoading(true)
    const params = new URLSearchParams({ period, q: query })
    if (period === "monthly") {
      params.set("month", month)
    } else if (period === "weekly") {
      params.set("start", weekStart)
    } else {
      params.set("start", day)
    }
    if (storeId) params.set("store_id", storeId)
    params.set("profit_scope", profitScope)
    params.set("page", String(nextPage))
    params.set("per_page", String(PAGE_SIZE))
    const cacheKey = `${PROFIT_LOSS_CACHE_PREFIX}${params.toString()}`
    let usedCachedData = false
    if (typeof window !== "undefined" && !data) {
      try {
        const cached = JSON.parse(window.localStorage.getItem(cacheKey) || "null")
        if (cached?.data && Date.now() - Number(cached.savedAt || 0) < PROFIT_LOSS_CACHE_MAX_AGE_MS) {
          setData(cached.data)
          setPage(cached.data.page || nextPage)
          setLoading(false)
          usedCachedData = true
        }
      } catch {
        usedCachedData = false
      }
    }
    try {
      const next = await api<ProfitLossData>(`/api/profit-loss?${params.toString()}`)
      if (requestSeq !== loadSeqRef.current) return
      setData(next)
      setPage(next.page || nextPage)
      try {
        window.localStorage.setItem(cacheKey, JSON.stringify({ savedAt: Date.now(), data: next }))
      } catch {
        // Ignore storage quota/private-mode failures; the live API result is already rendered.
      }
    } finally {
      if (requestSeq === loadSeqRef.current || usedCachedData) setLoading(false)
    }
  }

  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Profit/Loss load failed", message: String(error) }))
  }, [storeId, period, month, day, weekStart, profitScope, page])

  async function uploadShipping() {
    if (!file) return
    setBusy(true)
    try {
      const form = new FormData()
      form.append("file", file)
      form.append("month", month)
      form.append("default_fulfilment_fee", "4")
      const result = await uploadWithAdminToken("/api/profit-loss/shipping-upload", form)
      onResult({ ok: true, title: "Shipping Imported", message: result.message || "Imported." })
      setFile(null)
      await load(page)
    } catch (error) {
      onResult({ ok: false, title: "Shipping Import Failed", message: String(error) })
    } finally {
      setBusy(false)
    }
  }

  function applyFilters() {
    if (page === 1) {
      void load(1).catch((error) => onResult({ ok: false, title: "Profit/Loss load failed", message: String(error) }))
      return
    }
    setPage(1)
  }

  function resetManualCostForm() {
    setManualCostId(null)
    setManualCostLabel("Google Ads")
    setManualCostAmount("")
    setManualCostMonth(period === "monthly" ? month : period === "weekly" ? weekStart.slice(0, 7) : day.slice(0, 7))
    setManualCostNote("")
  }

  function editManualCost(row: Record<string, string | number | null>) {
    setManualCostId(Number(row.id || 0) || null)
    setManualCostLabel(String(row.label || ""))
    setManualCostAmount(String(row.amount || ""))
    setManualCostMonth(String(row.month || month))
    setManualCostNote(String(row.note || ""))
  }

  async function saveManualCost() {
    const amount = Number(manualCostAmount || 0)
    if (!Number.isFinite(amount) || amount < 0) {
      onResult({ ok: false, title: "Manual Cost", message: "Enter a valid monthly cost amount." })
      return
    }
    setBusy(true)
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/profit-loss/manual-costs", {
        method: "POST",
        body: JSON.stringify({
          id: manualCostId,
          store_id: storeId ? Number(storeId) : null,
          month: manualCostMonth,
          label: manualCostLabel,
          amount,
          note: manualCostNote,
        }),
      })
      onResult({ ok: result.ok, title: "Manual Cost Saved", message: result.message })
      resetManualCostForm()
      await load(page)
    } catch (error) {
      onResult({ ok: false, title: "Manual Cost Failed", message: String(error) })
    } finally {
      setBusy(false)
    }
  }

  async function deleteManualCost(row: Record<string, string | number | null>) {
    const confirmed = window.confirm(`Remove ${row.label || "manual cost"} from ${row.month || month}?`)
    if (!confirmed) return
    setBusy(true)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/profit-loss/manual-costs/${row.id}`, { method: "DELETE" })
      onResult({ ok: result.ok, title: "Manual Cost Removed", message: result.message })
      if (manualCostId === Number(row.id || 0)) resetManualCostForm()
      await load(page)
    } catch (error) {
      onResult({ ok: false, title: "Remove Manual Cost Failed", message: String(error) })
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (manualCostId) return
    setManualCostMonth(period === "monthly" ? month : period === "weekly" ? weekStart.slice(0, 7) : day.slice(0, 7))
  }, [period, month, day, weekStart, manualCostId])

  const summary = data?.summary || {}
  const showInitialLoading = loading && !data
  const notAmazonOrderedOrders = Number(summary.not_amazon_ordered_orders || 0)
  const notAmazonOrderedLines = Number(summary.not_amazon_ordered_lines || 0)
  return (
    <div className="grid gap-3">
      <section className="grid gap-3 xl:grid-cols-4 2xl:grid-cols-7">
        {[
          ["Sales", formatMoney(Number(summary.odoo_order_value || 0))],
          ["Collected Payments", formatMoney(Number(summary.collected_payment_total || 0))],
          ["Delivery Collected", formatMoney(Number(summary.collected_delivery || 0))],
          ["Discounts", formatMoney(Number(summary.order_discounts || 0))],
          ["Amazon Cost", formatMoney(Number(summary.amazon_order_value || 0))],
          [
            "Not Ordered on Amazon",
            `${notAmazonOrderedOrders.toLocaleString()} order${notAmazonOrderedOrders === 1 ? "" : "s"}`,
            `${notAmazonOrderedLines.toLocaleString()} line${notAmazonOrderedLines === 1 ? "" : "s"} · ${formatMoney(Number(summary.not_amazon_ordered_collected || 0))} collected`,
          ],
          ["Shipping + Fulfilment", formatMoney(Number(summary.shipping_fee || 0) + Number(summary.fulfilment_fee || 0))],
          ["Manual Costs", formatMoney(Number(summary.manual_costs_total || 0))],
          ["Net Profit", formatMoney(Number(summary.net_profit || 0))],
        ].map(([label, value, detail]) => (
          <Card key={label}>
            <CardContent>
              <div className="text-secondary text-xs font-bold uppercase">{label}</div>
              <div className="mt-2 text-2xl font-semibold">
                {showInitialLoading ? <span className="spinner-border spinner-border-sm text-primary" role="status"></span> : value}
              </div>
              {!showInitialLoading && detail ? <div className="mt-1 text-xs text-muted-foreground">{detail}</div> : null}
            </CardContent>
          </Card>
        ))}
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Profit / Loss Controls</CardTitle>
          {loading && (
            <CardDescription className="inline-flex items-center gap-2">
              <span className="spinner-border spinner-border-sm text-primary" role="status"></span>
              Loading Profit / Loss...
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-[160px_180px_220px_1fr_auto] lg:items-end">
          <SelectField label="View" value={period} onChange={(value) => { onPeriod(value); setPage(1) }}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </SelectField>
          <div>
            <Label>{period === "daily" ? "Day" : period === "weekly" ? "Week Starting" : "Month"}</Label>
            {period === "daily" ? (
              <Input type="date" value={day} onChange={(event) => { setDay(event.target.value); setPage(1) }} />
            ) : period === "weekly" ? (
              <Input type="date" value={weekStart} onChange={(event) => { setWeekStart(event.target.value); setPage(1) }} />
            ) : (
              <Input type="month" value={month} onChange={(event) => { setMonth(event.target.value); setPage(1) }} />
            )}
          </div>
          <SelectField label="Net Profit Includes" value={profitScope} onChange={(value) => { setProfitScope(value); setPage(1) }}>
            <option value="all">All orders</option>
            <option value="ordered">Ordered on Amazon</option>
            <option value="not_ordered">Not ordered on Amazon</option>
          </SelectField>
          <div>
            <Label>Search</Label>
            <SearchBox value={query} onChange={setQuery} placeholder="Odoo order or Amazon order" />
          </div>
          <Button variant="outline" onClick={applyFilters}>Apply</Button>
        </CardContent>
        <CardContent className="grid gap-3 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <Label>Monthly shipping invoice CSV/XLSX</Label>
            <Input type="file" accept=".csv,.xlsx" onChange={(event) => setFile(event.target.files?.[0] || null)} />
          </div>
          <Button disabled={!file || busy} onClick={uploadShipping}>
            <Download className="size-4" />
            Import Charges
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Manual Monthly Costs</CardTitle>
          <CardDescription>Costs entered here are subtracted from the Profit / Loss total for their month.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-[1fr_160px_160px_1fr_auto_auto] lg:items-end">
          <div>
            <Label>Cost Name</Label>
            <Input value={manualCostLabel} onChange={(event) => setManualCostLabel(event.target.value)} placeholder="Google Ads" />
          </div>
          <div>
            <Label>Amount</Label>
            <Input type="number" min="0" step="0.01" value={manualCostAmount} onChange={(event) => setManualCostAmount(event.target.value)} placeholder="0.00" />
          </div>
          <div>
            <Label>Cost Month</Label>
            <Input type="month" value={manualCostMonth} onChange={(event) => setManualCostMonth(event.target.value)} />
          </div>
          <div>
            <Label>Note</Label>
            <Input value={manualCostNote} onChange={(event) => setManualCostNote(event.target.value)} placeholder="Campaign, invoice, or memo" />
          </div>
          <Button disabled={busy || !manualCostLabel.trim()} onClick={saveManualCost}>
            {manualCostId ? "Update Cost" : "Add Cost"}
          </Button>
          <Button variant="outline" disabled={busy} onClick={resetManualCostForm}>Clear</Button>
        </CardContent>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Month</TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Note</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.manual_costs || []).map((row) => (
                <TableRow key={String(row.id)}>
                  <TableCell>{row.month}</TableCell>
                  <TableCell>{row.label}</TableCell>
                  <TableCell>{formatMoney(Number(row.amount || 0))}</TableCell>
                  <TableCell className="max-w-[320px] truncate">{row.note || "-"}</TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => editManualCost(row)}>
                        <Edit className="size-4" />
                        Edit
                      </Button>
                      <Button size="sm" variant="destructive" disabled={busy} onClick={() => deleteManualCost(row)}>
                        <Trash2 className="size-4" />
                        Remove
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!(data?.manual_costs || []).length && (
                <TableRow>
                  <TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">
                    No manual monthly costs added for this period.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <section className="grid gap-3 xl:grid-cols-[0.75fr_1.25fr]">
        <Card>
          <CardHeader><CardTitle>{period[0].toUpperCase() + period.slice(1)} Summary</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow><TableHead>Period</TableHead><TableHead>Orders</TableHead><TableHead>Manual Costs</TableHead><TableHead>Net Profit</TableHead></TableRow></TableHeader>
              <TableBody>
                {showInitialLoading ? (
                  <TableRow>
                    <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">
                      <span className="inline-flex items-center gap-2"><span className="spinner-border spinner-border-sm text-primary" role="status"></span>Loading Profit / Loss...</span>
                    </TableCell>
                  </TableRow>
                ) : (data?.period_rows || []).map((row) => (
                  <TableRow key={String(row.period)}>
                    <TableCell>{row.period}</TableCell>
                    <TableCell>{row.orders}</TableCell>
                    <TableCell>{formatMoney(Number(row.manual_costs_total || 0))}</TableCell>
                    <TableCell>{formatMoney(Number(row.net_profit || 0))}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Recent Shipping Imports</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow><TableHead>Month</TableHead><TableHead>File</TableHead><TableHead>Rows</TableHead><TableHead>Matched</TableHead></TableRow></TableHeader>
              <TableBody>
                {(data?.imports || []).map((row) => (
                  <TableRow key={String(row.id)}>
                    <TableCell>{row.month}</TableCell>
                    <TableCell>{row.filename}</TableCell>
                    <TableCell>{row.row_count}</TableCell>
                    <TableCell>{row.matched_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader><CardTitle>Order Profit / Loss</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Odoo Order</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Sales</TableHead>
                <TableHead>Delivery</TableHead>
                <TableHead>Discounts</TableHead>
                <TableHead>Amazon Cost</TableHead>
                <TableHead>Gross</TableHead>
                <TableHead>Shipping</TableHead>
                <TableHead>Fulfilment</TableHead>
                <TableHead>Net Profit</TableHead>
                <TableHead>Margin</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {showInitialLoading ? (
                <TableRow>
                  <TableCell colSpan={11} className="py-8 text-center text-muted-foreground">
                    <span className="inline-flex items-center gap-2"><span className="spinner-border spinner-border-sm text-primary" role="status"></span>Loading Profit / Loss...</span>
                  </TableCell>
                </TableRow>
              ) : (data?.orders || []).map((row) => (
                <TableRow key={`${row.odoo_order_id}-${row.odoo_order_name}`}>
                  <TableCell><OdooOrderRef name={row.odoo_order_name} linkClassName="font-medium" /></TableCell>
                  <TableCell>{formatDateTime(row.order_date)}</TableCell>
                  <TableCell>{formatMoney(row.odoo_order_value)}</TableCell>
                  <TableCell>{formatMoney(row.collected_delivery)}</TableCell>
                  <TableCell>{formatMoney(row.order_discounts)}</TableCell>
                  <TableCell>{formatMoney(row.amazon_order_value)}</TableCell>
                  <TableCell>{formatMoney(row.gross_profit)}</TableCell>
                  <TableCell>{formatMoney(row.shipping_fee)}</TableCell>
                  <TableCell>{formatMoney(row.fulfilment_fee)}</TableCell>
                  <TableCell className={row.net_profit < 0 ? "text-destructive font-semibold" : "text-green font-semibold"}>{formatMoney(row.net_profit)}</TableCell>
                  <TableCell>{row.margin_percent}%</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
        <CardFooter>
          <PaginationControls
            page={data?.page || page}
            total={data?.total || 0}
            perPage={data?.per_page || PAGE_SIZE}
            onPage={setPage}
            disabled={busy}
          />
        </CardFooter>
      </Card>
    </div>
  )
}

function AccountingPage({ storeId, onResult }: { storeId: string; onResult: (modal: ModalState) => void }) {
  const [data, setData] = useState<AccountingData>({ documents: [], summary: [] })
  const [query, setQuery] = useState("")
  const [page, setPage] = useState(1)
  const [documentType, setDocumentType] = useState("odoo")
  const [orderName, setOrderName] = useState("")
  const [countryCode, setCountryCode] = useState("")
  const [invoiceDate, setInvoiceDate] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [syncingOdoo, setSyncingOdoo] = useState(false)

  async function load(nextPage = page) {
    const params = new URLSearchParams({ q: query })
    params.set("page", String(nextPage))
    params.set("per_page", String(PAGE_SIZE))
    const next = await api<AccountingData>(`/api/accounting?${params.toString()}`)
    setData(next)
    setPage(next.page || nextPage)
  }

  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Accounting load failed", message: String(error) }))
  }, [page])

  function applyAccountingFilter() {
    if (page === 1) {
      void load(1).catch((error) => onResult({ ok: false, title: "Accounting load failed", message: String(error) }))
      return
    }
    setPage(1)
  }

  async function uploadDocument() {
    if (!file || !orderName) return
    try {
      const form = new FormData()
      form.append("file", file)
      form.append("document_type", documentType)
      form.append("odoo_order_name", orderName)
      form.append("country_code", countryCode)
      form.append("invoice_date", invoiceDate)
      const result = await uploadWithAdminToken("/api/accounting/documents", form)
      onResult({ ok: true, title: "Invoice Stored", message: result.message || "Stored." })
      setFile(null)
      await load(page)
    } catch (error) {
      onResult({ ok: false, title: "Invoice Upload Failed", message: String(error) })
    }
  }

  async function syncOdooInvoices() {
    setSyncingOdoo(true)
    try {
      const params = new URLSearchParams({ days: "30", limit: "200" })
      if (storeId) params.set("store_id", storeId)
      const result = await api<{ ok: boolean; message: string }>(`/api/accounting/odoo-sync?${params.toString()}`, { method: "POST" })
      onResult({ ok: true, title: "Odoo Invoice Sync", message: result.message })
      await load(page)
    } catch (error) {
      onResult({ ok: false, title: "Odoo Invoice Sync Failed", message: String(error) })
    } finally {
      setSyncingOdoo(false)
    }
  }

  return (
    <div className="grid gap-3">
      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {data.summary.map((row) => (
          <Card key={`${row.tax_region}-${row.document_type}`}>
            <CardContent>
              <div className="text-secondary text-xs font-bold uppercase">{row.tax_region} / {row.document_type}</div>
              <div className="mt-2 text-2xl font-semibold">{row.document_count}</div>
              <div className="text-secondary text-sm">{Math.round(Number(row.total_bytes || 0) / 1024).toLocaleString()} KB stored</div>
            </CardContent>
          </Card>
        ))}
      </section>
      <Card>
        <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Store Invoice</CardTitle>
            <CardDescription>Odoo invoices sync directly into R2; Amazon invoices are uploaded by the Amazon Invoice extension.</CardDescription>
          </div>
          <Button variant="outline" disabled={syncingOdoo} onClick={syncOdooInvoices}>
            <RefreshCw className="size-4" />
            Sync Odoo Invoices
          </Button>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-[140px_160px_120px_160px_1fr_auto] lg:items-end">
          <SelectField label="Type" value={documentType} onChange={setDocumentType}>
            <option value="odoo">Odoo invoice</option>
            <option value="amazon">Amazon invoice</option>
          </SelectField>
          <div><Label>Odoo order #</Label><Input value={orderName} onChange={(event) => setOrderName(event.target.value)} /></div>
          <div><Label>Country</Label><Input value={countryCode} onChange={(event) => setCountryCode(event.target.value.toUpperCase())} placeholder="IN, US..." /></div>
          <div><Label>Invoice date</Label><Input type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} /></div>
          <div><Label>File</Label><Input type="file" onChange={(event) => setFile(event.target.files?.[0] || null)} /></div>
          <Button disabled={!file || !orderName} onClick={uploadDocument}>Upload</Button>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Accounting Documents</CardTitle>
          <div className="flex gap-2">
            <SearchBox value={query} onChange={setQuery} placeholder="Filter order or file" />
            <Button variant="outline" onClick={applyAccountingFilter}>Filter</Button>
          </div>
        </CardHeader>
        <div className="border-t px-6 py-3">
          <PaginationControls page={data.page || page} total={data.total || 0} perPage={data.per_page || PAGE_SIZE} onPage={setPage} />
        </div>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow><TableHead>Order</TableHead><TableHead>Type</TableHead><TableHead>Tax Region</TableHead><TableHead>Country</TableHead><TableHead>File</TableHead><TableHead>Stored</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {data.documents.map((doc) => (
                <TableRow key={doc.id}>
                  <TableCell><OdooOrderRef name={doc.odoo_order_name} linkClassName="font-medium" /></TableCell>
                  <TableCell><StatusBadge value={doc.document_type} /></TableCell>
                  <TableCell><Badge variant={doc.tax_region === "india" ? "destructive" : "secondary"}>{doc.tax_region === "india" ? "India GST" : "International"}</Badge></TableCell>
                  <TableCell>{doc.country_code}</TableCell>
                  <TableCell>{doc.storage_url ? <a href={doc.storage_url} target="_blank">{doc.stored_filename}</a> : doc.stored_filename}</TableCell>
                  <TableCell>{formatDateTime(doc.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
        <CardFooter>
          <PaginationControls page={data.page || page} total={data.total || 0} perPage={data.per_page || PAGE_SIZE} onPage={setPage} />
        </CardFooter>
      </Card>
    </div>
  )
}

function DownloadsPage({ onResult }: { onResult: (modal: ModalState) => void }) {
  const [jobs, setJobs] = useState<ExportJob[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  async function refresh() {
    try {
      const result = await api<{ jobs: ExportJob[]; total: number }>(`/api/exports?page=${page}&per_page=${PAGE_SIZE}`)
      setJobs(result.jobs)
      setTotal(result.total || 0)
    } catch (error) {
      onResult({ ok: false, title: "Downloads Load Failed", message: String(error) })
    }
  }
  async function cancel(jobId: string) {
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/exports/${jobId}/cancel`, { method: "POST" })
      onResult({ ok: result.ok, title: "Export Job", message: result.message })
      await refresh()
    } catch (error) {
      onResult({ ok: false, title: "Cancel Failed", message: String(error) })
    }
  }
  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 4000)
    return () => window.clearInterval(timer)
  }, [page])
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Downloads</CardTitle>
          <CardDescription>CSV exports run in the background and split into 5,000-row parts. Downloaded files are marked for cleanup after 7 days.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={refresh}><RefreshCw className="size-4" />Refresh</Button>
          <PaginationControls page={page} total={total} onPage={setPage} />
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Export</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Files</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell>
                  <div className="font-medium">{job.view.replace("_", " ")}</div>
                  <div className="font-mono text-xs text-muted-foreground">{job.id}</div>
                  {job.error ? <div className="max-w-[420px] text-xs"><ErrorTooltip value={job.error} /></div> : null}
                </TableCell>
                <TableCell><StatusBadge value={job.status} /></TableCell>
                <TableCell>{Number(job.processed_records || 0).toLocaleString()} / {Number(job.total_records || 0).toLocaleString()}</TableCell>
                <TableCell>
                  <div className="grid gap-1">
                    {(job.files || []).map((file) => (
                      <a key={file.id} className="text-primary underline-offset-4 hover:underline" href={adminDownloadHref(`/api/exports/files/${file.id}/download`)} target="_blank">
                        Part {file.part_number} - {file.row_count.toLocaleString()} rows
                      </a>
                    ))}
                    {!job.files?.length ? <span className="text-muted-foreground">No files yet</span> : null}
                  </div>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.created_at)}</TableCell>
                <TableCell className="text-right">
                  {["pending", "running", "cancelling"].includes(job.status) ? (
                    <Button variant="destructive" size="sm" onClick={() => cancel(job.id)}>Stop</Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
            {!jobs.length && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">No export jobs yet.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function PullJobsPage({ onResult }: { onResult: (modal: ModalState) => void }) {
  const [jobs, setJobs] = useState<PullJob[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [stoppingIds, setStoppingIds] = useState<string[]>([])
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const [clearing, setClearing] = useState(false)
  async function refresh() {
    try {
      const result = await api<{ jobs: PullJob[]; total: number }>(`/api/pull/jobs?page=${page}&per_page=${PAGE_SIZE}`)
      setJobs(result.jobs || [])
      setTotal(result.total || 0)
    } catch (error) {
      onResult({ ok: false, title: "Pull Jobs Load Failed", message: String(error) })
    }
  }
  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => window.clearInterval(timer)
  }, [page])
  function progressPercent(job: PullJob) {
    const totalOrders = Number(job.total_orders || 0)
    const processedOrders = Number(job.processed_orders || 0)
    if (!totalOrders) return job.status === "completed" ? 100 : 0
    return Math.max(0, Math.min(100, (processedOrders / totalOrders) * 100))
  }
  async function stopJob(job: PullJob) {
    setStoppingIds((ids) => Array.from(new Set([...ids, job.id])))
    try {
      const result = await api<{ message: string; job: PullJob }>(`/api/pull/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" })
      setJobs((current) => current.map((item) => (item.id === job.id ? result.job : item)))
      onResult({ ok: true, title: "Pull Job Stopped", message: result.message || "Pull job stopped." })
      await refresh()
    } catch (error) {
      onResult({ ok: false, title: "Stop Pull Job Failed", message: String(error) })
    } finally {
      setStoppingIds((ids) => ids.filter((id) => id !== job.id))
    }
  }

  async function clearAllJobs() {
    setClearing(true)
    try {
      const result = await api<{ ok: boolean; message: string; jobs: PullJob[]; total: number }>("/api/pull/jobs/clear", { method: "POST" })
      setJobs(result.jobs || [])
      setTotal(result.total || 0)
      setPage(1)
      setClearConfirmOpen(false)
      onResult({ ok: true, title: "Pull Jobs Cleared", message: result.message || "All pull jobs cleared." })
    } catch (error) {
      onResult({ ok: false, title: "Clear Pull Jobs Failed", message: String(error) })
    } finally {
      setClearing(false)
    }
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Order Pull Jobs</CardTitle>
            <CardDescription>Odoo order pulls run in the background per store, so large imports do not lock the app.</CardDescription>
          </div>
          <div className="btn-list">
            <Button variant="outline" onClick={refresh}><RefreshCw className="size-4" />Refresh</Button>
            <Button variant="destructive" disabled={!total || clearing} onClick={() => setClearConfirmOpen(true)}>
              <Trash2 className="size-4" />
              Clear All
            </Button>
            <PaginationControls page={page} total={total} onPage={setPage} />
          </div>
        </CardHeader>
        <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Store</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Window</TableHead>
              <TableHead>Progress</TableHead>
              <TableHead>Inserted</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead>Error</TableHead>
              <TableHead className="text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.map((job) => (
              <TableRow key={job.id}>
                <TableCell>
                  <div className="font-medium">{job.store_name || `Store ${job.store_id}`}</div>
                  <div className="font-mono text-xs text-muted-foreground">{job.id}</div>
                </TableCell>
                <TableCell><StatusBadge value={job.status} /></TableCell>
                <TableCell>
                  <div>{Number(job.days || 0).toLocaleString()} day(s), limit {Number(job.limit_value || 0) ? Number(job.limit_value || 0).toLocaleString() : "all"}</div>
                  <div className="text-xs text-muted-foreground">Batch {Number(job.batch_size || 50).toLocaleString()}</div>
                </TableCell>
                <TableCell className="min-w-[260px]">
                  <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
                    <span className="font-medium text-foreground">
                      Pulled {Number(job.processed_orders || 0).toLocaleString()} of {Number(job.total_orders || 0).toLocaleString()}
                    </span>
                    <span className="text-muted-foreground">{progressPercent(job).toFixed(0)}%</span>
                  </div>
                  <div className="progress">
                    <div
                      className={`progress-bar bg-primary transition-all ${job.status === "running" ? "progress-bar-striped progress-bar-animated" : ""}`}
                      role="progressbar"
                      aria-label={`Pulled ${Number(job.processed_orders || 0).toLocaleString()} of ${Number(job.total_orders || 0).toLocaleString()} Odoo orders`}
                      aria-valuenow={progressPercent(job)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      style={{ width: `${progressPercent(job)}%` }}
                    />
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    Total found: {Number(job.total_orders || 0).toLocaleString()} Odoo order{Number(job.total_orders || 0) === 1 ? "" : "s"}
                  </div>
                </TableCell>
                <TableCell>{Number(job.inserted_records || 0).toLocaleString()}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.updated_at || job.created_at)}</TableCell>
                <TableCell className="max-w-[420px] text-xs"><ErrorTooltip value={job.error} /></TableCell>
                <TableCell className="text-right">
                  {["queued", "running"].includes(String(job.status || "").toLowerCase()) ? (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => stopJob(job)}
                      disabled={stoppingIds.includes(job.id)}
                    >
                      <AlertCircle className="size-4" />
                      {stoppingIds.includes(job.id) ? "Stopping" : "Stop"}
                    </Button>
                  ) : (
                    <span className="text-xs text-muted-foreground">-</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!jobs.length && (
              <TableRow>
                <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">No pull jobs yet.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        </CardContent>
      </Card>
      <Dialog open={clearConfirmOpen} onOpenChange={(open) => !open && setClearConfirmOpen(false)}>
        <DialogContent className="border-destructive/50 sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-destructive">
              <AlertCircle className="size-5" />
              Clear all pull jobs?
            </DialogTitle>
            <DialogDescription>
              This will clear every pull job record and stop queued or running pull jobs at their next progress check.
            </DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            This clears all pull job history and kills in-progress pulls. Continue only if you are sure.
          </div>
          <DialogFooter>
            <Button variant="outline" disabled={clearing} onClick={() => setClearConfirmOpen(false)}>Cancel</Button>
            <Button variant="destructive" disabled={clearing} onClick={clearAllJobs}>
              {clearing ? "Clearing" : "Clear all jobs"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function ReplacementDialog({
  line,
  storeId,
  onClose,
  onSaved,
  onResult,
}: {
  line: OrderLine
  storeId: number
  onClose: () => void
  onSaved: (message: string) => Promise<void>
  onResult: (modal: ModalState) => void
}) {
  const [asin, setAsin] = useState("")
  const [note, setNote] = useState("")
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  useEffect(() => {
    setAsin(line.replacement_asin || "")
    setNote(line.replacement_note || "")
  }, [line])
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Assign Replacement ASIN</DialogTitle>
          <DialogDescription>{line.odoo_order_name} / current {line.asin}</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3">
          <TextField label="Replacement ASIN" value={asin} onChange={(value) => setAsin(value.toUpperCase())} />
          <TextField label="Internal note" value={note} onChange={setNote} />
        </div>
        <DialogFooter className="gap-2 sm:justify-between">
          <Button
            variant="outline"
            disabled={!line.replacement_asin || saving || resetting}
            onClick={async () => {
              try {
                setResetting(true)
                const result = await api<{ ok: boolean; message: string }>(`/api/lines/${line.id}/replacement/reset`, {
                  method: "POST",
                  body: JSON.stringify({ store_id: storeId }),
                })
                await onSaved(result.message)
              } catch (error) {
                onResult({ ok: false, title: "Replacement Reset Failed", message: String(error) })
              } finally {
                setResetting(false)
              }
            }}
          >
            <RefreshCw className="size-4" />
            {resetting ? "Resetting" : "Reset Replacement"}
          </Button>
          <div className="flex gap-2">
          <Button variant="outline" disabled={saving || resetting} onClick={onClose}>Cancel</Button>
          <Button
            disabled={saving || resetting}
            onClick={async () => {
              try {
                setSaving(true)
                const result = await api<{ ok: boolean; message: string }>(`/api/lines/${line.id}/replacement`, {
                  method: "POST",
                  body: JSON.stringify({ store_id: storeId, asin, note }),
                })
                await onSaved(result.message)
              } catch (error) {
                onResult({ ok: false, title: "Replacement Save Failed", message: String(error) })
              } finally {
                setSaving(false)
              }
            }}
          >
            {saving ? "Saving" : "Save Replacement"}
          </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StoreDialog({ open, value, id, onClose, onSaved, onResult }: { open: boolean; value: Omit<Store, "id">; id?: number; onClose: () => void; onSaved: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [form, setForm] = useState(value)
  useEffect(() => {
    if (open) setForm(value)
  }, [open, value])
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{id ? "Edit Store" : "Add Store"}</DialogTitle>
          <DialogDescription>Credentials stay in the local app database on this machine.</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3 md:grid-cols-2">
          <TextField label="Name" value={form.name} onChange={(name) => setForm({ ...form, name })} />
          <TextField label="Odoo URL" value={form.odoo_url} onChange={(odoo_url) => setForm({ ...form, odoo_url })} />
          <TextField label="Database" value={form.odoo_db} onChange={(odoo_db) => setForm({ ...form, odoo_db })} />
          <TextField label="User" value={form.odoo_user} onChange={(odoo_user) => setForm({ ...form, odoo_user })} />
          <TextField label="Password" type="password" value={form.odoo_password} onChange={(odoo_password) => setForm({ ...form, odoo_password })} />
          <TextField label="Website ID" value={String(form.website_id || "")} onChange={(website_id) => setForm({ ...form, website_id })} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                await api(id ? `/api/stores/${id}` : "/api/stores", { method: id ? "PUT" : "POST", body: JSON.stringify(form) })
                await onSaved()
                onClose()
                onResult({ ok: true, title: "Store Saved", message: `${form.name} was saved.` })
              } catch (error) {
                onResult({ ok: false, title: "Store Save Failed", message: String(error) })
              }
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddressesPage({ addresses, onChanged, onResult }: { addresses: Address[]; onChanged: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [editing, setEditing] = useState<Address | null>(null)
  const [creating, setCreating] = useState(false)
  return (
    <SettingsTable<Address>
      title="Fulfilment Addresses"
      description="Amazon orders use the selected default fulfilment address, with recipient name based on Odoo order number."
      rows={addresses}
      columns={["Label", "Company", "City", "Postcode", "Country", "Default"]}
      renderRow={(address) => [address.label, address.company_name, address.city, address.postal_code, address.country_code, address.is_default ? "Yes" : "No"]}
      onAdd={() => setCreating(true)}
      onEdit={setEditing}
      onDelete={async (address) => {
        try {
          await api(`/api/addresses/${address.id}`, { method: "DELETE" })
          await onChanged()
          onResult({ ok: true, title: "Address Deleted", message: `${address.label} was removed.` })
        } catch (error) {
          onResult({ ok: false, title: "Address Delete Failed", message: String(error) })
        }
      }}
      onTest={(address) => onResult({ ok: true, title: "Address", message: `${address.label} is available for Amazon order payloads.` })}
    >
      <AddressDialog open={creating} value={emptyAddress} onClose={() => setCreating(false)} onSaved={onChanged} onResult={onResult} />
      {editing && <AddressDialog open value={editing} id={editing.id} onClose={() => setEditing(null)} onSaved={onChanged} onResult={onResult} />}
    </SettingsTable>
  )
}

function SpaidDialog({
  line,
  storeId,
  onClose,
  onSaved,
  onResult,
}: {
  line: OrderLine
  storeId: number
  onClose: () => void
  onSaved: (data: DashboardData) => void
  onResult: (modal: ModalState) => void
}) {
  const [value, setValue] = useState(line.supplier_part_auxiliary_id || "")
  useEffect(() => setValue(line.supplier_part_auxiliary_id || ""), [line])
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>SupplierPartAuxiliaryID</DialogTitle>
          <DialogDescription>{line.odoo_order_name} / {line.asin}</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset">
          <TextField label="SPAID from Amazon Punchout cart" value={value} onChange={setValue} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                const next = await api<DashboardData>(`/api/lines/${line.id}/spaid`, {
                  method: "PUT",
                  body: JSON.stringify({ store_id: storeId, supplier_part_auxiliary_id: value }),
                })
                onSaved(next)
              } catch (error) {
                onResult({ ok: false, title: "SupplierPartAuxiliaryID Save Failed", message: String(error) })
              }
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddressDialog({ open, value, id, onClose, onSaved, onResult }: { open: boolean; value: Omit<Address, "id">; id?: number; onClose: () => void; onSaved: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [form, setForm] = useState(value)
  useEffect(() => {
    if (open) setForm(value)
  }, [open, value])
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{id ? "Edit Address" : "Add Address"}</DialogTitle>
          <DialogDescription>This is the warehouse or fulfilment address sent to Amazon.</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3 md:grid-cols-3">
          <TextField label="Label" value={form.label} onChange={(label) => setForm({ ...form, label })} />
          <TextField label="Company" value={form.company_name} onChange={(company_name) => setForm({ ...form, company_name })} />
          <TextField label="Phone" value={form.phone_number} onChange={(phone_number) => setForm({ ...form, phone_number })} />
          <TextField label="Address 1" value={form.address_line1} onChange={(address_line1) => setForm({ ...form, address_line1 })} />
          <TextField label="Address 2" value={form.address_line2} onChange={(address_line2) => setForm({ ...form, address_line2 })} />
          <TextField label="Address 3" value={form.address_line3} onChange={(address_line3) => setForm({ ...form, address_line3 })} />
          <TextField label="City" value={form.city} onChange={(city) => setForm({ ...form, city })} />
          <TextField label="State or Region" value={form.state_or_region} onChange={(state_or_region) => setForm({ ...form, state_or_region })} />
          <TextField label="Postal Code" value={form.postal_code} onChange={(postal_code) => setForm({ ...form, postal_code })} />
          <TextField label="Country Code" value={form.country_code} onChange={(country_code) => setForm({ ...form, country_code })} />
          <label className="mt-6 flex items-center gap-2 text-sm">
            <Checkbox checked={Boolean(form.is_default)} onCheckedChange={(checked) => setForm({ ...form, is_default: checked ? 1 : 0 })} />
            Default address
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                await api(id ? `/api/addresses/${id}` : "/api/addresses", { method: id ? "PUT" : "POST", body: JSON.stringify({ ...form, is_default: Boolean(form.is_default) }) })
                await onSaved()
                onClose()
                onResult({ ok: true, title: "Address Saved", message: `${form.label} was saved.` })
              } catch (error) {
                onResult({ ok: false, title: "Address Save Failed", message: String(error) })
              }
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AmazonPage({ accounts, onChanged, onResult }: { accounts: AmazonAccount[]; onChanged: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [editing, setEditing] = useState<AmazonAccount | null>(null)
  const [creating, setCreating] = useState(false)
  return (
    <SettingsTable<AmazonAccount>
      title="Amazon Business Accounts"
      description="Add sandbox or production Amazon Business API profiles and test LWA connectivity."
      rows={accounts}
      columns={["Name", "Endpoint", "Buyer Email", "cXML", "Default"]}
      renderRow={(account) => [
        account.name,
        <div className="grid gap-1">
          <Badge variant={account.api_base_url.includes("sandbox.") ? "outline" : "secondary"} className="w-fit">
            {account.api_base_url.includes("sandbox.") ? "Sandbox" : "Production"}
          </Badge>
          <span className="max-w-[340px] truncate font-mono text-xs text-muted-foreground">{account.api_base_url}</span>
        </div>,
        account.buyer_email || "Missing",
        account.cxml_po_url ? "Configured" : "Not set",
        account.is_default ? "Yes" : "No",
      ]}
      onAdd={() => setCreating(true)}
      onEdit={setEditing}
      onDelete={async (account) => {
        try {
          await api(`/api/amazon-accounts/${account.id}`, { method: "DELETE" })
          await onChanged()
          onResult({ ok: true, title: "Amazon Account Deleted", message: `${account.name} was removed.` })
        } catch (error) {
          onResult({ ok: false, title: "Amazon Account Delete Failed", message: String(error) })
        }
      }}
      onTest={async (account) => onResult({ title: "Test Amazon Account", ...(await api<{ ok: boolean; message: string }>(`/api/amazon-accounts/${account.id}/test`, { method: "POST" })) })}
    >
      <AmazonDialog open={creating} value={emptyAccount} onClose={() => setCreating(false)} onSaved={onChanged} onResult={onResult} />
      {editing && <AmazonDialog open value={editing} id={editing.id} onClose={() => setEditing(null)} onSaved={onChanged} onResult={onResult} />}
    </SettingsTable>
  )
}

function ChromeQueuePage({
  rows,
  counts,
  storeId,
  onResult,
  onRefresh,
}: {
  rows: ChromeQueueJob[]
  counts: ChromeQueueCount[]
  storeId: string
  onResult: (modal: ModalState) => void
  onRefresh: () => Promise<void>
}) {
  const [loading, setLoading] = useState(false)
  const lockedRows = rows.filter((row) => row.claimed_by)
  const lockedCount = lockedRows.length
  const submittedCount = counts.find((item) => item.state === "submitted")?.count || rows.length

  async function releaseLock(groupKey: string) {
    const confirmed = window.confirm("Break this Chrome job lock only if no extension is currently working on it. Continue?")
    if (!confirmed) return
    setLoading(true)
    try {
      const query = new URLSearchParams()
      if (storeId) query.set("store_id", storeId)
      const result = await api<{ ok: boolean; message: string; released: number }>(
        `/api/chrome/jobs/${encodeURIComponent(groupKey)}/force-release${query.toString() ? `?${query.toString()}` : ""}`,
        { method: "POST" },
      )
      onResult({ ok: result.ok, title: "Chrome Lock Released", message: result.message })
      await onRefresh()
    } catch (error) {
      onResult({ ok: false, title: "Chrome Lock Release Failed", message: String(error) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Chrome Jobs Queue</CardTitle>
          <CardDescription>Chrome extension jobs waiting for fulfilment or manual lock release.</CardDescription>
        </div>
        <div className="btn-list">
          <Badge variant={lockedCount ? "destructive" : "outline"}>{lockedCount.toLocaleString()} locked</Badge>
          <Badge variant="secondary">{Number(submittedCount || 0).toLocaleString()} submitted</Badge>
          <Button variant="outline" onClick={onRefresh} disabled={loading}>
            <RefreshCw className="size-4" />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Group</TableHead>
              <TableHead>Odoo Orders</TableHead>
              <TableHead>Recipient</TableHead>
              <TableHead>Items</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Expires</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((job) => (
              <TableRow key={job.group_key} className={job.back_in_stock ? "bg-emerald-50" : ""}>
                <TableCell className="font-mono text-xs">
                  <div className="grid gap-1">
                    <span>{job.group_key}</span>
                    {job.back_in_stock ? <Badge variant="secondary">Back in stock</Badge> : null}
                  </div>
                </TableCell>
                <TableCell className="max-w-[220px]">
                  <OdooOrderRefs names={job.order_names || []} />
                </TableCell>
                <TableCell>{job.recipient_name || "-"}</TableCell>
                <TableCell className="max-w-[320px]">
                  <div className="space-y-1">
                    {(job.items || []).map((item, index) => (
                      <div key={`${job.group_key}-${item.asin}-${index}`} className="text-sm">
                        <span className="font-mono">{item.asin}</span>
                        <span className="text-muted-foreground"> x {item.quantity}</span>
                        {item.product_name ? <div className="truncate text-xs text-muted-foreground">{item.product_name}</div> : null}
                      </div>
                    ))}
                  </div>
                </TableCell>
                <TableCell>
                  {job.claimed_by ? (
                    <div className="space-y-1">
                      <Badge variant="destructive">Locked</Badge>
                      <div className="max-w-[220px] truncate text-xs text-muted-foreground">{job.claimed_by}</div>
                    </div>
                  ) : (
                    <Badge variant="outline">Ready</Badge>
                  )}
                </TableCell>
                <TableCell>{job.claim_expires_at ? formatDateTime(job.claim_expires_at) : "-"}</TableCell>
                <TableCell className="text-right">
                  {job.claimed_by ? (
                    <Button variant="destructive" size="sm" disabled={loading} onClick={() => releaseLock(job.group_key)}>
                      Break lock
                    </Button>
                  ) : (
                    <span className="text-sm text-muted-foreground">No lock</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
            {!rows.length && (
              <TableRow>
                <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                  No Chrome jobs waiting.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function ShopifyFulfilmentPage({ storeId, onResult }: { storeId: string; onResult: (modal: ModalState) => void }) {
  const [jobs, setJobs] = useState<ShopifyFulfilmentJob[]>([])
  const [oauthMissing, setOauthMissing] = useState<ShopifyOAuthMissing[]>([])
  const [oauthStatus, setOauthStatus] = useState<ShopifyOAuthMissing[]>([])
  const [progress, setProgress] = useState<ShopifyFulfilmentProgress | null>(null)
  const [duplicateProgress, setDuplicateProgress] = useState<ShopifyDuplicateProgress | null>(null)
  const [duplicateGroups, setDuplicateGroups] = useState<ShopifyDuplicateGroup[]>([])
  const [duplicateFilter, setDuplicateFilter] = useState("duplicates")
  const [productRepairProgress, setProductRepairProgress] = useState<ShopifyProductRepairProgress | null>(null)
  const [productRepairLogs, setProductRepairLogs] = useState<ShopifyProductRepairLog[]>([])
  const [productRenameEnabled, setProductRenameEnabled] = useState("true")
  const [genericProductName, setGenericProductName] = useState("")
  const [productTitleLoaded, setProductTitleLoaded] = useState(false)
  const [savingProductTitle, setSavingProductTitle] = useState(false)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [jobStatusCounts, setJobStatusCounts] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState("")
  const pageRef = useRef(page)
  const progressTotal = Math.max(0, Number(progress?.total || 0))
  const progressProcessed = Math.max(0, Number(progress?.processed || 0))
  const progressPercent = progressTotal ? Math.max(0, Math.min(100, Math.round((progressProcessed / progressTotal) * 100))) : 0
  const progressRunning = progress?.status === "running" || progress?.status === "queued"
  const showProgress = Boolean(progress && (progressRunning || progressTotal > 0 || progress.message))
  const duplicateTotal = Math.max(0, Number(duplicateProgress?.total || 0))
  const duplicateProcessed = Math.max(0, Number(duplicateProgress?.processed || 0))
  const duplicatePercent = duplicateTotal ? Math.max(0, Math.min(100, Math.round((duplicateProcessed / duplicateTotal) * 100))) : 0
  const duplicateBusy = duplicateProgress?.status === "running" || duplicateProgress?.status === "cancelling"
  const productRepairTotal = Math.max(0, Number(productRepairProgress?.total || 0))
  const productRepairProcessed = Math.max(0, Number(productRepairProgress?.processed || 0))
  const productRepairPercent = productRepairTotal ? Math.max(0, Math.min(100, Math.round((productRepairProcessed / productRepairTotal) * 100))) : 0
  const productRepairBusy = productRepairProgress?.status === "running"
  const showProductRepairProgress = Boolean(productRepairProgress && (productRepairBusy || productRepairTotal > 0 || productRepairProgress.message))
  const completedJobCount = Number(jobStatusCounts.completed || 0)
  const filteredDuplicateGroups = duplicateGroups.filter((group) => {
    if (duplicateFilter === "duplicates") return Number(group.duplicate_count || 0) > 0
    if (duplicateFilter === "errors") return Boolean(group.error || group.orders.some((order) => order.cancel_error))
    if (duplicateFilter === "cancelled") return group.orders.some((order) => order.cancel_status === "cancelled")
    return true
  })

  useEffect(() => {
    pageRef.current = page
  }, [page])

  async function load(nextPage = page) {
    const requestedPage = nextPage
    const result = await api<{ jobs: ShopifyFulfilmentJob[]; status_counts?: Record<string, number>; oauth_missing?: ShopifyOAuthMissing[]; oauth_status?: ShopifyOAuthMissing[]; progress?: ShopifyFulfilmentProgress; page?: number; total: number }>(`/api/shopify/fulfilment/jobs?page=${nextPage}&per_page=${PAGE_SIZE}`)
    const resultPage = result.page || requestedPage
    if (requestedPage !== pageRef.current && resultPage !== pageRef.current) return
    setJobs(result.jobs || [])
    setJobStatusCounts(result.status_counts || {})
    setOauthMissing(result.oauth_missing || [])
    setOauthStatus(result.oauth_status || [])
    setProgress(result.progress || null)
    pageRef.current = resultPage
    setPage(resultPage)
    setTotal(result.total || 0)
  }
  async function loadDuplicates() {
    const result = await api<{ progress?: ShopifyDuplicateProgress; duplicates?: ShopifyDuplicateGroup[] }>("/api/shopify/fulfilment/duplicates")
    setDuplicateProgress(result.progress || null)
    setDuplicateGroups(result.duplicates || [])
  }
  async function loadProductRepair() {
    const result = await api<{ progress?: ShopifyProductRepairProgress; logs?: ShopifyProductRepairLog[] }>("/api/shopify/fulfilment/products/repair")
    setProductRepairProgress(result.progress || null)
    setProductRepairLogs(result.logs || [])
  }
  async function fetchProductTitleSettings() {
    const result = await api<{ settings: ServiceSettings }>("/api/settings/services")
    setProductRenameEnabled(result.settings.shopify_product_rename_enabled || "true")
    setGenericProductName(result.settings.shopify_generic_product_name || "")
    setProductTitleLoaded(true)
    return result.settings
  }
  async function loadProductTitleSettings() {
    await fetchProductTitleSettings()
  }
  async function saveProductTitleSettings(showResult = true) {
    let title = genericProductName.trim()
    setSavingProductTitle(true)
    try {
      if (!title && !productTitleLoaded) {
        const settings = await fetchProductTitleSettings()
        title = (settings.shopify_generic_product_name || "").trim()
      }
      if (!title) {
        if (showResult) onResult({ ok: false, title: "Product Title Required", message: "Enter the Shopify product title before saving or running repair." })
        return
      }
      const result = await api<{ ok: boolean; message: string; settings: ServiceSettings }>("/api/settings/services", {
        method: "POST",
        body: JSON.stringify({
          settings: {
            shopify_product_rename_enabled: productRenameEnabled || "true",
            shopify_generic_product_name: title,
          },
        }),
      })
      setProductRenameEnabled(result.settings.shopify_product_rename_enabled || productRenameEnabled || "true")
      setGenericProductName(result.settings.shopify_generic_product_name || title)
      setProductTitleLoaded(true)
      if (showResult) onResult({ ok: result.ok, title: "Product Title Saved", message: `Shopify products will use: ${result.settings.shopify_generic_product_name || title}` })
    } finally {
      setSavingProductTitle(false)
    }
  }
  useEffect(() => {
    load(page).catch((error) => onResult({ ok: false, title: "Shopify Fulfilment", message: String(error) }))
    const timer = window.setInterval(() => load(page).catch(() => undefined), 5000)
    return () => window.clearInterval(timer)
  }, [page, storeId])
  useEffect(() => {
    loadDuplicates().catch(() => undefined)
    const timer = window.setInterval(() => loadDuplicates().catch(() => undefined), 5000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    loadProductRepair().catch(() => undefined)
    const timer = window.setInterval(() => loadProductRepair().catch(() => undefined), 5000)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    loadProductTitleSettings().catch(() => undefined)
  }, [])
  async function enqueuePending() {
    setBusy("Queue")
    setProgress({
      status: "queued",
      total: Math.max(1, total || jobs.length || 1),
      processed: 0,
      message: "Queueing Shopify fulfilment jobs.",
    })
    try {
      await saveProductTitleSettings(false)
      const path = `/api/shopify/fulfilment/enqueue${storeId ? `?store_id=${encodeURIComponent(storeId)}` : ""}`
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>(path, { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      onResult({ ok: result.ok, title: "Shopify Fulfilment", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function enqueueOneTestOrder() {
    setBusy("QueueOne")
    setProgress({
      status: "queued",
      total: 1,
      processed: 0,
      message: "Queueing one Shopify test order.",
    })
    try {
      await saveProductTitleSettings(false)
      const path = `/api/shopify/fulfilment/enqueue?limit=1${storeId ? `&store_id=${encodeURIComponent(storeId)}` : ""}`
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>(path, { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      onResult({ ok: result.ok, title: "Shopify Test Order", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function runWorker() {
    setBusy("Run")
    setProgress({
      status: "running",
      total: Math.max(1, total || jobs.length || 1),
      processed: 0,
      message: "Starting Shopify fulfilment worker.",
    })
    try {
      await saveProductTitleSettings(false)
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>("/api/shopify/fulfilment/run", { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      onResult({ ok: result.ok, title: "Shopify Fulfilment", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function runOneJob() {
    setBusy("RunOne")
    setProgress({
      status: "running",
      total: 1,
      processed: 0,
      message: "Running one Shopify fulfilment job.",
    })
    try {
      await saveProductTitleSettings(false)
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>("/api/shopify/fulfilment/run-one", { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      onResult({ ok: result.ok, title: "Shopify Test Job", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function retryJob(jobId: string) {
    setBusy(`Retry-${jobId}`)
    setProgress({
      status: "running",
      total: Math.max(1, progressTotal || 1),
      processed: progressProcessed,
      message: "Starting Shopify sync for this order.",
    })
    try {
      await saveProductTitleSettings(false)
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>(`/api/shopify/fulfilment/jobs/${jobId}/retry`, { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      window.setTimeout(() => load().catch(() => undefined), 1500)
      onResult({ ok: result.ok, title: "Shopify Sync Started", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function clearCompletedJobs() {
    const confirmed = window.confirm("Clear completed Shopify fulfilment jobs from the displayed history? Queued, running, failed, and dead jobs will stay visible.")
    if (!confirmed) return
    setBusy("ClearCompleted")
    try {
      const result = await api<{ ok: boolean; message: string; jobs: ShopifyFulfilmentJob[]; status_counts?: Record<string, number>; page?: number; total: number }>(`/api/shopify/fulfilment/jobs/clear-completed?page=${pageRef.current}&per_page=${PAGE_SIZE}`, { method: "POST" })
      setJobs(result.jobs || [])
      setJobStatusCounts(result.status_counts || {})
      setTotal(result.total || 0)
      if (result.page) {
        pageRef.current = result.page
        setPage(result.page)
      }
      onResult({ ok: result.ok, title: "Shopify Jobs Cleared", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function repushJob(job: ShopifyFulfilmentJob) {
    if (!job.shopify_order_id) return
    const confirmed = window.confirm(`Only continue after Shopify order #${job.shopify_order_id} is cancelled. Repush ${job.odoo_order_name} now?`)
    if (!confirmed) return
    setBusy(`Repush-${job.id}`)
    try {
      await saveProductTitleSettings(false)
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyFulfilmentProgress }>(`/api/shopify/fulfilment/jobs/${job.id}/repush`, { method: "POST" })
      if (result.progress) setProgress(result.progress)
      await load()
      onResult({ ok: result.ok, title: "Repush Shopify Job", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function startOAuth(route: string, force = false) {
    const busyKey = `${force ? "Reauth" : "OAuth"}-${route}`
    setBusy(busyKey)
    try {
      const result = await api<{ ok: boolean; authorized?: boolean; auth_url?: string; message: string }>(`/api/shopify/fulfilment/oauth/start?route=${encodeURIComponent(route)}${force ? "&force=true" : ""}`, { method: "POST" })
      if (result.auth_url) {
        window.open(result.auth_url, "_blank", "noopener,noreferrer")
      }
      await load()
      onResult({ ok: result.ok, title: force ? "Reauthenticate Shopify" : "Shopify OAuth", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function scanDuplicates() {
    setBusy("DuplicateScan")
    try {
      const path = `/api/shopify/fulfilment/duplicates/scan${storeId ? `?store_id=${encodeURIComponent(storeId)}` : ""}`
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyDuplicateProgress; duplicates?: ShopifyDuplicateGroup[] }>(path, { method: "POST" })
      setDuplicateProgress(result.progress || null)
      setDuplicateGroups(result.duplicates || [])
      onResult({ ok: result.ok, title: "Shopify Duplicate Scan", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function cancelDuplicates() {
    setBusy("DuplicateCancel")
    try {
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyDuplicateProgress; duplicates?: ShopifyDuplicateGroup[] }>("/api/shopify/fulfilment/duplicates/cancel", { method: "POST" })
      setDuplicateProgress(result.progress || null)
      setDuplicateGroups(result.duplicates || [])
      onResult({ ok: result.ok, title: "Cancel Shopify Duplicates", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function repairSyncedProducts() {
    setBusy("ProductRepair")
    setProductRepairProgress({
      status: "running",
      total: Math.max(1, total || jobs.length || 1),
      processed: 0,
      repaired: 0,
      missing: 0,
      failed: 0,
      message: "Starting Shopify product repair for already-synced orders.",
    })
    try {
      await saveProductTitleSettings(false)
      const path = `/api/shopify/fulfilment/products/repair?limit=1000${storeId ? `&store_id=${encodeURIComponent(storeId)}` : ""}`
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyProductRepairProgress; logs?: ShopifyProductRepairLog[] }>(path, { method: "POST" })
      if (result.progress) setProductRepairProgress(result.progress)
      setProductRepairLogs(result.logs || [])
      onResult({ ok: result.ok, title: "Repair Synced Products", message: result.message })
    } finally {
      setBusy("")
    }
  }
  async function cancelProductRepair() {
    setBusy("ProductRepairCancel")
    try {
      const result = await api<{ ok: boolean; message: string; progress?: ShopifyProductRepairProgress; logs?: ShopifyProductRepairLog[] }>("/api/shopify/fulfilment/products/repair/cancel", { method: "POST" })
      if (result.progress) setProductRepairProgress(result.progress)
      setProductRepairLogs(result.logs || [])
      onResult({ ok: result.ok, title: "Cancel Product Repair", message: result.message })
    } finally {
      setBusy("")
    }
  }
  return (
    <div className="grid gap-5">
      <section className="page-section">
        <div className="page-pretitle">Fulfilment queue</div>
        <h2 className="page-title">Shopify Fulfilment</h2>
        <p className="text-sm text-muted-foreground">Amazon-ordered Odoo orders are routed to DTC for non-India countries and DTB for India, then processed one job at a time.</p>
      </section>
      <Card>
        <CardHeader>
          <CardTitle>Queue Controls</CardTitle>
          <CardDescription>Failed jobs stay visible and can be requeued. The worker claims one job at a time to avoid double exports.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="form-fieldset">
            <div className="grid gap-3 md:grid-cols-[220px_minmax(260px,1fr)_auto] md:items-end">
              <SelectField label="Product Title Mode" value={productRenameEnabled} onChange={setProductRenameEnabled}>
                <option value="true">Use typed title</option>
                <option value="false">Use original titles</option>
              </SelectField>
              <TextField label="Product Title" value={productTitleLoaded ? genericProductName : ""} onChange={(value) => { setProductTitleLoaded(true); setGenericProductName(value) }} />
              <Button variant="outline" onClick={() => saveProductTitleSettings(true)} disabled={Boolean(busy) || savingProductTitle}>
                {savingProductTitle ? "Saving..." : "Save Title"}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {productTitleLoaded
                ? "When typed-title mode is on, synced Shopify products use this title and the original Odoo product name is kept in the SKU with a 39 character limit."
                : "Loading saved Shopify product title."}
            </p>
          </div>
          <div className="btn-list">
            <Button onClick={enqueuePending} disabled={Boolean(busy)}>{busy === "Queue" ? "Queueing..." : "Queue Pending Amazon Orders"}</Button>
            <Button variant="outline" onClick={enqueueOneTestOrder} disabled={Boolean(busy)}>{busy === "QueueOne" ? "Queueing..." : "Queue 1 Test Order"}</Button>
            <Button variant="outline" onClick={runWorker} disabled={Boolean(busy)}>{busy === "Run" ? "Starting..." : "Run Worker"}</Button>
            <Button variant="outline" onClick={runOneJob} disabled={Boolean(busy)}>{busy === "RunOne" ? "Running..." : "Run One Job"}</Button>
            <Button variant="outline" onClick={repairSyncedProducts} disabled={Boolean(busy) || productRepairBusy}>{busy === "ProductRepair" || productRepairBusy ? "Repairing..." : "Repair Synced Products"}</Button>
            <Button variant="outline" onClick={clearCompletedJobs} disabled={Boolean(busy) || completedJobCount === 0}>
              {busy === "ClearCompleted" ? "Clearing..." : `Clear Completed${completedJobCount ? ` (${completedJobCount})` : ""}`}
            </Button>
            {productRepairBusy ? (
              <Button variant="destructive" onClick={cancelProductRepair} disabled={busy === "ProductRepairCancel" || Boolean(productRepairProgress?.cancel_requested)}>
                {busy === "ProductRepairCancel" || productRepairProgress?.cancel_requested ? "Cancelling..." : "Cancel Repair"}
              </Button>
            ) : null}
            {oauthMissing.map((item) => (
              <Button
                key={`${item.route}-${item.dest_name}`}
                variant="warning"
                onClick={() => startOAuth(item.route)}
                disabled={Boolean(busy)}
              >
                <Link className="size-4" />
                {busy === `OAuth-${item.route}` ? "Opening..." : `Authorize ${item.dest_name || item.route.toUpperCase()}`}
              </Button>
            ))}
            {oauthStatus.filter((item) => item.authorized).map((item) => (
              <Button
                key={`${item.route}-${item.dest_name}-connected`}
                variant="success"
                className="shopify-connected-button"
                onClick={() => startOAuth(item.route, true)}
                disabled={Boolean(busy)}
                title={`Reconnect ${item.dest_name || item.route.toUpperCase()}`}
              >
                <CheckCircle2 className="size-4" />
                {busy === `Reauth-${item.route}` ? "Opening..." : `Connected ${item.route.toUpperCase()}`}
              </Button>
            ))}
            <Button variant="outline" onClick={() => load()}>Refresh</Button>
          </div>
          {showProgress ? (
            <div className="form-fieldset">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-medium">
                  Shopify worker: {progress?.status || "idle"}
                  {progress?.current_order ? ` - ${progress.current_order}` : ""}
                  {progress?.current_route ? ` (${progress.current_route})` : ""}
                </span>
                <span className="text-muted-foreground">
                  {progressProcessed.toLocaleString()} / {progressTotal.toLocaleString()} job{progressTotal === 1 ? "" : "s"}
                </span>
              </div>
              <div className="progress">
                <div
                  className={`progress-bar bg-primary transition-all ${progressRunning ? "progress-bar-striped progress-bar-animated" : ""}`}
                  role="progressbar"
                  aria-valuenow={progressPercent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Shopify fulfilment ${progressPercent}% complete`}
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{progress?.message || "Waiting for Shopify fulfilment work."}</span>
                <span>{progressPercent}%</span>
              </div>
              {progress?.error ? <p className="text-xs text-destructive">{progress.error}</p> : null}
            </div>
          ) : null}
          {showProductRepairProgress ? (
            <div className="form-fieldset">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-medium">
                  Product repair: {productRepairProgress?.status || "idle"}
                  {productRepairProgress?.current_order ? ` - ${productRepairProgress.current_order}` : ""}
                </span>
                <span className="text-muted-foreground">
                  {productRepairProcessed.toLocaleString()} / {productRepairTotal.toLocaleString()} order{productRepairTotal === 1 ? "" : "s"}
                </span>
              </div>
              <div className="progress">
                <div
                  className={`progress-bar bg-primary transition-all ${productRepairBusy ? "progress-bar-striped progress-bar-animated" : ""}`}
                  role="progressbar"
                  aria-valuenow={productRepairPercent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Shopify product repair ${productRepairPercent}% complete`}
                  style={{ width: `${productRepairPercent}%` }}
                />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{productRepairProgress?.cancel_requested ? "Cancellation requested. Waiting for the current safe checkpoint." : productRepairProgress?.message || "No product repair has run yet."}</span>
                <span>
                  {Number(productRepairProgress?.repaired || 0).toLocaleString()} repaired · {Number(productRepairProgress?.missing || 0).toLocaleString()} missing · {Number(productRepairProgress?.failed || 0).toLocaleString()} failed
                </span>
              </div>
              {productRepairProgress?.error ? <p className="text-xs text-destructive">{productRepairProgress.error}</p> : null}
            </div>
          ) : null}
          {productRepairLogs.length ? (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Odoo Order</TableHead>
                    <TableHead>Shopify Order</TableHead>
                    <TableHead>Order Line Snapshot</TableHead>
                    <TableHead>Before Product/Variant</TableHead>
                    <TableHead>New Product Title</TableHead>
                    <TableHead>New SKU</TableHead>
                    <TableHead>Verified On Shopify</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {productRepairLogs.map((log, index) => (
                    <TableRow key={`${log.odoo_order_name}-${log.shopify_order_id}-${index}`}>
                      <TableCell><OdooOrderRef name={log.odoo_order_name} linkClassName="font-medium" /></TableCell>
                      <TableCell>
                        {log.shopify_order_url ? (
                          <a className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline" href={log.shopify_order_url} target="_blank" rel="noreferrer" title="Open Shopify order" onClick={(event) => openExternalLink(event, log.shopify_order_url)}>
                            #{log.shopify_order_id}
                            <ExternalLink className="size-3.5" />
                          </a>
                        ) : (
                          <span>{log.shopify_order_id || "Not linked"}</span>
                        )}
                        <div className="text-xs text-muted-foreground">{log.dest_name || log.shop}</div>
                      </TableCell>
                      <TableCell className="min-w-[240px]">
                        <div>{log.order_line_title || <span className="text-muted-foreground">Empty</span>}</div>
                        <div className="font-mono text-xs text-muted-foreground">{log.order_line_sku || ""}</div>
                      </TableCell>
                      <TableCell className="min-w-[240px]">
                        <div>{log.old_product_title || <span className="text-muted-foreground">Empty</span>}</div>
                        <div className="font-mono text-xs text-muted-foreground">{log.old_sku || ""}</div>
                      </TableCell>
                      <TableCell className="min-w-[220px]">{log.new_product_title || <span className="text-muted-foreground">Empty</span>}</TableCell>
                      <TableCell className="font-mono text-xs">{log.new_sku || <span className="text-muted-foreground">Empty</span>}</TableCell>
                      <TableCell className="min-w-[220px]">
                        <div>{log.verified_product_title || <span className="text-muted-foreground">Not checked</span>}</div>
                        <div className="font-mono text-xs text-muted-foreground">{log.verified_variant_sku || ""}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={log.status === "repaired" ? "secondary" : log.status === "failed" ? "destructive" : "outline"}>{log.status}</Badge>
                        {log.error ? <div className="mt-1 max-w-[260px] text-xs text-destructive">{log.error}</div> : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ) : null}
          {oauthMissing.length ? (
            <Alert variant="destructive">
              <AlertCircle className="size-4" />
              <AlertTitle>Shopify OAuth required</AlertTitle>
              <AlertDescription>{oauthMissing.map((item) => `${item.dest_name || item.route.toUpperCase()} is not connected`).join(", ")}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Duplicate Finder</CardTitle>
            <CardDescription>Scan pushed Shopify orders by Odoo order number and cancel only duplicate Shopify orders that are not fulfilled.</CardDescription>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <SelectField className="w-[220px]" label="Filter" value={duplicateFilter} onChange={setDuplicateFilter}>
              <option value="duplicates">Duplicates only</option>
              <option value="all">All scan results</option>
              <option value="cancelled">Cancelled</option>
              <option value="errors">Errors</option>
            </SelectField>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={scanDuplicates} disabled={Boolean(busy) || duplicateBusy}>
                {busy === "DuplicateScan" || duplicateProgress?.status === "running" ? "Scanning..." : "Scan Duplicates"}
              </Button>
              <Button variant="destructive" onClick={cancelDuplicates} disabled={Boolean(busy) || duplicateBusy || !duplicateGroups.some((group) => group.orders.some((order) => order.duplicate && order.cancel_status !== "cancelled"))}>
                {busy === "DuplicateCancel" || duplicateProgress?.status === "cancelling" ? "Cancelling..." : "Cancel Duplicates"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          {duplicateProgress ? (
            <div className="form-fieldset">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-medium">Duplicate scan: {duplicateProgress.status || "idle"}</span>
                <span className="text-muted-foreground">
                  {duplicateProcessed.toLocaleString()} / {duplicateTotal.toLocaleString()} scanned · {Number(duplicateProgress.duplicates_found || 0).toLocaleString()} duplicate{Number(duplicateProgress.duplicates_found || 0) === 1 ? "" : "s"} found
                </span>
              </div>
              <div className="progress">
                <div
                  className={`progress-bar bg-primary transition-all ${duplicateBusy ? "progress-bar-striped progress-bar-animated" : ""}`}
                  role="progressbar"
                  aria-valuenow={duplicatePercent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Shopify duplicate scan ${duplicatePercent}% complete`}
                  style={{ width: `${duplicatePercent}%` }}
                />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{duplicateProgress.message || "No duplicate scan has run yet."}</span>
                <span>{Number(duplicateProgress.cancelled || 0).toLocaleString()} cancelled · {Number(duplicateProgress.cancel_failed || 0).toLocaleString()} failed</span>
              </div>
              {duplicateProgress.error ? <p className="text-xs text-destructive">{duplicateProgress.error}</p> : null}
            </div>
          ) : null}
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow><TableHead>Odoo Order</TableHead><TableHead>Shopify Store</TableHead><TableHead>Duplicate Orders</TableHead><TableHead>Status</TableHead></TableRow>
              </TableHeader>
              <TableBody>
                {filteredDuplicateGroups.map((group) => (
                  <TableRow key={group.key}>
                    <TableCell
                      className={cn("font-medium", group.odoo_order_url && "cursor-pointer")}
                      role={group.odoo_order_url ? "link" : undefined}
                      tabIndex={group.odoo_order_url ? 0 : undefined}
                      onClick={(event) => openExternalCell(event, group.odoo_order_url)}
                      onKeyDown={(event) => openExternalCellKey(event, group.odoo_order_url)}
                    >
                      {group.odoo_order_url ? (
                        <OdooOrderRef name={group.odoo_order_name} url={group.odoo_order_url} linkClassName="font-medium" />
                      ) : <OdooOrderRef name={group.odoo_order_name} linkClassName="font-medium" />}
                    </TableCell>
                    <TableCell>
                      <div>{group.dest_name || group.route?.toUpperCase()}</div>
                      <div className="text-xs text-muted-foreground">{group.shop}</div>
                    </TableCell>
                    <TableCell className="min-w-[320px]">
                      <div className="grid gap-1">
                        {(group.orders || []).map((order) => (
                          <div key={`${group.key}-${order.id}`} className="flex flex-wrap items-center gap-2 text-sm">
                            {order.url ? (
                              <a className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline" href={order.url} target="_blank" rel="noreferrer" title="Open Shopify order" onClick={(event) => openExternalLink(event, order.url)}>
                                {order.name || order.id}
                                <ExternalLink className="size-3.5" />
                              </a>
                            ) : (
                              <span>{order.name || order.id}</span>
                            )}
                            <Badge variant={order.keep || order.protected || order.cancel_status === "protected_fulfilled" ? "secondary" : order.cancel_status === "cancelled" ? "outline" : "destructive"}>
                              {order.keep ? "Keep" : order.protected || order.cancel_status === "protected_fulfilled" ? "Fulfilled protected" : order.cancel_status === "cancelled" ? "Cancelled" : "Duplicate"}
                            </Badge>
                            {order.fulfillment_status ? <span className="text-xs text-muted-foreground">{order.fulfillment_status}</span> : null}
                            <span className="text-xs text-muted-foreground">{order.created_at ? formatDateTime(order.created_at) : ""}</span>
                            {order.cancel_error ? <span className="text-xs text-destructive">{order.cancel_error}</span> : null}
                          </div>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      {group.error ? <span className="text-destructive">{group.error}</span> : `${Number(group.duplicate_count || 0).toLocaleString()} duplicate${Number(group.duplicate_count || 0) === 1 ? "" : "s"}`}
                    </TableCell>
                  </TableRow>
                ))}
                {!filteredDuplicateGroups.length && (
                  <TableRow>
                    <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No duplicate scan results for this filter.</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>Jobs</CardTitle>
            <CardDescription>{total.toLocaleString()} Shopify fulfilment job(s).</CardDescription>
          </div>
          <PaginationControls page={page} total={total} onPage={setPage} disabled={Boolean(busy)} />
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow><TableHead>Odoo Order</TableHead><TableHead>Shopify Ref</TableHead><TableHead>Store</TableHead><TableHead>Route</TableHead><TableHead>Status</TableHead><TableHead>Attempts</TableHead><TableHead>Updated</TableHead><TableHead>Error</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {jobs.map((job) => (
                <TableRow key={job.id}>
                  <TableCell
                    className={cn("font-medium", job.odoo_order_url && "cursor-pointer")}
                    role={job.odoo_order_url ? "link" : undefined}
                    tabIndex={job.odoo_order_url ? 0 : undefined}
                    onClick={(event) => openExternalCell(event, job.odoo_order_url)}
                    onKeyDown={(event) => openExternalCellKey(event, job.odoo_order_url)}
                  >
                    {job.odoo_order_url ? (
                      <OdooOrderRef name={job.odoo_order_name} url={job.odoo_order_url} linkClassName="font-medium" />
                    ) : <OdooOrderRef name={job.odoo_order_name} linkClassName="font-medium" />}
                  </TableCell>
                  <TableCell
                    className={job.shopify_order_url ? "cursor-pointer" : undefined}
                    role={job.shopify_order_url ? "link" : undefined}
                    tabIndex={job.shopify_order_url ? 0 : undefined}
                    onClick={(event) => openExternalCell(event, job.shopify_order_url)}
                    onKeyDown={(event) => openExternalCellKey(event, job.shopify_order_url)}
                  >
                    {job.shopify_order_url ? (
                      <a className="inline-flex items-center gap-1 text-primary underline-offset-4 hover:underline" href={job.shopify_order_url} target="_blank" rel="noreferrer" title="Open Shopify order" onClick={(event) => openExternalLink(event, job.shopify_order_url)}>
                        #{job.shopify_order_id}
                        <ExternalLink className="size-3.5" />
                      </a>
                    ) : job.shopify_order_id ? (
                      <span className="font-mono text-xs">{job.shopify_order_id}</span>
                    ) : (
                      <span className="text-muted-foreground">Not synced</span>
                    )}
                    {job.shopify_dest_name ? <div className="text-xs text-muted-foreground">{job.shopify_dest_name}</div> : null}
                  </TableCell>
                  <TableCell>{job.store_name}</TableCell>
                  <TableCell><Badge variant="outline">{job.route?.toUpperCase()}</Badge></TableCell>
                  <TableCell><StatusBadge value={job.status} /></TableCell>
                  <TableCell>{job.attempts}/{job.max_attempts}</TableCell>
                  <TableCell>{formatDateTime(job.updated_at || job.created_at)}</TableCell>
                  <TableCell className="max-w-md"><ErrorTooltip value={job.last_error} /></TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
                      {["amazon_placed", "queued", "failed", "dead"].includes(job.status) ? (
                        <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => retryJob(job.id)}>
                          {busy === `Retry-${job.id}` ? "Syncing..." : ["amazon_placed", "queued"].includes(job.status) ? "Sync Now" : "Retry"}
                        </Button>
                      ) : null}
                      {job.shopify_order_id && ["completed", "failed", "dead"].includes(job.status) ? (
                        <Button size="sm" variant="warning" disabled={Boolean(busy)} onClick={() => repushJob(job)}>
                          {busy === `Repush-${job.id}` ? "Repushing..." : "Repush"}
                        </Button>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!jobs.length && <TableRow><TableCell colSpan={9} className="py-8 text-center text-muted-foreground">No Shopify fulfilment jobs yet.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
        <CardFooter>
          <PaginationControls page={page} total={total} onPage={setPage} disabled={Boolean(busy)} />
        </CardFooter>
      </Card>
    </div>
  )
}

function ShopifyTrackingSyncPage({ onResult }: { onResult: (modal: ModalState) => void }) {
  const [jobs, setJobs] = useState<ShopifyTrackingJob[]>([])
  const [fromDate, setFromDate] = useState("")
  const [toDate, setToDate] = useState("")
  const [dryRun, setDryRun] = useState(false)
  const [busy, setBusy] = useState(false)
  async function load() {
    const result = await api<{ jobs: ShopifyTrackingJob[] }>("/api/shopify/tracking/jobs?page=1&per_page=100")
    setJobs(result.jobs || [])
  }
  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Shopify Tracking", message: String(error) }))
    const timer = window.setInterval(() => load().catch(() => undefined), 5000)
    return () => window.clearInterval(timer)
  }, [])
  async function startSync() {
    setBusy(true)
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/shopify/tracking/jobs", {
        method: "POST",
        body: JSON.stringify({ from_date: fromDate, to_date: toDate, dry_run: dryRun }),
      })
      await load()
      onResult({ ok: result.ok, title: "Shopify Tracking", message: result.message })
    } finally {
      setBusy(false)
    }
  }
  async function retry(jobId: string) {
    const result = await api<{ ok: boolean; message: string }>(`/api/shopify/tracking/jobs/${jobId}/retry`, { method: "POST" })
    await load()
    onResult({ ok: result.ok, title: "Retry Tracking Sync", message: result.message })
  }
  return (
    <div className="grid gap-5">
      <section className="page-section">
        <div className="page-pretitle">Tracking sync</div>
        <h2 className="page-title">Shopify Tracking to Odoo</h2>
        <p className="text-sm text-muted-foreground">Runs the strict tag-based Shopify tracking script and writes tracking back to matching Odoo pickings.</p>
      </section>
      <Card>
        <CardHeader>
          <CardTitle>Start Tracking Sync</CardTitle>
          <CardDescription>Uses SRC_ODOO_DB and SRC_ODOO_ORDER tags only. Empty dates use the configured default window.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <TextField label="From Date" type="date" value={fromDate} onChange={setFromDate} />
          <TextField label="To Date" type="date" value={toDate} onChange={setToDate} />
          <label className="form-check mt-6 w-fit cursor-pointer">
            <Checkbox checked={dryRun} onCheckedChange={(checked) => setDryRun(checked)} />
            <span className="form-check-label">Dry run</span>
          </label>
          <div className="mt-6 btn-list">
            <Button onClick={startSync} disabled={busy}>{busy ? "Queueing..." : "Run Sync"}</Button>
            <Button variant="outline" onClick={load}>Refresh</Button>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Tracking Jobs</CardTitle></CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader><TableRow><TableHead>Date Range</TableHead><TableHead>Status</TableHead><TableHead>Attempts</TableHead><TableHead>Report</TableHead><TableHead>Error</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {jobs.map((job) => (
                <TableRow key={job.id}>
                  <TableCell>{job.from_date} to {job.to_date}{job.dry_run ? " (dry run)" : ""}</TableCell>
                  <TableCell><StatusBadge value={job.status} /></TableCell>
                  <TableCell>{job.attempts}</TableCell>
                  <TableCell className="max-w-sm truncate">{job.report_csv}</TableCell>
                  <TableCell className="max-w-md"><ErrorTooltip value={job.last_error} /></TableCell>
                  <TableCell>{job.status === "failed" && <Button size="sm" variant="outline" onClick={() => retry(job.id)}>Retry</Button>}</TableCell>
                </TableRow>
              ))}
              {!jobs.length && <TableRow><TableCell colSpan={6} className="py-8 text-center text-muted-foreground">No tracking sync jobs yet.</TableCell></TableRow>}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}

function formatScriptConfigValue(value: any): string {
  if (value === true) return "Yes"
  if (value === false) return "No"
  if (value === null || value === undefined || value === "") return "Not set"
  if (Array.isArray(value)) return value.map(formatScriptConfigValue).join(", ")
  if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${key.replace(/_/g, " ")}: ${formatScriptConfigValue(item)}`).join(", ")
  return String(value)
}

function scriptConfigTitle(script: string) {
  if (script === "dtb") return "DTB Export Script"
  if (script === "tracking") return "Tracking Sync Script"
  return "DTC Export Script"
}

function ScriptConfigField({ label, value }: { label: string; value: any }) {
  return (
    <div className="grid min-w-0 gap-1.5">
      <Label className="capitalize">{label.replace(/_/g, " ")}</Label>
      <Input readOnly value={formatScriptConfigValue(value)} className="bg-muted/30" />
    </div>
  )
}

function ScriptConfigObjectFields({ value }: { value: Record<string, any> }) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {Object.entries(value).map(([key, item]) => (
        <ScriptConfigField key={key} label={key} value={item} />
      ))}
    </div>
  )
}

function ScriptConfigArrayFields({ label, value }: { label: string; value: any[] }) {
  return (
    <div className="grid gap-2">
      <div className="font-semibold capitalize">{label.replace(/_/g, " ")}</div>
      <div className="grid gap-3">
        {value.map((item, index) => (
          <div key={`${label}-${index}`} className="rounded border bg-muted/10 p-3">
            <div className="mb-3 text-xs font-semibold uppercase text-muted-foreground">{label.replace(/_/g, " ")} {index + 1}</div>
            {item && typeof item === "object" && !Array.isArray(item) ? (
              <ScriptConfigObjectFields value={item} />
            ) : (
              <ScriptConfigField label={label} value={item} />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function ScriptConfigSection({ label, value }: { label: string; value: any }) {
  if (Array.isArray(value)) return <ScriptConfigArrayFields label={label} value={value} />
  if (value && typeof value === "object") {
    return (
      <div className="grid gap-2">
        <div className="font-semibold capitalize">{label.replace(/_/g, " ")}</div>
        <div className="rounded border bg-muted/10 p-3">
          <ScriptConfigObjectFields value={value} />
        </div>
      </div>
    )
  }
  return <ScriptConfigField label={label} value={value} />
}

function ScriptConfigPanel({
  activeScript,
  onActiveScript,
  config,
}: {
  activeScript: string
  onActiveScript: (value: string) => void
  config: ShopifyScriptConfig
}) {
  const selectedConfig = (config[activeScript as keyof ShopifyScriptConfig] || {}) as Record<string, any>
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Bundled Script Settings</CardTitle>
        <CardDescription>Select a script to view its current settings as regular fields.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4 text-sm">
        <div className="grid gap-3 md:grid-cols-[minmax(220px,320px)_1fr] md:items-end">
          <SelectField label="Script" value={activeScript} onChange={onActiveScript}>
            <option value="dtc">DTC Export Script</option>
            <option value="dtb">DTB Export Script</option>
            <option value="tracking">Tracking Sync Script</option>
          </SelectField>
          <div className="rounded border bg-muted/10 px-3 py-2">
            <div className="text-xs font-semibold uppercase text-muted-foreground">Current Selection</div>
            <div className="font-semibold">{scriptConfigTitle(activeScript)}</div>
          </div>
        </div>
        <div className="grid gap-4">
          {Object.entries(selectedConfig).map(([key, value]) => (
            <ScriptConfigSection key={key} label={key} value={value} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function SettingsPage({
  stores,
  addresses,
  accounts,
  urls,
  onChanged,
  onResult,
}: {
  stores: Store[]
  addresses: Address[]
  accounts: AmazonAccount[]
  urls: PunchoutReturnUrl[]
  onChanged: () => Promise<void>
  onResult: (modal: ModalState) => void
}) {
  const [editing, setEditing] = useState<PunchoutReturnUrl | null>(null)
  const [creating, setCreating] = useState(false)
  const [settings, setSettings] = useState<ServiceSettings>({})
  const [shopifyScriptConfig, setShopifyScriptConfig] = useState<ShopifyScriptConfig | null>(null)
  const [activeShopifyScript, setActiveShopifyScript] = useState("dtc")
  const [savingServices, setSavingServices] = useState("")
  const [reindexProgress, setReindexProgress] = useState<ReindexProgress | null>(null)
  const [backups, setBackups] = useState<DatabaseBackup[]>([])
  const [selectedBackupKey, setSelectedBackupKey] = useState("")
  const [backupBusy, setBackupBusy] = useState("")
  const [backupProgress, setBackupProgress] = useState<BackupProgress | null>(null)
  const [odooRpcCacheEntries, setOdooRpcCacheEntries] = useState(0)
  const [adminCode, setAdminCode] = useState("")
  const [adminCodeConfirm, setAdminCodeConfirm] = useState("")
  useEffect(() => {
    api<{ settings: ServiceSettings }>("/api/settings/services")
      .then((result) => setSettings(result.settings))
      .catch((error) => onResult({ ok: false, title: "Settings Load Failed", message: String(error) }))
    api<{ config: ShopifyScriptConfig }>("/api/settings/shopify-script-config")
      .then((result) => setShopifyScriptConfig(result.config))
      .catch(() => setShopifyScriptConfig(null))
    loadReindexProgress()
    loadBackupProgress()
    loadBackups()
    loadOdooRpcCacheStatus()
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!reindexProgress || ["running", "queued"].includes(reindexProgress.status)) {
        loadReindexProgress()
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [reindexProgress?.status])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (backupBusy === "backup" || backupProgress?.status === "running") {
        loadBackupProgress()
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [backupBusy, backupProgress?.status])

  async function loadReindexProgress() {
    try {
      const result = await api<{ ok: boolean; progress: ReindexProgress }>("/api/settings/typesense/reindex")
      setReindexProgress(result.progress)
    } catch {
      // Keep settings usable even if progress cannot be loaded.
    }
  }
  async function loadBackupProgress() {
    try {
      const result = await api<{ ok: boolean; progress: BackupProgress }>("/api/settings/backup/progress")
      setBackupProgress(result.progress)
    } catch {
      // Keep backup controls usable even if progress cannot be loaded.
    }
  }
  async function loadBackups() {
    try {
      const result = await api<{ ok: boolean; backups: DatabaseBackup[] }>("/api/settings/backup/list")
      setBackups(result.backups || [])
      setSelectedBackupKey((current) => current && result.backups?.some((backup) => backup.key === current) ? current : "")
    } catch {
      setBackups([])
    }
  }
  async function loadOdooRpcCacheStatus() {
    try {
      const result = await api<{ ok: boolean; entries: number }>("/api/settings/odoo-rpc-cache")
      setOdooRpcCacheEntries(Number(result.entries || 0))
    } catch {
      setOdooRpcCacheEntries(0)
    }
  }

  async function saveSettingsGroup(title: string, keys: string[]) {
    setSavingServices(title)
    try {
      const nextSettings = keys.reduce<ServiceSettings>((values, key) => {
        values[key] = settings[key] || ""
        return values
      }, {})
      const result = await api<{ ok: boolean; message: string; settings: ServiceSettings }>("/api/settings/services", {
        method: "POST",
        body: JSON.stringify({ settings: nextSettings }),
      })
      setSettings((current) => ({ ...current, ...result.settings }))
      onResult({ ok: result.ok, title, message: result.message })
    } finally {
      setSavingServices("")
    }
  }
  async function testService(service: string) {
    const result = await api<{ ok: boolean; message: string }>(`/api/settings/test/${service}`, { method: "POST" })
    onResult({ ok: result.ok, title: `Test ${service}`, message: result.message })
  }
  async function reindexTypesense() {
    const result = await api<{ ok: boolean; message: string; progress: ReindexProgress }>("/api/settings/typesense/reindex", { method: "POST" })
    setReindexProgress(result.progress)
    onResult({ ok: result.ok, title: "Typesense Reindex", message: result.message })
  }
  async function runBackup() {
    setBackupBusy("backup")
    setBackupProgress({
      status: "queued",
      percent: 1,
      message: "Backup request sent. Waiting for pg_dump to start.",
      started_at: "",
      updated_at: "",
      completed_at: "",
      error: "",
    })
    try {
      const result = await api<{ ok: boolean; message: string; backups: DatabaseBackup[]; backup?: DatabaseBackup; progress?: BackupProgress }>("/api/settings/backup/run", { method: "POST" })
      setBackups(result.backups || [])
      if (result.progress) setBackupProgress(result.progress)
      if (result.backup?.key) setSelectedBackupKey(result.backup.key)
      onResult({ ok: result.ok, title: "Backup", message: result.message })
    } catch (error) {
      await loadBackupProgress()
      onResult({ ok: false, title: "Backup Failed", message: String(error) })
    } finally {
      setBackupBusy("")
    }
  }
  async function restoreBackup() {
    if (!selectedBackupKey) return
    const backup = backups.find((item) => item.key === selectedBackupKey)
    const confirmed = window.confirm(`Restore the full Postgres database from ${backup?.name || selectedBackupKey}? This replaces current database tables and data.`)
    if (!confirmed) return
    setBackupBusy("restore")
    try {
      const result = await api<{ ok: boolean; message: string }>("/api/settings/backup/restore", {
        method: "POST",
        body: JSON.stringify({ key: selectedBackupKey }),
      })
      onResult({ ok: result.ok, title: "Restore Backup", message: result.message })
    } finally {
      setBackupBusy("")
    }
  }
  async function deleteBackup() {
    if (!selectedBackupKey) return
    const backup = backups.find((item) => item.key === selectedBackupKey)
    const confirmed = window.confirm(`Delete ${backup?.name || selectedBackupKey} from Cloudflare R2?`)
    if (!confirmed) return
    setBackupBusy("delete")
    try {
      const result = await api<{ ok: boolean; message: string; backups: DatabaseBackup[] }>("/api/settings/backup/delete", {
        method: "POST",
        body: JSON.stringify({ key: selectedBackupKey }),
      })
      setBackups(result.backups || [])
      setSelectedBackupKey("")
      onResult({ ok: result.ok, title: "Delete Backup", message: result.message })
    } finally {
      setBackupBusy("")
    }
  }
  async function syncAmazonOtp() {
    const result = await api<{ ok: boolean; message: string }>("/api/settings/amazon-otp/sync", { method: "POST" })
    onResult({ ok: result.ok, title: "Amazon OTP Email Sync", message: result.message })
  }
  async function clearOdooRpcCache() {
    setSavingServices("Clear Odoo RPC Cache")
    try {
      const result = await api<{ ok: boolean; message: string; entries: number }>("/api/settings/odoo-rpc-cache/clear", { method: "POST" })
      setOdooRpcCacheEntries(Number(result.entries || 0))
      onResult({ ok: result.ok, title: "Odoo RPC Cache", message: result.message })
    } finally {
      setSavingServices("")
    }
  }
  async function saveAdminCode() {
    const nextCode = adminCode.trim()
    if (nextCode.length < 4) {
      onResult({ ok: false, title: "Admin Code", message: "Admin code must be at least 4 characters." })
      return
    }
    if (nextCode !== adminCodeConfirm.trim()) {
      onResult({ ok: false, title: "Admin Code", message: "Both admin code fields must match." })
      return
    }
    const result = await api<{ ok: boolean; message: string }>("/api/settings/admin-access", {
      method: "POST",
      body: JSON.stringify({ admin_access_token: nextCode }),
    })
    window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, nextCode)
    window.dispatchEvent(new Event("admin-token-saved"))
    setAdminCode("")
    setAdminCodeConfirm("")
    onResult({ ok: result.ok, title: "Admin Code", message: `${result.message} This PC was updated to use the new code.` })
  }
  const setSetting = (key: string, value: string) => setSettings((current) => ({ ...current, [key]: value }))
  return (
    <div className="grid gap-5">
      <section className="page-section">
        <div className="page-pretitle">Configuration</div>
        <h2 className="page-title">Settings</h2>
        <p className="text-sm text-muted-foreground">Configure stores, fulfilment addresses, Amazon accounts, service connections, backups, and punchout URLs.</p>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Lock className="size-4 text-muted-foreground" />
            <CardTitle>Admin Access Code</CardTitle>
          </div>
          <CardDescription>Change the code used for the internal admin panel.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-3 md:grid-cols-2">
            <TextField label="New Admin Code" type="password" value={adminCode} onChange={setAdminCode} />
            <TextField label="Confirm Admin Code" type="password" value={adminCodeConfirm} onChange={setAdminCodeConfirm} />
          </div>
          <div className="card-actions btn-list justify-between">
            <p className="text-sm text-muted-foreground">Use a private recovery code only if the saved admin code is forgotten.</p>
            <Button onClick={saveAdminCode} disabled={!adminCode.trim() || !adminCodeConfirm.trim()}>Save Admin Code</Button>
          </div>
        </CardContent>
      </Card>

      <StoresPage
        stores={stores}
        onChanged={onChanged}
        onResult={onResult}
      />

      <AddressesPage
        addresses={addresses}
        onChanged={onChanged}
        onResult={onResult}
      />

      <AmazonPage
        accounts={accounts}
        onChanged={onChanged}
        onResult={onResult}
      />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <RefreshCw className="size-4 text-muted-foreground" />
            <CardTitle>Amazon Order History Odoo RPC</CardTitle>
          </div>
          <CardDescription>Controls the direct Odoo verification shown by the Chrome extension on Amazon order history pages.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-3 md:grid-cols-2">
            <TextField label="Parallel Odoo RPC Workers" value={settings.amazon_history_odoo_rpc_concurrency || "10"} onChange={(value) => setSetting("amazon_history_odoo_rpc_concurrency", value)} />
            <TextField label="Odoo RPC Cache Minutes" value={settings.amazon_history_odoo_rpc_cache_minutes || "60"} onChange={(value) => setSetting("amazon_history_odoo_rpc_cache_minutes", value)} />
          </div>
          <div className="card-actions btn-list justify-between">
            <p className="text-sm text-muted-foreground">Cached direct lookup results: {odooRpcCacheEntries}</p>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Odoo RPC", [
                  "amazon_history_odoo_rpc_concurrency",
                  "amazon_history_odoo_rpc_cache_minutes",
                ])}
                disabled={savingServices === "Odoo RPC"}
              >
                {savingServices === "Odoo RPC" ? "Saving..." : "Save Odoo RPC"}
              </Button>
              <Button variant="outline" onClick={loadOdooRpcCacheStatus}>Refresh Cache Count</Button>
              <Button variant="destructive" onClick={clearOdooRpcCache} disabled={savingServices === "Clear Odoo RPC Cache"}>
                {savingServices === "Clear Odoo RPC Cache" ? "Clearing..." : "Clear Odoo RPC Cache"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <StoreIcon className="size-4 text-muted-foreground" />
            <CardTitle>Shopify Fulfilment & Tracking</CardTitle>
          </div>
          <CardDescription>Integrated DTC, DTB, and tracking sync. Runtime state, OAuth tokens, and sync maps are stored in Postgres.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <Alert>
            <CheckCircle2 className="size-4" />
            <AlertTitle>Scripts are bundled inside the app</AlertTitle>
            <AlertDescription>No Downloads folder path is required for Shopify fulfilment or tracking.</AlertDescription>
          </Alert>
          <div className="grid gap-3 md:grid-cols-2">
            <TextField label="OAuth Public Base URL" value={settings.shopify_oauth_public_base_url || ""} onChange={(value) => setSetting("shopify_oauth_public_base_url", value)} />
            <TextField label="DTC Destination Name" value={settings.shopify_dtc_dest_name || ""} onChange={(value) => setSetting("shopify_dtc_dest_name", value)} />
            <TextField label="DTC Shop Domain" value={settings.shopify_dtc_shop || ""} onChange={(value) => setSetting("shopify_dtc_shop", value)} />
            <TextField label="DTC Client ID" value={settings.shopify_dtc_client_id || ""} onChange={(value) => setSetting("shopify_dtc_client_id", value)} />
            <TextField label="DTC Client Secret" type="password" value={settings.shopify_dtc_client_secret || ""} onChange={(value) => setSetting("shopify_dtc_client_secret", value)} />
            <TextField label="DTC Scopes" value={settings.shopify_dtc_scopes || ""} onChange={(value) => setSetting("shopify_dtc_scopes", value)} />
            <TextField label="DTC Redirect URI" value={settings.shopify_dtc_redirect_uri || ""} onChange={(value) => setSetting("shopify_dtc_redirect_uri", value)} />
            <TextField label="DTC API Version" value={settings.shopify_dtc_api_version || ""} onChange={(value) => setSetting("shopify_dtc_api_version", value)} />
            <SelectField label="DTC Force Reauth" value={settings.shopify_dtc_force_reauth || "false"} onChange={(value) => setSetting("shopify_dtc_force_reauth", value)}>
              <option value="false">Use saved token</option>
              <option value="true">Force OAuth every run</option>
            </SelectField>
            <TextField label="DTB Destination Name" value={settings.shopify_dtb_dest_name || ""} onChange={(value) => setSetting("shopify_dtb_dest_name", value)} />
            <TextField label="DTB Shop Domain" value={settings.shopify_dtb_shop || ""} onChange={(value) => setSetting("shopify_dtb_shop", value)} />
            <TextField label="DTB Client ID" value={settings.shopify_dtb_client_id || ""} onChange={(value) => setSetting("shopify_dtb_client_id", value)} />
            <TextField label="DTB Client Secret" type="password" value={settings.shopify_dtb_client_secret || ""} onChange={(value) => setSetting("shopify_dtb_client_secret", value)} />
            <TextField label="DTB Scopes" value={settings.shopify_dtb_scopes || ""} onChange={(value) => setSetting("shopify_dtb_scopes", value)} />
            <TextField label="DTB Redirect URI" value={settings.shopify_dtb_redirect_uri || ""} onChange={(value) => setSetting("shopify_dtb_redirect_uri", value)} />
            <TextField label="DTB API Version" value={settings.shopify_dtb_api_version || ""} onChange={(value) => setSetting("shopify_dtb_api_version", value)} />
            <SelectField label="DTB Force Reauth" value={settings.shopify_dtb_force_reauth || "false"} onChange={(value) => setSetting("shopify_dtb_force_reauth", value)}>
              <option value="false">Use saved token</option>
              <option value="true">Force OAuth every run</option>
            </SelectField>
            <TextField label="Tracking Client Secret" type="password" value={settings.shopify_tracking_client_secret || ""} onChange={(value) => setSetting("shopify_tracking_client_secret", value)} />
            <TextField label="Odoo Script Password" type="password" value={settings.odoo_script_password || ""} onChange={(value) => setSetting("odoo_script_password", value)} />
            <SelectField label="Auto Queue After Amazon Order" value={settings.shopify_auto_enqueue_enabled || "true"} onChange={(value) => setSetting("shopify_auto_enqueue_enabled", value)}>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </SelectField>
            <SelectField label="Block Old Odoo Orders" value={settings.amazon_order_date_guard_enabled || "true"} onChange={(value) => setSetting("amazon_order_date_guard_enabled", value)}>
              <option value="true">Enabled - block before 18 May</option>
              <option value="false">Disabled - allow old orders</option>
            </SelectField>
            <SelectField label="Check Fulfilled Shopify Before Amazon Queue" value={settings.shopify_fulfilled_order_guard_enabled || "false"} onChange={(value) => setSetting("shopify_fulfilled_order_guard_enabled", value)}>
              <option value="false">Disabled - faster queue</option>
              <option value="true">Enabled - block fulfilled Shopify orders</option>
            </SelectField>
            <SelectField label="Generic Product Names" value={settings.shopify_product_rename_enabled || "true"} onChange={(value) => setSetting("shopify_product_rename_enabled", value)}>
              <option value="true">Use generic product name</option>
              <option value="false">Use original product names</option>
            </SelectField>
            <TextField label="Generic Product Name" value={settings.shopify_generic_product_name || ""} onChange={(value) => setSetting("shopify_generic_product_name", value)} />
            <TextField label="Fulfilment Max Attempts" value={settings.shopify_job_max_attempts || "5"} onChange={(value) => setSetting("shopify_job_max_attempts", value)} />
            <TextField label="Fulfilment Concurrency" value={settings.shopify_fulfilment_concurrency || "3"} onChange={(value) => setSetting("shopify_fulfilment_concurrency", value)} />
            <TextField label="Shopify API Requests/sec" value={settings.shopify_admin_api_requests_per_second || "2"} onChange={(value) => setSetting("shopify_admin_api_requests_per_second", value)} />
            <TextField label="Shopify API Burst" value={settings.shopify_admin_api_burst || "35"} onChange={(value) => setSetting("shopify_admin_api_burst", value)} />
            <TextField label="Tracking Default Days" value={settings.shopify_tracking_from_days || "7"} onChange={(value) => setSetting("shopify_tracking_from_days", value)} />
            <SelectField label="Tracking Validates Deliveries" value={settings.shopify_tracking_validate_deliveries || "true"} onChange={(value) => setSetting("shopify_tracking_validate_deliveries", value)}>
              <option value="true">Validate pickings</option>
              <option value="false">Only write tracking</option>
            </SelectField>
            <SelectField label="Skip Done Pickings" value={settings.shopify_tracking_skip_done_pickings || "false"} onChange={(value) => setSetting("shopify_tracking_skip_done_pickings", value)}>
              <option value="false">Smart update done pickings</option>
              <option value="true">Always skip done pickings</option>
            </SelectField>
          </div>
          <div className="btn-list">
            <Button
              onClick={() => saveSettingsGroup("Shopify Scripts", [
                "shopify_oauth_public_base_url",
                "shopify_dtc_dest_name",
                "shopify_dtc_shop",
                "shopify_dtc_client_id",
                "shopify_dtc_client_secret",
                "shopify_dtc_scopes",
                "shopify_dtc_redirect_uri",
                "shopify_dtc_api_version",
                "shopify_dtc_force_reauth",
                "shopify_dtb_dest_name",
                "shopify_dtb_shop",
                "shopify_dtb_client_id",
                "shopify_dtb_client_secret",
                "shopify_dtb_scopes",
                "shopify_dtb_redirect_uri",
                "shopify_dtb_api_version",
                "shopify_dtb_force_reauth",
                "shopify_tracking_client_secret",
                "odoo_script_password",
                "shopify_auto_enqueue_enabled",
                "amazon_order_date_guard_enabled",
                "shopify_fulfilled_order_guard_enabled",
                "shopify_product_rename_enabled",
                "shopify_generic_product_name",
                "shopify_job_max_attempts",
                "shopify_fulfilment_concurrency",
                "shopify_admin_api_requests_per_second",
                "shopify_admin_api_burst",
                "shopify_tracking_from_days",
                "shopify_tracking_validate_deliveries",
                "shopify_tracking_skip_done_pickings",
              ])}
              disabled={savingServices === "Shopify Scripts"}
            >
              {savingServices === "Shopify Scripts" ? "Saving..." : "Save Shopify Scripts"}
            </Button>
          </div>
          {shopifyScriptConfig && (
            <ScriptConfigPanel activeScript={activeShopifyScript} onActiveScript={setActiveShopifyScript} config={shopifyScriptConfig} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Bell className="size-4 text-muted-foreground" />
            <CardTitle>Email Alerts</CardTitle>
          </div>
          <CardDescription>Send SMTP alerts when Chrome fulfilment or Shopify jobs fail.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-3 md:grid-cols-2">
            <SelectField label="Email Alerts" value={settings.email_alerts_enabled || "false"} onChange={(value) => setSetting("email_alerts_enabled", value)}>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </SelectField>
            <TextField label="Alert Recipient Email" value={settings.email_alert_to || ""} onChange={(value) => setSetting("email_alert_to", value)} />
            <TextField label="From Address" value={settings.email_from_address || ""} onChange={(value) => setSetting("email_from_address", value)} />
            <TextField label="From Name" value={settings.email_from_name || ""} onChange={(value) => setSetting("email_from_name", value)} />
            <TextField label="SMTP Host" value={settings.email_smtp_host || ""} onChange={(value) => setSetting("email_smtp_host", value)} />
            <TextField label="SMTP Port" value={settings.email_smtp_port || "465"} onChange={(value) => setSetting("email_smtp_port", value)} />
            <SelectField label="SMTP Secure" value={settings.email_smtp_secure || "true"} onChange={(value) => setSetting("email_smtp_secure", value)}>
              <option value="true">SSL</option>
              <option value="false">Plain / STARTTLS on 587</option>
            </SelectField>
            <TextField label="SMTP User" value={settings.email_smtp_user || ""} onChange={(value) => setSetting("email_smtp_user", value)} />
            <TextField label="SMTP Password" type="password" value={settings.email_smtp_password || ""} onChange={(value) => setSetting("email_smtp_password", value)} />
            <TextField label="System Address" value={settings.email_system_address || ""} onChange={(value) => setSetting("email_system_address", value)} />
          </div>
          <div className="btn-list">
            <Button
              onClick={() => saveSettingsGroup("Email Alerts", [
                "email_alerts_enabled",
                "email_alert_to",
                "email_driver",
                "email_from_address",
                "email_from_name",
                "email_smtp_host",
                "email_smtp_password",
                "email_smtp_port",
                "email_smtp_secure",
                "email_smtp_user",
                "email_system_address",
              ])}
              disabled={savingServices === "Email Alerts"}
            >
              {savingServices === "Email Alerts" ? "Saving..." : "Save Email Alerts"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <section className="grid gap-5 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Search className="size-4 text-muted-foreground" />
              <CardTitle>Search Index</CardTitle>
            </div>
            <CardDescription>Typesense connection and indexing controls.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="Typesense URL" value={settings.typesense_url || ""} onChange={(value) => setSetting("typesense_url", value)} />
              <TextField label="Typesense API Key" type="password" value={settings.typesense_api_key || ""} onChange={(value) => setSetting("typesense_api_key", value)} />
              <SelectField label="Typesense Enabled" value={settings.typesense_enabled || "true"} onChange={(value) => setSetting("typesense_enabled", value)}>
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </SelectField>
            </div>
            <div className="btn-list">
              <Button onClick={() => saveSettingsGroup("Search Index", ["typesense_url", "typesense_api_key", "typesense_enabled"])} disabled={savingServices === "Search Index"}>
                {savingServices === "Search Index" ? "Saving..." : "Save Search"}
              </Button>
              <Button variant="outline" onClick={() => testService("typesense")}>Test Typesense</Button>
              <Button variant="outline" onClick={reindexTypesense} disabled={reindexProgress?.status === "running" || reindexProgress?.status === "queued"}>
                {reindexProgress?.status === "running" || reindexProgress?.status === "queued" ? "Reindexing..." : "Reindex Search"}
              </Button>
            </div>
            {reindexProgress ? (
              <div className="form-fieldset">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-medium">Search index status: {reindexProgress.status}</span>
                  <span className="text-muted-foreground">
                    {(reindexProgress.processed || 0).toLocaleString()} / {(reindexProgress.total || 0).toLocaleString()} rows
                  </span>
                </div>
                <div className="progress">
                  <div
                    className="progress-bar progress-bar-striped progress-bar-animated bg-primary transition-all"
                    role="progressbar"
                    aria-valuenow={Math.max(0, Math.min(100, Number(reindexProgress.percent || 0)))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${Number(reindexProgress.percent || 0).toFixed(1)}% Complete`}
                    style={{ width: `${Math.max(0, Math.min(100, Number(reindexProgress.percent || 0)))}%` }}
                  >
                    <span className="visually-hidden">{Number(reindexProgress.percent || 0).toFixed(1)}% Complete</span>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                  <span>{reindexProgress.message}</span>
                  <span>{Number(reindexProgress.percent || 0).toFixed(1)}%</span>
                </div>
                {reindexProgress.current_collection ? (
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <div>
                      Current collection: <span className="font-medium text-foreground">{reindexProgress.current_collection}</span>
                      {Number(reindexProgress.current_total || 0) ? (
                        <span> · {(reindexProgress.current_processed || 0).toLocaleString()} / {(reindexProgress.current_total || 0).toLocaleString()}</span>
                      ) : null}
                    </div>
                    {reindexProgress.latest_record ? <div>Latest indexed: {reindexProgress.latest_record}</div> : null}
                    {reindexProgress.updated_at ? <div>Last update: {formatDateTime(reindexProgress.updated_at)}</div> : null}
                  </div>
                ) : reindexProgress.updated_at ? (
                  <div className="text-xs text-muted-foreground">Last update: {formatDateTime(reindexProgress.updated_at)}</div>
                ) : null}
                {reindexProgress.error ? <p className="text-xs text-destructive">{reindexProgress.error}</p> : null}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="size-4 text-muted-foreground" />
              <CardTitle>Database & Automation</CardTitle>
            </div>
            <CardDescription>Postgres connection plus automatic order sync and Chrome fulfilment queue timing.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="Postgres URL" type="password" value={settings.postgres_url || ""} onChange={(value) => setSetting("postgres_url", value)} />
              <SelectField label="Auto Sync Orders" value={settings.autosync_interval_minutes || "0"} onChange={(value) => setSetting("autosync_interval_minutes", value)}>
                {["0", "15", "30", "60", "180", "360", "720", "1440"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
              <SelectField label="Auto Pull + Queue Chrome" value={settings.auto_chrome_fulfil_interval_minutes || "0"} onChange={(value) => setSetting("auto_chrome_fulfil_interval_minutes", value)}>
                {["0", "60", "180", "360", "720", "1440"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
              <SelectField label="Auto Fulfil Pull Window" value={settings.auto_chrome_fulfil_days || "2"} onChange={(value) => setSetting("auto_chrome_fulfil_days", value)}>
                {["1", "2", "3", "7", "14", "30"].map((value) => <option key={value} value={value}>Last {value} day{value === "1" ? "" : "s"}</option>)}
              </SelectField>
              <TextField label="Auto Fulfil Pull Limit" value={settings.auto_chrome_fulfil_limit || "100"} onChange={(value) => setSetting("auto_chrome_fulfil_limit", value)} />
              <SelectField label="Cancelled Order Sync" value={settings.cancelled_orders_sync_interval_minutes || "0"} onChange={(value) => setSetting("cancelled_orders_sync_interval_minutes", value)}>
                {["0", "60", "1440", "2880", "4320", "10080"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
              <SelectField label="Cancelled Pull Window" value={settings.cancelled_orders_sync_days || "30"} onChange={(value) => setSetting("cancelled_orders_sync_days", value)}>
                {["7", "14", "30", "60", "90", "180"].map((value) => <option key={value} value={value}>Last {value} days</option>)}
              </SelectField>
              <TextField label="Odoo Pull Batch Size" value={settings.pull_orders_batch_size || "50"} onChange={(value) => setSetting("pull_orders_batch_size", value)} />
            </div>
            <p className="text-xs text-muted-foreground">Order pulls read Odoo in batches and save each batch before moving to the next one. Use 50 unless Odoo starts throttling, then reduce it.</p>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Database & Automation", [
                  "postgres_url",
                  "autosync_interval_minutes",
                  "auto_chrome_fulfil_interval_minutes",
                  "auto_chrome_fulfil_days",
                  "auto_chrome_fulfil_limit",
                  "cancelled_orders_sync_interval_minutes",
                  "cancelled_orders_sync_days",
                  "pull_orders_batch_size",
                ])}
                disabled={savingServices === "Database & Automation"}
              >
                {savingServices === "Database & Automation" ? "Saving..." : "Save Database"}
              </Button>
              <Button variant="outline" onClick={() => testService("postgres")}>Test Postgres</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="size-4 text-muted-foreground" />
              <CardTitle>Currency Conversion</CardTitle>
            </div>
            <CardDescription>OpenExchange rates used to convert Odoo order currencies into USD for Chrome fulfilment and profit reporting.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="OpenExchange API Key" type="password" value={settings.openexchange_api_key || ""} onChange={(value) => setSetting("openexchange_api_key", value)} />
              <SelectField label="Rate Sync Interval" value={settings.openexchange_sync_interval_minutes || "2880"} onChange={(value) => setSetting("openexchange_sync_interval_minutes", value)}>
                {["60", "360", "720", "1440", "2880", "10080"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
            </div>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Currency Conversion", [
                  "openexchange_api_key",
                  "openexchange_sync_interval_minutes",
                ])}
                disabled={savingServices === "Currency Conversion"}
              >
                {savingServices === "Currency Conversion" ? "Saving..." : "Save Currency"}
              </Button>
              <Button variant="outline" onClick={() => testService("openexchange")}>Sync Rates Now</Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Last sync: {settings.openexchange_last_sync_at ? formatDateTime(settings.openexchange_last_sync_at) : "Never"} {settings.openexchange_last_sync_message ? `- ${settings.openexchange_last_sync_message}` : ""}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <StoreIcon className="size-4 text-muted-foreground" />
              <CardTitle>File Storage</CardTitle>
            </div>
            <CardDescription>R2/S3 endpoint used for generated files and stored artifacts.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="R2 Files Endpoint" value={settings.storage_s3_endpoint || ""} onChange={(value) => setSetting("storage_s3_endpoint", value)} />
              <TextField label="R2 Files Bucket" value={settings.storage_s3_bucket || ""} onChange={(value) => setSetting("storage_s3_bucket", value)} />
              <TextField label="S3 Access Key" value={settings.storage_s3_access_key_id || ""} onChange={(value) => setSetting("storage_s3_access_key_id", value)} />
              <TextField label="S3 Secret Key" type="password" value={settings.storage_s3_secret_access_key || ""} onChange={(value) => setSetting("storage_s3_secret_access_key", value)} />
            </div>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("File Storage", [
                  "storage_s3_endpoint",
                  "storage_s3_bucket",
                  "storage_s3_access_key_id",
                  "storage_s3_secret_access_key",
                ])}
                disabled={savingServices === "File Storage"}
              >
                {savingServices === "File Storage" ? "Saving..." : "Save Storage"}
              </Button>
              <Button variant="outline" onClick={() => testService("s3")}>Test R2/S3</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Download className="size-4 text-muted-foreground" />
              <CardTitle>Backups</CardTitle>
            </div>
            <CardDescription>Full Postgres backups stored in Cloudflare R2.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="Backup Endpoint" value={settings.backup_s3_endpoint || ""} onChange={(value) => setSetting("backup_s3_endpoint", value)} />
              <TextField label="Backup Bucket" value={settings.backup_s3_bucket || ""} onChange={(value) => setSetting("backup_s3_bucket", value)} />
              <SelectField label="Backup Interval" value={settings.backup_interval_minutes || "0"} onChange={(value) => setSetting("backup_interval_minutes", value)}>
                {["0", "60", "180", "360", "720", "1440"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
            </div>
            <SelectField label="Restore From Backup" value={selectedBackupKey} onChange={setSelectedBackupKey}>
              <option value="">{backups.length ? "Select backup" : "No backups found"}</option>
              {backups.map((backup) => (
                <option key={backup.key} value={backup.key}>
                  {backup.name} · {formatFileSize(backup.size)}{backup.last_modified ? ` · ${formatDateTime(backup.last_modified)}` : ""}
                </option>
              ))}
            </SelectField>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Backups", [
                  "backup_s3_endpoint",
                  "backup_s3_bucket",
                  "backup_interval_minutes",
                ])}
                disabled={savingServices === "Backups"}
              >
                {savingServices === "Backups" ? "Saving..." : "Save Backups"}
              </Button>
              <Button variant="outline" onClick={loadBackups} disabled={Boolean(backupBusy)}>
                Refresh List
              </Button>
              <Button variant="outline" onClick={runBackup} disabled={Boolean(backupBusy)}>
                {backupBusy === "backup" ? "Backing Up..." : "Run Backup Now"}
              </Button>
              <Button variant="outline" onClick={restoreBackup} disabled={!selectedBackupKey || Boolean(backupBusy)}>
                {backupBusy === "restore" ? "Restoring..." : "Restore Selected"}
              </Button>
              <Button variant="destructive" onClick={deleteBackup} disabled={!selectedBackupKey || Boolean(backupBusy)}>
                {backupBusy === "delete" ? "Deleting..." : "Delete Selected"}
              </Button>
            </div>
            {backupProgress && backupProgress.status !== "idle" ? (
              <div className="form-fieldset">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span className="font-medium">Backup status: {backupProgress.status}</span>
                  <span className="text-muted-foreground">{Math.max(0, Math.min(100, Number(backupProgress.percent || 0))).toFixed(0)}%</span>
                </div>
                <div className="progress">
                  <div
                    className="progress-bar progress-bar-striped progress-bar-animated bg-primary transition-all"
                    role="progressbar"
                    aria-valuenow={Math.max(0, Math.min(100, Number(backupProgress.percent || 0)))}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${Number(backupProgress.percent || 0).toFixed(0)}% Complete`}
                    style={{ width: `${Math.max(0, Math.min(100, Number(backupProgress.percent || 0)))}%` }}
                  >
                    <span className="visually-hidden">{Number(backupProgress.percent || 0).toFixed(0)}% Complete</span>
                  </div>
                </div>
                <div className="grid gap-1 text-xs text-muted-foreground">
                  <div>{backupProgress.message}</div>
                  {backupProgress.backup_name ? <div>Backup: {backupProgress.backup_name}</div> : null}
                  {Number(backupProgress.backup_size || 0) ? <div>Size: {formatFileSize(Number(backupProgress.backup_size || 0))}</div> : null}
                  {backupProgress.updated_at ? <div>Last update: {formatDateTime(backupProgress.updated_at)}</div> : null}
                  {backupProgress.error ? <div className="text-destructive">{backupProgress.error}</div> : null}
                </div>
              </div>
            ) : null}
            <p className="text-xs text-muted-foreground">
              Restore downloads the selected R2 dump and replaces the current Postgres schema/data using pg_restore.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Bell className="size-4 text-muted-foreground" />
              <CardTitle>Amazon OTP Email</CardTitle>
            </div>
            <CardDescription>IMAP mailbox used to read Amazon dispatch and one-time-password emails every few minutes.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="IMAP Host" value={settings.amazon_otp_imap_host || ""} onChange={(value) => setSetting("amazon_otp_imap_host", value)} />
              <TextField label="IMAP Port" value={settings.amazon_otp_imap_port || "993"} onChange={(value) => setSetting("amazon_otp_imap_port", value)} />
              <TextField label="IMAP Username" value={settings.amazon_otp_imap_username || ""} onChange={(value) => setSetting("amazon_otp_imap_username", value)} />
              <TextField label="IMAP Password" type="password" value={settings.amazon_otp_imap_password || ""} onChange={(value) => setSetting("amazon_otp_imap_password", value)} />
              <TextField label="IMAP Folder" value={settings.amazon_otp_imap_folder || "INBOX"} onChange={(value) => setSetting("amazon_otp_imap_folder", value)} />
              <SelectField label="Delete Processed OTP Emails" value={settings.amazon_otp_delete_processed_emails || "true"} onChange={(value) => setSetting("amazon_otp_delete_processed_emails", value)}>
                <option value="true">Enabled</option>
                <option value="false">Disabled</option>
              </SelectField>
              <SelectField label="Use SSL" value={settings.amazon_otp_imap_ssl || "true"} onChange={(value) => setSetting("amazon_otp_imap_ssl", value)}>
                <option value="true">SSL enabled</option>
                <option value="false">SSL disabled</option>
              </SelectField>
              <SelectField label="Email Sync Interval" value={settings.amazon_otp_imap_interval_minutes || "5"} onChange={(value) => setSetting("amazon_otp_imap_interval_minutes", value)}>
                {["0", "5", "10", "15", "30", "60"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
              <SelectField label="Read Emails Since" value={settings.amazon_otp_imap_since_days || "14"} onChange={(value) => setSetting("amazon_otp_imap_since_days", value)}>
                {["1", "3", "7", "14", "30", "60"].map((value) => <option key={value} value={value}>Last {value} day{value === "1" ? "" : "s"}</option>)}
              </SelectField>
            </div>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Amazon OTP Email", [
                  "amazon_otp_imap_host",
                  "amazon_otp_imap_port",
                  "amazon_otp_imap_username",
                  "amazon_otp_imap_password",
                  "amazon_otp_imap_folder",
                  "amazon_otp_delete_processed_emails",
                  "amazon_otp_imap_ssl",
                  "amazon_otp_imap_interval_minutes",
                  "amazon_otp_imap_since_days",
                ])}
                disabled={savingServices === "Amazon OTP Email"}
              >
                {savingServices === "Amazon OTP Email" ? "Saving..." : "Save Email"}
              </Button>
              <Button variant="outline" onClick={() => testService("amazon-otp-imap")}>Test IMAP</Button>
              <Button variant="outline" onClick={syncAmazonOtp}>Sync Amazon OTP Now</Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Last sync: {settings.amazon_otp_last_sync_at ? formatDateTime(settings.amazon_otp_last_sync_at) : "Never"} {settings.amazon_otp_last_sync_message ? `- ${settings.amazon_otp_last_sync_message}` : ""}
            </p>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Link className="size-4 text-muted-foreground" />
            <CardTitle>Punchout Return URLs</CardTitle>
          </div>
          <CardDescription>The default URL is sent as BrowserFormPost URL when the app launches an Amazon Punchout cart.</CardDescription>
        </CardHeader>
        <CardContent>
          <PunchoutReturnUrlsTable
            urls={urls}
            onAdd={() => setCreating(true)}
            onEdit={setEditing}
            onChanged={onChanged}
            onResult={onResult}
          />
        </CardContent>
      </Card>
      <PunchoutReturnUrlDialog open={creating} value={emptyPunchoutReturnUrl} onClose={() => setCreating(false)} onSaved={onChanged} onResult={onResult} />
      {editing && <PunchoutReturnUrlDialog open value={editing} id={editing.id} onClose={() => setEditing(null)} onSaved={onChanged} onResult={onResult} />}
    </div>
  )
}

function intervalLabel(value: string) {
  const minutes = Number(value)
  if (!minutes) return "Off"
  if (minutes < 60) return `${minutes} min`
  return `${minutes / 60} hour(s)`
}

function PunchoutReturnUrlsTable({
  urls,
  onAdd,
  onEdit,
  onChanged,
  onResult,
}: {
  urls: PunchoutReturnUrl[]
  onAdd: () => void
  onEdit: (url: PunchoutReturnUrl) => void
  onChanged: () => Promise<void>
  onResult: (modal: ModalState) => void
}) {
  const [page, setPage] = useState(1)
  const pagedUrls = useMemo(() => {
    const offset = (page - 1) * PAGE_SIZE
    return urls.slice(offset, offset + PAGE_SIZE)
  }, [page, urls])
  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(urls.length / PAGE_SIZE))
    if (page > totalPages) setPage(totalPages)
  }, [page, urls.length])

  return (
    <div className="grid gap-3">
      <div className="flex justify-end">
        <Button onClick={onAdd}>
          <Plus className="size-4" />
          Add
        </Button>
      </div>
      <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Label</TableHead>
              <TableHead>URL</TableHead>
              <TableHead>Default</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pagedUrls.map((url) => (
              <TableRow key={url.id}>
                <TableCell>{url.label}</TableCell>
                <TableCell><span className="font-mono text-xs">{url.url}</span></TableCell>
                <TableCell>{url.is_default ? "Yes" : "No"}</TableCell>
                <TableCell>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => onResult({ ok: true, title: "Punchout Return URL", message: url.url })}>Test</Button>
                    <Button variant="outline" size="icon-sm" onClick={() => onEdit(url)}>
                      <Edit className="size-4" />
                    </Button>
                    <Button
                      variant="destructive"
                      size="icon-sm"
                      onClick={async () => {
                        try {
                          await api(`/api/punchout-return-urls/${url.id}`, { method: "DELETE" })
                          await onChanged()
                          onResult({ ok: true, title: "Punchout URL Deleted", message: `${url.label} was removed.` })
                        } catch (error) {
                          onResult({ ok: false, title: "Punchout URL Delete Failed", message: String(error) })
                        }
                      }}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!pagedUrls.length ? (
              <TableRow>
                <TableCell colSpan={4} className="py-8 text-center text-muted-foreground">No punchout return URLs found.</TableCell>
              </TableRow>
            ) : null}
          </TableBody>
      </Table>
      <PaginationControls page={page} total={urls.length} onPage={setPage} />
    </div>
  )
}

function PunchoutReturnUrlDialog({ open, value, id, onClose, onSaved, onResult }: { open: boolean; value: Omit<PunchoutReturnUrl, "id">; id?: number; onClose: () => void; onSaved: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [form, setForm] = useState(value)
  useEffect(() => {
    if (open) setForm(value)
  }, [open, value])
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{id ? "Edit Punchout Return URL" : "Add Punchout Return URL"}</DialogTitle>
          <DialogDescription>This URL must be publicly reachable by Amazon and point to /punchout/cart-return.</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3">
          <TextField label="Label" value={form.label} onChange={(label) => setForm({ ...form, label })} />
          <TextField label="URL" value={form.url} onChange={(url) => setForm({ ...form, url })} />
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={Boolean(form.is_default)} onCheckedChange={(checked) => setForm({ ...form, is_default: checked ? 1 : 0 })} />
            Default Punchout return URL
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                await api(id ? `/api/punchout-return-urls/${id}` : "/api/punchout-return-urls", {
                  method: id ? "PUT" : "POST",
                  body: JSON.stringify({ ...form, is_default: Boolean(form.is_default) }),
                })
                await onSaved()
                onClose()
                onResult({ ok: true, title: "Punchout URL Saved", message: `${form.label} was saved.` })
              } catch (error) {
                onResult({ ok: false, title: "Punchout URL Save Failed", message: String(error) })
              }
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AmazonDialog({ open, value, id, onClose, onSaved, onResult }: { open: boolean; value: Omit<AmazonAccount, "id">; id?: number; onClose: () => void; onSaved: () => Promise<void>; onResult: (modal: ModalState) => void }) {
  const [form, setForm] = useState(value)
  useEffect(() => {
    if (open) setForm(value)
  }, [open, value])
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{id ? "Edit Amazon Account" : "Add Amazon Account"}</DialogTitle>
          <DialogDescription>Use LWA client ID, client secret, and refresh token from the Amazon Business developer console.</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3 md:grid-cols-2">
          <TextField label="Name" value={form.name} onChange={(name) => setForm({ ...form, name })} />
          <TextField label="API Base URL" value={form.api_base_url} onChange={(api_base_url) => setForm({ ...form, api_base_url })} />
          <TextField label="Tracking API Base URL" value={form.tracking_api_base_url} onChange={(tracking_api_base_url) => setForm({ ...form, tracking_api_base_url })} />
          <TextField label="LWA Token URL" value={form.lwa_token_url} onChange={(lwa_token_url) => setForm({ ...form, lwa_token_url })} />
          <TextField label="LWA Client ID" value={form.lwa_client_id} onChange={(lwa_client_id) => setForm({ ...form, lwa_client_id })} />
          <TextField label="LWA Client Secret" type="password" value={form.lwa_client_secret} onChange={(lwa_client_secret) => setForm({ ...form, lwa_client_secret })} />
          <TextField label="Refresh Token" type="password" value={form.lwa_refresh_token} onChange={(lwa_refresh_token) => setForm({ ...form, lwa_refresh_token })} />
          <TextField label="Access Token Override" type="password" value={form.api_access_token} onChange={(api_access_token) => setForm({ ...form, api_access_token })} />
          <TextField label="Buyer Email" value={form.buyer_email || ""} onChange={(buyer_email) => setForm({ ...form, buyer_email })} />
          <TextField label="Buying Group ID" value={form.buying_group_id || ""} onChange={(buying_group_id) => setForm({ ...form, buying_group_id })} />
          <TextField label="Product Region" value={form.product_region || "US"} onChange={(product_region) => setForm({ ...form, product_region })} />
          <TextField label="Locale" value={form.locale || "en_US"} onChange={(locale) => setForm({ ...form, locale })} />
          <TextField label="cXML From Identity" value={form.cxml_from_identity || ""} onChange={(cxml_from_identity) => setForm({ ...form, cxml_from_identity })} />
          <TextField label="cXML Shared Secret" type="password" value={form.cxml_shared_secret || ""} onChange={(cxml_shared_secret) => setForm({ ...form, cxml_shared_secret })} />
          <TextField label="cXML Purchase Order URL" value={form.cxml_po_url || ""} onChange={(cxml_po_url) => setForm({ ...form, cxml_po_url })} />
          <TextField label="Punchout URL" value={form.cxml_punchout_url || ""} onChange={(cxml_punchout_url) => setForm({ ...form, cxml_punchout_url })} />
          <TextField label="Punchout Test URL" value={form.cxml_punchout_test_url || ""} onChange={(cxml_punchout_test_url) => setForm({ ...form, cxml_punchout_test_url })} />
          <SelectField label="cXML Auth Mode" value={form.cxml_auth_mode || "header"} onChange={(cxml_auth_mode) => setForm({ ...form, cxml_auth_mode })}>
            <option value="header">cXML Header Only</option>
            <option value="basic">HTTP Basic Only</option>
            <option value="both">cXML Header + HTTP Basic</option>
          </SelectField>
          <TextField label="Punchout Cart/Session ID" value={form.cxml_cart_session_id || ""} onChange={(cxml_cart_session_id) => setForm({ ...form, cxml_cart_session_id })} />
          <TextField label="cXML Credential Domain" value={form.cxml_credential_domain || "NetworkId"} onChange={(cxml_credential_domain) => setForm({ ...form, cxml_credential_domain })} />
          <TextField label="cXML To Identity" value={form.cxml_to_identity || "Amazon"} onChange={(cxml_to_identity) => setForm({ ...form, cxml_to_identity })} />
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={Boolean(form.is_default)} onCheckedChange={(checked) => setForm({ ...form, is_default: checked ? 1 : 0 })} />
            Default Amazon account
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                const payload = {
                  ...form,
                  buyer_email: form.buyer_email || "",
                  buying_group_id: form.buying_group_id || "",
                  product_region: form.product_region || "US",
                  locale: form.locale || "en_US",
                  cxml_from_identity: form.cxml_from_identity || "",
                  cxml_shared_secret: form.cxml_shared_secret || "",
                  cxml_po_url: form.cxml_po_url || "",
                  cxml_punchout_url: form.cxml_punchout_url || "",
                  cxml_punchout_test_url: form.cxml_punchout_test_url || "",
                  cxml_auth_mode: form.cxml_auth_mode || "header",
                  cxml_cart_session_id: form.cxml_cart_session_id || "",
                  cxml_credential_domain: form.cxml_credential_domain || "NetworkId",
                  cxml_to_identity: form.cxml_to_identity || "Amazon",
                  is_default: Boolean(form.is_default),
                }
                await api(id ? `/api/amazon-accounts/${id}` : "/api/amazon-accounts", { method: id ? "PUT" : "POST", body: JSON.stringify(payload) })
                await onSaved()
                onClose()
                onResult({ ok: true, title: "Amazon Account Saved", message: `${form.name} was saved. Buyer email: ${form.buyer_email || "not set"}` })
              } catch (error) {
                onResult({ ok: false, title: "Amazon Account Save Failed", message: String(error) })
              }
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function SettingsTable<T extends { id: number }>({
  title,
  description,
  rows,
  columns,
  renderRow,
  onAdd,
  onEdit,
  onDelete,
  onTest,
  children,
}: {
  title: string
  description: string
  rows: T[]
  columns: string[]
  renderRow: (row: T) => ReactNode[]
  onAdd: () => void
  onEdit: (row: T) => void
  onDelete: (row: T) => Promise<void>
  onTest: (row: T) => void | Promise<void>
  children: ReactNode
}) {
  const [page, setPage] = useState(1)
  const pagedRows = useMemo(() => {
    const offset = (page - 1) * PAGE_SIZE
    return rows.slice(offset, offset + PAGE_SIZE)
  }, [page, rows])
  useEffect(() => {
    const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
    if (page > totalPages) setPage(totalPages)
  }, [page, rows.length])

  return (
    <Card>
      {children}
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>{title}</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        <Button onClick={onAdd}>
          <Plus className="size-4" />
          Add
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              {columns.map((column) => (
                <TableHead key={column}>{column}</TableHead>
              ))}
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pagedRows.map((row) => (
              <TableRow key={row.id}>
                {renderRow(row).map((cell, index) => (
                  <TableCell key={index}>{cell}</TableCell>
                ))}
                <TableCell>
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" size="sm" onClick={() => onTest(row)}>Test</Button>
                    <Button variant="outline" size="icon-sm" onClick={() => onEdit(row)}>
                      <Edit className="size-4" />
                    </Button>
                    <Button variant="destructive" size="icon-sm" onClick={() => onDelete(row)}>
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
            {!pagedRows.length ? (
              <TableRow>
                <TableCell colSpan={columns.length + 1} className="py-8 text-center text-muted-foreground">No rows found.</TableCell>
              </TableRow>
            ) : null}
          </TableBody>
        </Table>
      </CardContent>
      <CardFooter>
        <PaginationControls page={page} total={rows.length} onPage={setPage} />
      </CardFooter>
    </Card>
  )
}

function TextField({ label, value, onChange, type = "text", placeholder = "" }: { label: string; value: string; onChange: (value: string) => void; type?: string; placeholder?: string }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      <Input type={type} value={value || ""} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
    </div>
  )
}

export default App
