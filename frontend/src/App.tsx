import { useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import {
  IconAlertCircle as AlertCircle,
  IconBell as Bell,
  IconBuildingStore as StoreIcon,
  IconCircleCheck as CheckCircle2,
  IconChevronDown as ChevronDown,
  IconChevronLeft as ChevronLeft,
  IconChevronRight as ChevronRight,
  IconColumns3 as Columns3,
  IconDatabase as Database,
  IconDownload as Download,
  IconEdit as Edit,
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
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
  odoo_order_name: string
  odoo_order_url: string
  product_name: string
  default_code: string
  asin: string
  missing_asin?: string
  missing_asin_url?: string
  original_asin?: string
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
  amazon_order_id: string
  amazon_order_url: string
  amazon_account_name: string
  order_engine: string
  tracking_status: string
  tracking_payload: string
  fulfilment_note: string
  last_error: string
  pulled_at: string
  ordered_at: string
  created_at: string
  updated_at: string
  duplicate_asin_count: number
  inventory_quantity: number
}

type DuplicateAsin = {
  asin: string
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
  lines: OrderLine[]
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

type BulkGroup = {
  asin: string
  asin_url: string
  quantity: number
  order_names: string[]
  line_ids: number[]
  product_names: string[]
  has_missing_order: boolean
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
  tracking_orders?: TrackingOrder[]
  default_ordering_engine: string
  pull_orders_days?: number
  pull_orders_limit?: number
  message?: string
  ok?: boolean
  punchout_launch_url?: string
}

type ModalState = { title: string; message: string; ok: boolean } | null

const ADMIN_TOKEN_STORAGE_KEY = "admin_access_token"
const PULL_DAYS_STORAGE_KEY = "pull_orders_days"
const PULL_LIMIT_STORAGE_KEY = "pull_orders_limit"
const PULL_STORE_IDS_STORAGE_KEY = "pull_orders_store_ids"

class AdminAuthError extends Error {
  constructor(message = "Admin access code required.") {
    super(message)
    this.name = "AdminAuthError"
  }
}

function savedAdminToken() {
  return typeof window !== "undefined" ? window.localStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || "" : ""
}

function savedPullSetting(key: string, fallback: string) {
  return typeof window !== "undefined" ? window.localStorage.getItem(key) || fallback : fallback
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

function notifyAdminAuthRequired() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("admin-auth-required"))
  }
}

const PAGE_SIZE = 100

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
  inserted_records: number
  error: string
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
}

type OrderColumnKey =
  | "odoo_order"
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
  | "amazon_order"
  | "comments"
  | "error"

type SortKey = "pulled_at" | "ordered_at" | "odoo_order_name"
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
  { key: "odoo_order", label: "Odoo Order", width: "w-32", sortable: "odoo_order_name" },
  { key: "product", label: "Product", width: "w-[520px]" },
  { key: "reference", label: "Reference", width: "w-48" },
  { key: "pulled_at", label: "Pulled At", width: "w-44", sortable: "pulled_at" },
  { key: "ordered_at", label: "Placed At", width: "w-44", sortable: "ordered_at" },
  { key: "asin", label: "ASIN", width: "w-36" },
  { key: "spaid", label: "SPAID", width: "w-44" },
  { key: "qty", label: "Qty", width: "w-20" },
  { key: "odoo_status", label: "Odoo Status", width: "w-32" },
  { key: "inventory", label: "Inventory", width: "w-36" },
  { key: "state", label: "State", width: "w-28" },
  { key: "engine", label: "Engine", width: "w-28" },
  { key: "amazon_account", label: "Amazon Account", width: "w-44" },
  { key: "tracking", label: "Tracking", width: "w-36" },
  { key: "amazon_order", label: "Amazon Order", width: "w-44" },
  { key: "comments", label: "Comments", width: "w-80" },
  { key: "error", label: "Error", width: "w-64" },
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
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
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
  return <Badge variant="outline">{status}</Badge>
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
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={remember} onCheckedChange={(checked) => setRemember(Boolean(checked))} />
            Save token on this PC
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
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={remember} onCheckedChange={(checked) => setRemember(Boolean(checked))} />
              Save token on this PC
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
}: {
  page: number
  total: number
  onPage: (page: number) => void
  disabled?: boolean
}) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
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
        Page {page} / {totalPages} - {total.toLocaleString()} rows
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
  const [data, setData] = useState<DashboardData | null>(null)
  const [inventory, setInventory] = useState<InventoryItem[]>([])
  const [inventoryPage, setInventoryPage] = useState(1)
  const [inventoryTotal, setInventoryTotal] = useState(0)
  const [ordersPage, setOrdersPage] = useState(1)
  const [ordersTotal, setOrdersTotal] = useState(0)
  const [missingRows, setMissingRows] = useState<OrderLine[]>([])
  const [missingPage, setMissingPage] = useState(1)
  const [missingTotal, setMissingTotal] = useState(0)
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
  const [bulkGroups, setBulkGroups] = useState<BulkGroup[]>([])
  const [bulkPage, setBulkPage] = useState(1)
  const [bulkTotal, setBulkTotal] = useState(0)
  const [costlyRows, setCostlyRows] = useState<OrderLine[]>([])
  const [costlyPage, setCostlyPage] = useState(1)
  const [costlyTotal, setCostlyTotal] = useState(0)
  const [page, setPage] = useState("home")
  const [openNavGroup, setOpenNavGroup] = useState<string | null>(null)
  const [storeId, setStoreId] = useState("")
  const [addressId, setAddressId] = useState("")
  const [amazonAccountId, setAmazonAccountId] = useState("")
  const [orderingEngine, setOrderingEngine] = useState("rest")
  const [days, setDays] = useState(() => savedPullSetting(PULL_DAYS_STORAGE_KEY, "7"))
  const [limit, setLimit] = useState(() => savedPullSetting(PULL_LIMIT_STORAGE_KEY, "50"))
  const [pullStoreIds, setPullStoreIds] = useState<string[]>(savedPullStoreIds)
  const [search, setSearch] = useState("")
  const [searchRows, setSearchRows] = useState<OrderLine[] | null>(null)
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
  const [allowMissingSpaid, setAllowMissingSpaid] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey>("pulled_at")
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc")
  const [showColumnSettings, setShowColumnSettings] = useState(false)
  const [orderColumns, setOrderColumns] = useState<OrderColumn[]>(loadOrderColumns)
  const [draggedColumnKey, setDraggedColumnKey] = useState<OrderColumnKey | null>(null)
  const [busy, setBusy] = useState("")
  const [modal, setModal] = useState<ModalState>(null)
  const [adminTokenSaved, setAdminTokenSaved] = useState(Boolean(savedAdminToken()))
  const [adminAccessOpen, setAdminAccessOpen] = useState(!savedAdminToken())
  const [adminAuthError, setAdminAuthError] = useState("")
  const [adminAuthBusy, setAdminAuthBusy] = useState(false)

  function pagedQuery(nextStoreId: string, nextPage = 1, extra?: Record<string, string | number>) {
    const query = new URLSearchParams()
    if (nextStoreId) query.set("store_id", nextStoreId)
    query.set("page", String(nextPage))
    query.set("per_page", String(PAGE_SIZE))
    Object.entries(extra || {}).forEach(([key, value]) => query.set(key, String(value)))
    return `?${query.toString()}`
  }

  function resetPagination() {
      setOrdersPage(1)
      setOrdersSelectAll(false)
      setSelected([])
      setInventoryPage(1)
      setMissingPage(1)
      setMissingSelectAll(false)
      setMissingSelected([])
      setFulfilmentPendingPage(1)
      setPendingSelectAll(false)
      setPendingSelected([])
      setEpostPage(1)
      setEpostSelectAll(false)
      setEpostSelected([])
      setTrackingPage(1)
      setTrackingSelectAll(false)
      setTrackingSelected([])
      setBulkPage(1)
      setBulkSelectAll(false)
      setBulkSelected([])
      setCostlyPage(1)
      setCostlySelectAll(false)
      setCostlySelected([])
  }

  function applyDashboardData(next: DashboardData, nextPage = ordersPage) {
    setData(next)
    setOrdersPage(next.page || nextPage)
    setOrdersTotal(next.total || 0)
    const resolvedStore = String(next.current_store_id || "")
    setStoreId(resolvedStore)
    setAddressId((current) => current || String(next.addresses.find((address) => address.is_default)?.id || next.addresses[0]?.id || ""))
    setAmazonAccountId((current) => current || String(next.amazon_accounts.find((account) => account.is_default)?.id || next.amazon_accounts[0]?.id || ""))
    setOrderingEngine(next.default_ordering_engine || "rest")
    if (next.pull_orders_days) setDays(String(next.pull_orders_days))
    if (next.pull_orders_limit) setLimit(String(next.pull_orders_limit))
    setPullStoreIds((current) => {
      const valid = new Set(next.stores.map((store) => String(store.id)))
      const filtered = current.filter((id) => valid.has(id))
      if (filtered.length) return filtered
      return resolvedStore ? [resolvedStore] : next.stores[0]?.id ? [String(next.stores[0].id)] : []
    })
  }

  async function refresh(nextStoreId = storeId, nextPage = ordersPage) {
    const query = pagedQuery(nextStoreId, nextPage)
    const next = await api<DashboardData>(`/api/dashboard${query}`)
    applyDashboardData(next, nextPage)
  }

  async function handleAdminTokenSave(token: string, remember = true) {
    const nextToken = token.trim()
    if (!nextToken) return
    setAdminAuthBusy(true)
    setAdminAuthError("")
    setModal(null)
    try {
      const next = await apiWithAdminToken<DashboardData>(`/api/dashboard${pagedQuery(storeId, ordersPage)}`, nextToken)
      window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      if (remember) {
        window.localStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, nextToken)
      } else {
        window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, nextToken)
      }
      applyDashboardData(next, ordersPage)
      setAdminTokenSaved(true)
      setAdminAccessOpen(false)
      setAdminAuthError("")
    } catch (error) {
      window.localStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
      setAdminTokenSaved(false)
      if (error instanceof AdminAuthError) {
        setAdminAuthError("That code did not work. Please check it and try again, or use master code 1284.")
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
    setAdminAuthError("")
    setAdminTokenSaved(false)
    setAdminAccessOpen(true)
  }

  useEffect(() => {
    const listener = () => {
      setAdminAuthError("Admin code required. Please enter a valid code or master code 1284.")
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
    if (!savedAdminToken()) {
      setAdminTokenSaved(false)
      return
    }
    refresh(storeId, ordersPage).catch((error) => {
      if (error instanceof AdminAuthError) return
      setModal({ ok: false, title: "Unable to load app", message: String(error) })
    })
  }, [ordersPage])

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
    if (page !== "missing") return
    api<{ rows: OrderLine[]; total: number }>(`/api/missing${pagedQuery(storeId, missingPage)}`)
      .then((result) => {
        setMissingRows(result.rows)
        setMissingTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Missing orders load failed", message: String(error) }))
  }, [page, storeId, missingPage])

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
    if (page !== "epost") return
    api<{ rows: EpostTrackingRow[]; total: number }>(`/api/epost/tracking${pagedQuery(storeId, epostPage, { status: epostStatusFilter })}`)
      .then((result) => {
        setEpostRows(result.rows)
        setEpostTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "ePost tracking load failed", message: String(error) }))
  }, [page, storeId, epostPage, epostStatusFilter])

  useEffect(() => {
    if (page !== "bulk") return
    api<{ groups: BulkGroup[]; total: number }>(`/api/bulk${pagedQuery(storeId, bulkPage, { days: 2 })}`)
      .then((result) => {
        setBulkGroups(result.groups)
        setBulkTotal(result.total || 0)
      })
      .catch((error) => setModal({ ok: false, title: "Bulk opportunities load failed", message: String(error) }))
  }, [page, storeId, bulkPage])

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
    const term = search.trim()
    if (!term) {
      setSearchRows(null)
      setOrdersTotal(data?.total || ordersTotal)
      return
    }
    const timer = window.setTimeout(() => {
      api<DashboardData>(`/api/search${pagedQuery(storeId, ordersPage)}&q=${encodeURIComponent(term)}`)
        .then((result) => {
          setSearchRows(result.rows)
          setOrdersTotal(result.total || 0)
        })
        .catch(() => setSearchRows(null))
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search, storeId, ordersPage])

  useEffect(() => {
    window.localStorage.setItem(
      ORDER_TABLE_STORAGE_KEY,
      JSON.stringify(orderColumns.map((column) => ({ key: column.key, visible: column.visible !== false }))),
    )
  }, [orderColumns])

  const rows = searchRows || data?.rows || []
  const filteredRows = useMemo(() => {
    if (searchRows) return rows
    const term = search.trim().toLowerCase()
    if (!term) return rows
    return rows.filter((row) =>
      [row.odoo_order_name, row.product_name, row.asin, row.state, row.odoo_status_label, row.amazon_order_id, row.amazon_account_name]
        .join(" ")
        .toLowerCase()
        .includes(term),
    )
  }, [rows, search, searchRows])
  const sortedRows = useMemo(() => {
    const multiplier = sortDirection === "asc" ? 1 : -1
    return [...filteredRows].sort((a, b) => {
      if (sortKey === "odoo_order_name") {
        return a.odoo_order_name.localeCompare(b.odoo_order_name, undefined, { numeric: true, sensitivity: "base" }) * multiplier
      }
      const aTime = Date.parse(a[sortKey] || "")
      const bTime = Date.parse(b[sortKey] || "")
      const aValue = Number.isNaN(aTime) ? 0 : aTime
      const bValue = Number.isNaN(bTime) ? 0 : bTime
      return (aValue - bValue) * multiplier
    })
  }, [filteredRows, sortDirection, sortKey])
  const visibleOrderColumns = orderColumns.filter((column) => column.visible !== false)
  const orderExportColumns = visibleOrderColumns.map((column) => ({ key: column.key === "odoo_order" ? "odoo_order_name" : column.key === "product" ? "product_name" : column.key === "reference" ? "default_code" : column.key === "qty" ? "quantity" : column.key === "odoo_status" ? "odoo_status_label" : column.key === "amazon_account" ? "amazon_account_name" : column.key === "tracking" ? "tracking_status" : column.key === "amazon_order" ? "amazon_order_id" : column.key === "comments" ? "fulfilment_note" : column.key === "error" ? "last_error" : column.key, label: column.label }))
  const selectedRows = rows.filter((row) => selected.includes(row.id))
  const selectedClubName = selectedRows.length
    ? `Nutricity ${Array.from(new Set(selectedRows.map((row) => row.odoo_order_name))).join(" ")}`
    : ""

  async function runAction(title: string, fn: () => Promise<DashboardData | { message?: string; ok?: boolean; defer_refresh?: boolean }>) {
    try {
      setBusy(title)
      const result = await fn()
      if ("rows" in result) {
        applyDashboardData(result)
        if (result.punchout_launch_url) {
          window.open(result.punchout_launch_url, "_blank")
        }
      } else if (result.defer_refresh) {
        window.setTimeout(() => {
          void refresh().catch((error) => setModal({ ok: false, title: "Refresh Failed", message: String(error) }))
        }, 2500)
      } else {
        await refresh()
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

  function moveColumn(dragKey: OrderColumnKey, targetKey: OrderColumnKey) {
    if (dragKey === targetKey) return
    setOrderColumns((current) => {
      const next = [...current]
      const from = next.findIndex((column) => column.key === dragKey)
      const to = next.findIndex((column) => column.key === targetKey)
      if (from < 0 || to < 0) return current
      const [moved] = next.splice(from, 1)
      next.splice(to, 0, moved)
      return next
    })
  }

  function applySort(nextSortKey: SortKey) {
    if (sortKey === nextSortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"))
      return
    }
    setSortKey(nextSortKey)
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
      case "odoo_order":
        return row.odoo_order_url ? (
          <a
            className={`${row.state === "missing" ? "text-destructive" : "text-primary"} underline-offset-4 hover:underline`}
            href={row.odoo_order_url}
            target="_blank"
            onDoubleClick={(event) => {
              event.preventDefault()
              copyText(row.odoo_order_name, "Odoo order number")
            }}
          >
            {row.odoo_order_name}
          </a>
        ) : (
          <button className="font-medium" type="button" onDoubleClick={() => copyText(row.odoo_order_name, "Odoo order number")}>
            {row.odoo_order_name}
          </button>
        )
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
      case "pulled_at":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.pulled_at || row.created_at)}</span>
      case "ordered_at":
        return <span className="text-xs text-muted-foreground">{formatDateTime(row.ordered_at)}</span>
      case "asin":
        return row.asin ? (
          <a className="text-primary underline-offset-4 hover:underline" href={`https://www.amazon.com/dp/${row.asin}`} target="_blank">
            {row.asin}
          </a>
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
      case "amazon_order":
        return row.amazon_order_url ? (
          <a className="text-primary underline-offset-4 hover:underline" href={row.amazon_order_url} target="_blank">
            {row.amazon_order_id}
          </a>
        ) : (
          row.amazon_order_id
        )
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
  const pageTitles: Record<string, string> = {
    home: "Dashboard",
    orders: "Orders",
    "pull-jobs": "Pull Jobs",
    tracking: "Tracking",
    "amazon-otp": "Amazon OTP",
    epost: "ePost Tracking",
    "duplicate-tracking": "Duplicate Tracking",
    "fulfilment-pending": "Pending Dispatch",
    missing: "Missing ASINs",
    bulk: "Bulk Ordering",
    costly: "Cost Review",
    "profit-loss": "Profit / Loss",
    accounting: "Accounting",
    downloads: "Downloads",
    inventory: "Inventory",
    settings: "Settings",
  }
  const pageTitle = pageTitles[page] || "Dashboard"
  const navGroups = [
    {
      key: "operations",
      label: "Operations",
      icon: ShoppingCart,
      items: [
        ["orders", "Orders", ShoppingCart],
        ["pull-jobs", "Pull Jobs", RefreshCw],
        ["bulk", "Bulk Ordering", PackageCheck],
        ["missing", "Missing ASINs", AlertCircle],
        ["costly", "Cost Review", AlertCircle],
        ["fulfilment-pending", "Pending Dispatch", AlertCircle],
        ["inventory", "Inventory", PackageCheck],
      ],
    },
    {
      key: "tracking",
      label: "Tracking",
      icon: PackageCheck,
      items: [
        ["tracking", "Amazon Tracking", PackageCheck],
        ["amazon-otp", "Amazon OTP", Bell],
        ["epost", "ePost Global", PackageCheck],
        ["duplicate-tracking", "Duplicate Tracking", AlertCircle],
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
      <AdminAccessDialog open={adminAccessOpen} onSubmit={handleAdminTokenSave} onClose={() => setAdminAccessOpen(false)} error={adminAuthError} busy={adminAuthBusy} />
      <header className="navbar navbar-expand-md d-print-none sticky top-0 z-20">
        <div className="container-xl flex items-center justify-between gap-4 py-3">
          <button className="navbar-brand" onClick={() => setPage("home")}>
            <div className="avatar avatar-sm bg-primary text-primary-fg">
              <PackageCheck className="size-4" />
            </div>
            <div>
              <h1 className="navbar-brand-title">Amazon Business Fulfilment</h1>
              <p className="navbar-brand-subtitle">Odoo ASIN orders, Amazon ordering, delivery tracking.</p>
            </div>
          </button>
          <div className="flex items-center gap-2">
            <button className="btn btn-icon" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Day mode"><Sun className="size-5" /></button>
            <button className="btn btn-icon" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Dark mode"><Moon className="size-5" /></button>
            <button className="btn btn-icon relative" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Notifications">
              <Bell className="size-5" />
              {(missingRows.length + costlyRows.length + fulfilmentPendingRows.length) > 0 && <span className="absolute right-1 top-1 size-2 rounded-full bg-red" />}
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
        <div className="navbar-tabs">
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
        </div>
      </header>

      <main className="page-wrapper">
      <div className="page-header d-print-none">
        <div className="container-xl">
          <div className="page-header-row">
            <div>
              <div className="page-pretitle">Control panel</div>
              <h2 className="page-title">{pageTitle}</h2>
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
                      <Input type="number" min="1" max="50000" value={limit} onChange={(event) => setLimit(event.target.value)} />
                    </div>
                    <Button variant="outline" onClick={savePullDefaults} disabled={Boolean(busy)}>
                      Save Pull Defaults
                    </Button>
                    </div>
                    <SelectField className="max-w-md" label="Store view" value={storeId} onChange={(value) => { resetPagination(); refresh(value, 1) }}>
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
                  <CardTitle>Duplicate ASINs Across Recent Orders</CardTitle>
                </CardHeader>
                <CardContent>
                  {data?.duplicate_asins.length ? (
                    <div className="list-group">
                      {data.duplicate_asins.map((group) => (
                        <button
                          key={group.asin}
                          onClick={() => {
                            setSearch(group.asin)
                            setSelected(rows.filter((row) => row.asin === group.asin && !row.amazon_order_id).map((row) => row.id))
                          }}
                          className="list-group-item"
                        >
                          <span className="font-mono font-medium">{group.asin}</span>
                          <span className="text-muted-foreground">
                            {group.order_count} orders, qty {group.total_quantity}
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No duplicate ASIN groups waiting to order.</p>
                  )}
                </CardContent>
              </Card>
            </section>

            <Card>
              <CardHeader className="gap-4">
                <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                  <CardTitle>Odoo Order Lines</CardTitle>
                  <div className="btn-list">
                    <SelectField className="w-[190px]" label="Sort by" value={sortKey} onChange={(value) => setSortKey(value as SortKey)}>
                      <option value="pulled_at">Pulled date</option>
                      <option value="ordered_at">Placed date</option>
                      <option value="odoo_order_name">Order number</option>
                    </SelectField>
                    <div className="grid gap-1.5">
                      <Label>Direction</Label>
                      <Button variant="outline" onClick={() => setSortDirection((current) => (current === "asc" ? "desc" : "asc"))}>
                        {sortDirection === "asc" ? "Ascending" : "Descending"}
                      </Button>
                    </div>
                    <SearchBox className="w-full sm:w-[360px]" value={search} onChange={setSearch} placeholder="Search order, product, ASIN, status..." />
                    <Button variant="outline" onClick={() => setShowColumnSettings((current) => !current)}>
                      <Columns3 className="size-4" />
                      Columns
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!selected.length || Boolean(busy)}
                      onClick={() =>
                        runAction("Place Selected", () =>
                          api<DashboardData>("/api/place", {
                            method: "POST",
                            body: JSON.stringify({
                              store_id: Number(storeId),
                              address_id: Number(addressId),
                              amazon_account_id: Number(amazonAccountId),
                              line_ids: selected,
                              ordering_engine: orderingEngine,
                              allow_missing_spaid: allowMissingSpaid,
                            }),
                          }),
                        )
                      }
                    >
                      Place Selected
                    </Button>
                    <Button
                      disabled={!selected.length || Boolean(busy)}
                      onClick={() =>
                        runAction("Club Place Selected", () =>
                          api<DashboardData>("/api/place", {
                            method: "POST",
                            body: JSON.stringify({
                              store_id: Number(storeId),
                              address_id: Number(addressId),
                              amazon_account_id: Number(amazonAccountId),
                              line_ids: selected,
                              club: true,
                              ordering_engine: orderingEngine,
                              allow_missing_spaid: allowMissingSpaid,
                            }),
                          }),
                        )
                      }
                    >
                      Club Place
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!selected.length || Boolean(busy)}
                      onClick={() => {
                        const confirmed = window.confirm("Reset selected lines to fresh pulled status? This clears Amazon order IDs, Chrome job state, tracking, pricing, and errors for those lines.")
                        if (!confirmed) return
                        runAction("Reset Selected", () =>
                          api<DashboardData>("/api/lines/reset-fulfilment", {
                            method: "POST",
                            body: JSON.stringify({ store_id: Number(storeId), line_ids: selected }),
                          }),
                        )
                      }}
                    >
                      <RefreshCw className="size-4" />
                      Reset Selected
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={!selected.length || Boolean(busy)}
                      onClick={() =>
                        runAction("Delete Selected Lines", () =>
                          api<DashboardData>("/api/lines/delete", {
                            method: "POST",
                            body: JSON.stringify({ store_id: Number(storeId), line_ids: selected }),
                          }),
                        )
                      }
                    >
                      <Trash2 className="size-4" />
                      Delete
                    </Button>
                  </div>
                </div>
                <PaginationControls page={ordersPage} total={ordersTotal} onPage={setOrdersPage} disabled={Boolean(busy)} />
                {selectedClubName && <p className="text-sm text-muted-foreground">Clubbed recipient: {selectedClubName}</p>}
                {orderingEngine === "cxml" && (
                  <label className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Checkbox checked={allowMissingSpaid} onCheckedChange={(checked) => setAllowMissingSpaid(Boolean(checked))} />
                    Allow cXML submit without SPAID
                  </label>
                )}
                <ExportControls
                  view="orders"
                  storeId={storeId}
                  columns={orderExportColumns}
                  selectedIds={selected}
                  selectAll={ordersSelectAll}
                  total={ordersTotal}
                  filters={{ q: search.trim() }}
                  onSelectAll={() => setOrdersSelectAll(true)}
                  onClear={() => { setOrdersSelectAll(false); setSelected([]) }}
                  onResult={setModal}
                  onDownloads={() => setPage("downloads")}
                  extraAction={
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!selected.length || Boolean(busy)}
                      onClick={() => {
                        const confirmed = window.confirm("Reset selected lines to fresh pulled status? This clears Amazon order IDs, Chrome job state, tracking, pricing, and errors for those lines.")
                        if (!confirmed) return
                        runAction("Reset Selected", () =>
                          api<DashboardData>("/api/lines/reset-fulfilment", {
                            method: "POST",
                            body: JSON.stringify({ store_id: Number(storeId), line_ids: selected }),
                          }),
                        )
                      }}
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
                          onDragStart={() => setDraggedColumnKey(column.key)}
                          onDragOver={(event) => event.preventDefault()}
                          onDrop={() => {
                            if (draggedColumnKey) moveColumn(draggedColumnKey, column.key)
                            setDraggedColumnKey(null)
                          }}
                          className="badge badge-outline cursor-grab active:cursor-grabbing"
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
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">
                        <Checkbox
                          checked={filteredRows.length > 0 && filteredRows.every((row) => selected.includes(row.id))}
                          onCheckedChange={(checked) => {
                            setOrdersSelectAll(false)
                            const pageIds = filteredRows.map((row) => row.id)
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
                    {sortedRows.map((row) => (
                      <TableRow key={row.id} className={["cancelled", "refunded"].includes(row.odoo_status_label) ? "bg-destructive/5" : ""}>
                        <TableCell>
                          <Checkbox
                            checked={selected.includes(row.id)}
                            onCheckedChange={(checked) => {
                              setOrdersSelectAll(false)
                              setSelected((current) => (checked ? [...current, row.id] : current.filter((id) => id !== row.id)))
                            }}
                          />
                        </TableCell>
                        {visibleOrderColumns.map((column) => (
                          <TableCell key={column.key} className="overflow-hidden">
                            {renderOrderCell(row, column)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
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
        {page === "bulk" && (
          <BulkPage
            groups={bulkGroups}
            storeId={Number(storeId)}
            addressId={Number(addressId)}
            amazonAccountId={Number(amazonAccountId)}
            orderingEngine={orderingEngine}
            page={bulkPage}
            total={bulkTotal}
            onPage={setBulkPage}
            selected={bulkSelected}
            selectAll={bulkSelectAll}
            onSelected={setBulkSelected}
            onSelectAll={setBulkSelectAll}
            onResult={setModal}
            onNavigate={setPage}
            onRefresh={async () => {
              const result = await api<{ groups: BulkGroup[]; total: number }>(`/api/bulk${pagedQuery(storeId, bulkPage, { days: 2 })}`)
              setBulkGroups(result.groups)
              setBulkTotal(result.total || 0)
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
          <ProfitLossPage stores={stores} storeId={storeId} onResult={setModal} />
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
              setModal({ ok: true, title: "Replacement Assigned", message })
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
  const costlyCount = stateCount("costly") || costlyRows.length
  const bulkCount = bulkGroups.length || duplicateCount
  const reviewCount = missingCount + costlyCount + fulfilmentPendingRows.length
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
            {costlyRows.length ? <button className="list-group-item" onClick={() => onNavigate("costly")}>Approve or reject {costlyRows.length} costly order(s).</button> : null}
            {epostLost ? <button className="list-group-item text-destructive" onClick={() => onNavigate("epost")}>Review {epostLost} lost ePost shipment(s).</button> : null}
            {fulfilmentPendingRows.length ? <button className="list-group-item text-destructive" onClick={() => onNavigate("fulfilment-pending")}>Fix {fulfilmentPendingRows.length} Amazon delivered/Odoo pending order(s).</button> : null}
            </div>
            {!missingRows.length && !costlyRows.length && !epostLost && !fulfilmentPendingRows.length ? <p className="text-muted-foreground">No urgent review queues right now.</p> : null}
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

  async function refreshTracking() {
    setLoading(true)
    try {
      const query = new URLSearchParams()
      if (storeId) query.set("store_id", storeId)
      query.set("page", String(page))
      query.set("per_page", String(PAGE_SIZE))
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
  }, [storeId, page])

  useEffect(() => setLocalTotal(total), [total])

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Package Tracking</CardTitle>
          <CardDescription>Chrome tracking extension updates package status, carrier, tracking ID, and latest scan until delivered.</CardDescription>
        </div>
        <Button variant="outline" onClick={refreshTracking} disabled={loading}>
          <RefreshCw className="size-4" />
          Refresh
        </Button>
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
                <TableRow key={order.amazon_order_id}>
                  <TableCell>
                    <Checkbox
                      checked={selected.includes(order.amazon_order_id)}
                      onCheckedChange={(checked) => {
                        onSelectAll(false)
                        onSelected(checked ? Array.from(new Set([...selected, order.amazon_order_id])) : selected.filter((id) => id !== order.amazon_order_id))
                      }}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1">
                      {order.lines.slice(0, 4).map((line) => (
                        <a key={line.id} className="text-primary underline-offset-4 hover:underline" href={line.odoo_order_url} target="_blank">
                          {line.odoo_order_name}
                        </a>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <a className="font-mono text-primary underline-offset-4 hover:underline" href={order.amazon_order_url} target="_blank">
                      {order.amazon_order_id}
                    </a>
                  </TableCell>
                  <TableCell><StatusBadge value={order.tracking_status || firstPackage.status || "Unknown"} /></TableCell>
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

function AmazonOtpPage({ onResult }: { onResult: (modal: ModalState) => void }) {
  const [rows, setRows] = useState<AmazonOtpRow[]>([])
  const [query, setQuery] = useState("")
  const [loading, setLoading] = useState(false)

  async function refreshOtp(nextQuery = query) {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (nextQuery.trim()) params.set("q", nextQuery.trim())
      const result = await api<{ ok: boolean; rows: AmazonOtpRow[]; total: number }>(`/api/amazon-otp${params.toString() ? `?${params.toString()}` : ""}`)
      setRows(result.rows || [])
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
    refreshOtp("")
  }, [])

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
        <form className="flex flex-col gap-2 md:flex-row" onSubmit={(event) => { event.preventDefault(); refreshOtp(query) }}>
          <SearchBox className="w-full md:w-[520px]" value={query} onChange={setQuery} placeholder="Search OTP, tracking number, Amazon order, Odoo order, product" />
          <Button type="submit" variant="outline" disabled={loading}>
            <Search className="size-4" />
            Search
          </Button>
          <a className="btn btn-outline-secondary" href="/public/amazon-otp" target="_blank">Open Public Page</a>
        </form>
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
                    <span className="text-xs text-muted-foreground">{row.odoo_order_names}</span>
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
                        onCheckedChange={(checked) => {
                          onSelectAll(false)
                          onSelected(checked ? Array.from(new Set([...selected, row.id])) : selected.filter((id) => id !== row.id))
                        }}
                      />
                    </TableCell>
                    <TableCell>{row.store_name}</TableCell>
                    <TableCell>
                      <a className="font-medium text-primary underline-offset-4 hover:underline" href={row.odoo_order_url} target="_blank">
                        {row.odoo_order_name}
                      </a>
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
                    onCheckedChange={(checked) => {
                      onSelectAll(false)
                      onSelected(checked ? Array.from(new Set([...selected, row.id])) : selected.filter((id) => id !== row.id))
                    }}
                  />
                </TableCell>
                <TableCell>{row.store_name}</TableCell>
                <TableCell>
                  <a className="text-primary underline-offset-4 hover:underline" href={row.odoo_order_url} target="_blank">
                    {row.odoo_order_name}
                  </a>
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
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [selectAll, setSelectAll] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const params = new URLSearchParams({ q: query })
      if (storeId) params.set("store_id", storeId)
      const result = await api<{ rows: DuplicateTrackingRow[]; total: number }>(`/api/duplicate-tracking?${params.toString()}`)
      setRows(result.rows || [])
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
  }, [storeId])

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
            <Button variant="outline" onClick={load} disabled={loading}>
              <Search className="size-4" />
              Filter
            </Button>
            <Button variant="outline" onClick={load} disabled={loading}>
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
            total={rows.length}
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
                      onCheckedChange={(checked) => {
                        setSelectAll(false)
                        setSelected(checked ? Array.from(new Set([...selected, row.tracking_code])) : selected.filter((id) => id !== row.tracking_code))
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
                        <span key={order} className="font-medium">{order}</span>
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
  const [form, setForm] = useState({ asin: "", quantity: "1", product_name: "", notes: "" })
  async function addManualInventory() {
    try {
      const result = await api<{ ok: boolean; message: string; items: InventoryItem[]; total: number }>("/api/inventory", {
        method: "POST",
        body: JSON.stringify({ ...form, store_id: Number(storeId), quantity: Number(form.quantity || 1) }),
      })
      setForm({ asin: "", quantity: "1", product_name: "", notes: "" })
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
        <CardContent className="grid gap-3 md:grid-cols-[1fr_120px_1.2fr_1.2fr_auto]">
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
            <Label>Notes</Label>
            <Input value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
          </div>
          <div className="flex items-end">
            <Button disabled={!storeId || !form.asin.trim()} onClick={addManualInventory}>
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
                    <div className="text-xs text-muted-foreground">{item.source_odoo_order_name || item.notes || ""}</div>
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
  const groups = useMemo(() => {
    const grouped = new Map<string, OrderLine[]>()
    rows.forEach((row) => {
      const key = row.odoo_order_name || String(row.id)
      grouped.set(key, [...(grouped.get(key) || []), row])
    })
    return [...grouped.entries()]
  }, [rows])
  return (
    <div className="grid gap-5">
      <section className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-lg font-semibold tracking-tight">Missing Orders</h2>
          <p className="text-sm text-muted-foreground">Orders paused because one or more Amazon ASINs were unavailable during fulfilment.</p>
        </div>
        <Button variant="outline" onClick={onRefresh}>
          <RefreshCw className="size-4" />
          Refresh
        </Button>
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
                  <TableRow key={row.id}>
                    <TableCell>
                      <Checkbox
                        checked={selected.includes(row.id)}
                        onCheckedChange={(checked) => {
                          onSelectAll(false)
                          onSelected(checked ? Array.from(new Set([...selected, row.id])) : selected.filter((id) => id !== row.id))
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
                    <TableCell className="max-w-[360px]">
                      <Tooltip>
                        <TooltipTrigger className="block w-full truncate text-left text-destructive">{row.last_error}</TooltipTrigger>
                        <TooltipContent className="max-w-xl whitespace-normal break-words">{row.last_error}</TooltipContent>
                      </Tooltip>
                    </TableCell>
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
                      <Button size="sm" onClick={() => onAssign(row)} disabled={!storeId}>
                        Assign ASIN
                      </Button>
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

function BulkPage({
  groups,
  storeId,
  addressId,
  amazonAccountId,
  orderingEngine,
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
  groups: BulkGroup[]
  storeId: number
  addressId: number
  amazonAccountId: number
  orderingEngine: string
  page: number
  total: number
  onPage: (page: number) => void
  selected: string[]
  selectAll: boolean
  onSelected: (ids: string[]) => void
  onSelectAll: (value: boolean) => void
  onRefresh: () => Promise<void>
  onResult: (modal: ModalState) => void
  onNavigate: (page: string) => void
}) {
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
        <p className="text-sm text-muted-foreground">Same ASIN demand combined across pulled orders from the last 2 days.</p>
      </section>
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <ExportControls view="bulk" storeId={String(storeId || "")} columns={bulkExportColumns} selectedIds={selected} selectAll={selectAll} total={total} filters={{ days: 2 }} onSelectAll={() => onSelectAll(true)} onClear={() => { onSelectAll(false); onSelected([]) }} onResult={onResult} onDownloads={() => onNavigate("downloads")} />
        <PaginationControls page={page} total={total} onPage={onPage} />
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
                      onCheckedChange={(checked) => {
                        onSelectAll(false)
                        onSelected(checked ? Array.from(new Set([...selected, group.asin])) : selected.filter((id) => id !== group.asin))
                      }}
                    />
                  </TableCell>
                  <TableCell className="font-mono">
                    <a className="text-primary underline-offset-4 hover:underline" href={group.asin_url} target="_blank">{group.asin}</a>
                  </TableCell>
                  <TableCell>{group.quantity}</TableCell>
                  <TableCell>{group.order_names.join(", ")}</TableCell>
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
                      onCheckedChange={(checked) => {
                        onSelectAll(false)
                        onSelected(checked ? Array.from(new Set([...selected, row.id])) : selected.filter((id) => id !== row.id))
                      }}
                    />
                  </TableCell>
                  <TableCell>{row.odoo_order_url ? <a className="text-primary underline-offset-4 hover:underline" href={row.odoo_order_url} target="_blank">{row.odoo_order_name}</a> : row.odoo_order_name}</TableCell>
                  <TableCell className="font-mono"><a className="text-primary underline-offset-4 hover:underline" href={row.asin_url || `https://www.amazon.com/dp/${row.asin}`} target="_blank">{row.asin}</a></TableCell>
                  <TableCell className="max-w-[420px] truncate">{row.product_name}</TableCell>
                  <TableCell className="text-destructive">{Number(row.cost_review_loss || 0).toFixed(2)}</TableCell>
                  <TableCell className="max-w-[420px] truncate text-destructive">{row.last_error}</TableCell>
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

function ProfitLossPage({ storeId, onResult }: { stores: Store[]; storeId: string; onResult: (modal: ModalState) => void }) {
  const [data, setData] = useState<ProfitLossData | null>(null)
  const [period, setPeriod] = useState("monthly")
  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7))
  const [query, setQuery] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    const params = new URLSearchParams({ period, month, q: query })
    if (storeId) params.set("store_id", storeId)
    const next = await api<ProfitLossData>(`/api/profit-loss?${params.toString()}`)
    setData(next)
  }

  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Profit/Loss load failed", message: String(error) }))
  }, [storeId, period, month])

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
      await load()
    } catch (error) {
      onResult({ ok: false, title: "Shipping Import Failed", message: String(error) })
    } finally {
      setBusy(false)
    }
  }

  const summary = data?.summary || {}
  return (
    <div className="grid gap-3">
      <section className="grid gap-3 xl:grid-cols-6">
        {[
          ["Sales", formatMoney(Number(summary.odoo_order_value || 0))],
          ["Delivery Collected", formatMoney(Number(summary.collected_delivery || 0))],
          ["Discounts", formatMoney(Number(summary.order_discounts || 0))],
          ["Amazon Cost", formatMoney(Number(summary.amazon_order_value || 0))],
          ["Shipping + Fulfilment", formatMoney(Number(summary.shipping_fee || 0) + Number(summary.fulfilment_fee || 0))],
          ["Net Profit", formatMoney(Number(summary.net_profit || 0))],
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
        <CardHeader>
          <CardTitle>Profit / Loss Controls</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 lg:grid-cols-[160px_180px_1fr_auto] lg:items-end">
          <SelectField label="View" value={period} onChange={setPeriod}>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
          </SelectField>
          <div>
            <Label>Month</Label>
            <Input type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
          </div>
          <div>
            <Label>Search</Label>
            <SearchBox value={query} onChange={setQuery} placeholder="Odoo order or Amazon order" />
          </div>
          <Button variant="outline" onClick={load}>Apply</Button>
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

      <section className="grid gap-3 xl:grid-cols-[0.75fr_1.25fr]">
        <Card>
          <CardHeader><CardTitle>{period[0].toUpperCase() + period.slice(1)} Summary</CardTitle></CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader><TableRow><TableHead>Period</TableHead><TableHead>Orders</TableHead><TableHead>Net Profit</TableHead></TableRow></TableHeader>
              <TableBody>
                {(data?.period_rows || []).map((row) => (
                  <TableRow key={String(row.period)}>
                    <TableCell>{row.period}</TableCell>
                    <TableCell>{row.orders}</TableCell>
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
              {(data?.orders || []).map((row) => (
                <TableRow key={`${row.odoo_order_id}-${row.odoo_order_name}`}>
                  <TableCell className="font-medium">{row.odoo_order_name}</TableCell>
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
      </Card>
    </div>
  )
}

function AccountingPage({ storeId, onResult }: { storeId: string; onResult: (modal: ModalState) => void }) {
  const [data, setData] = useState<AccountingData>({ documents: [], summary: [] })
  const [query, setQuery] = useState("")
  const [documentType, setDocumentType] = useState("odoo")
  const [orderName, setOrderName] = useState("")
  const [countryCode, setCountryCode] = useState("")
  const [invoiceDate, setInvoiceDate] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [syncingOdoo, setSyncingOdoo] = useState(false)

  async function load() {
    const params = new URLSearchParams({ q: query })
    const next = await api<AccountingData>(`/api/accounting?${params.toString()}`)
    setData(next)
  }

  useEffect(() => {
    load().catch((error) => onResult({ ok: false, title: "Accounting load failed", message: String(error) }))
  }, [])

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
      await load()
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
      await load()
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
            <Button variant="outline" onClick={load}>Filter</Button>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow><TableHead>Order</TableHead><TableHead>Type</TableHead><TableHead>Tax Region</TableHead><TableHead>Country</TableHead><TableHead>File</TableHead><TableHead>Stored</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {data.documents.map((doc) => (
                <TableRow key={doc.id}>
                  <TableCell className="font-medium">{doc.odoo_order_name}</TableCell>
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
                  {job.error ? <div className="max-w-[420px] truncate text-xs text-destructive">{job.error}</div> : null}
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
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <CardTitle>Order Pull Jobs</CardTitle>
          <CardDescription>Odoo order pulls run in the background per store, so large imports do not lock the app.</CardDescription>
        </div>
        <div className="btn-list">
          <Button variant="outline" onClick={refresh}><RefreshCw className="size-4" />Refresh</Button>
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
              <TableHead>Inserted</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead>Error</TableHead>
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
                <TableCell>{Number(job.days || 0).toLocaleString()} day(s), limit {Number(job.limit_value || 0).toLocaleString()}</TableCell>
                <TableCell>{Number(job.inserted_records || 0).toLocaleString()}</TableCell>
                <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.updated_at || job.created_at)}</TableCell>
                <TableCell className="max-w-[420px] truncate text-xs text-destructive">{job.error || ""}</TableCell>
              </TableRow>
            ))}
            {!jobs.length && (
              <TableRow>
                <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">No pull jobs yet.</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
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
  useEffect(() => {
    setAsin(line.replacement_asin || "")
    setNote(line.replacement_note || "")
  }, [line])
  return (
    <Dialog open onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Assign Replacement ASIN</DialogTitle>
          <DialogDescription>{line.odoo_order_name} / missing {line.missing_asin || line.asin}</DialogDescription>
        </DialogHeader>
        <div className="form-fieldset grid gap-3">
          <TextField label="Replacement ASIN" value={asin} onChange={(value) => setAsin(value.toUpperCase())} />
          <TextField label="Internal note" value={note} onChange={setNote} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={async () => {
              try {
                const result = await api<{ ok: boolean; message: string }>(`/api/missing/lines/${line.id}/replacement`, {
                  method: "POST",
                  body: JSON.stringify({ store_id: storeId, asin, note }),
                })
                await onSaved(result.message)
              } catch (error) {
                onResult({ ok: false, title: "Replacement Save Failed", message: String(error) })
              }
            }}
          >
            Save Replacement
          </Button>
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
  const [savingServices, setSavingServices] = useState("")
  const [reindexProgress, setReindexProgress] = useState<ReindexProgress | null>(null)
  const [adminCode, setAdminCode] = useState("")
  const [adminCodeConfirm, setAdminCodeConfirm] = useState("")
  useEffect(() => {
    api<{ settings: ServiceSettings }>("/api/settings/services")
      .then((result) => setSettings(result.settings))
      .catch((error) => onResult({ ok: false, title: "Settings Load Failed", message: String(error) }))
    loadReindexProgress()
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!reindexProgress || ["running", "queued"].includes(reindexProgress.status)) {
        loadReindexProgress()
      }
    }, 2000)
    return () => window.clearInterval(timer)
  }, [reindexProgress?.status])

  async function loadReindexProgress() {
    try {
      const result = await api<{ ok: boolean; progress: ReindexProgress }>("/api/settings/typesense/reindex")
      setReindexProgress(result.progress)
    } catch {
      // Keep settings usable even if progress cannot be loaded.
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
    const result = await api<{ ok: boolean; message: string }>("/api/settings/backup/run", { method: "POST" })
    onResult({ ok: result.ok, title: "Backup", message: result.message })
  }
  async function syncAmazonOtp() {
    const result = await api<{ ok: boolean; message: string }>("/api/settings/amazon-otp/sync", { method: "POST" })
    onResult({ ok: result.ok, title: "Amazon OTP Email Sync", message: result.message })
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
          <CardDescription>Change the code used for the internal admin panel. The master recovery code is 1284.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid gap-3 md:grid-cols-2">
            <TextField label="New Admin Code" type="password" value={adminCode} onChange={setAdminCode} />
            <TextField label="Confirm Admin Code" type="password" value={adminCodeConfirm} onChange={setAdminCodeConfirm} />
          </div>
          <div className="card-actions btn-list justify-between">
            <p className="text-sm text-muted-foreground">Use 1284 in the login dialog if the saved admin code is forgotten.</p>
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
            </div>
            <div className="btn-list">
              <Button
                onClick={() => saveSettingsGroup("Database & Automation", [
                  "postgres_url",
                  "autosync_interval_minutes",
                  "auto_chrome_fulfil_interval_minutes",
                  "auto_chrome_fulfil_days",
                  "auto_chrome_fulfil_limit",
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
            <CardDescription>Backup destination and recurring backup interval.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 md:grid-cols-2">
              <TextField label="Backup Endpoint" value={settings.backup_s3_endpoint || ""} onChange={(value) => setSetting("backup_s3_endpoint", value)} />
              <TextField label="Backup Bucket" value={settings.backup_s3_bucket || ""} onChange={(value) => setSetting("backup_s3_bucket", value)} />
              <SelectField label="Backup Interval" value={settings.backup_interval_minutes || "0"} onChange={(value) => setSetting("backup_interval_minutes", value)}>
                {["0", "60", "180", "360", "720", "1440"].map((value) => <option key={value} value={value}>{intervalLabel(value)}</option>)}
              </SelectField>
            </div>
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
              <Button variant="outline" onClick={runBackup}>Run Backup Now</Button>
            </div>
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
            {urls.map((url) => (
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
          </TableBody>
      </Table>
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
            {rows.map((row) => (
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
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function TextField({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      <Input type={type} value={value || ""} onChange={(event) => onChange(event.target.value)} />
    </div>
  )
}

export default App
