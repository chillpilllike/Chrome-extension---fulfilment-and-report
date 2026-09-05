import { useEffect, useState, type ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import "./epost-workspace.css"

export type EpostWorkRow = {
  id: number; store_name: string; odoo_order_name: string; odoo_order_url: string
  tracking_code: string; tracking_url: string; status: string; last_update_at: string
  last_checked_at: string; destination: string; location: string; awb: string
  picking_name: string; amazon_order_id: string; amazon_order_url: string
  shipping_total: number; shipping_fee: number; fulfilment_fee: number; shipping_match_type: string
  refund_status?: string; refund_claimed_at?: string; refund_received_at?: string
  workflow_queue?: string; workflow_label?: string; suggested_owner?: string; next_action?: string
  days_since_update?: number | null; days_since_import?: number | null
}

const queues = [
  ["attention", "Needs attention", "Start here", "Review unresolved tracking and delivery issues."],
  ["unscanned", "Not checked yet", "Tracking team", "A code is imported, but no carrier check is recorded. Check it before deciding whether it has moved."],
  ["lookup_error", "Tracking not found", "Fulfilment team", "The carrier could not locate the code. Verify the number, carrier and handover proof. A lookup error is not a lost parcel."],
  ["awaiting_first_scan", "Awaiting carrier scan", "Fulfilment team", "Only a blank page, label, electronic record or transit-to-processing-center message exists. Confirm physical handover."],
  ["stalled", "Movement stalled", "Carrier support", "A physical-movement status has no update beyond your threshold. Investigate with the carrier; do not automatically promise a refund."],
  ["carrier_exception", "Delivery exceptions", "Customer support", "Review customs, KYC, address, failed delivery, return and damage messages. Follow the specific carrier instruction."],
  ["confirmed_lost", "Carrier-reported loss", "Customer support", "The carrier message explicitly reports loss. Review evidence and obtain refund or replacement approval."],
  ["needs_review", "Status needs review", "Tracking team", "The message does not reliably establish physical movement or delivery. Read the carrier record."],
  ["active", "All not delivered", "Monitor", "All imported shipments without a positive delivery status, including records with no scans."],
  ["in_transit", "In transit", "Monitor", "Physical-movement status, below the inactivity threshold or without a usable event date."],
  ["delivered", "Delivered", "Complete", "Carrier reports delivery. Use After-order care if the customer disputes receipt."],
  ["refund_claimed", "Claims awaiting payment", "Finance", "A team member recorded a carrier refund claim. Check the carrier response and record payment only when received."],
  ["refund_received", "Refunds received", "Finance", "Recorded carrier refunds. This is separate from any customer refund or replacement."],
  ["all", "All shipments", "Overview", "Every imported ePost shipment in the selected store. Queue counts cover the store, not just this page or search."],
] as const

type Props = {
  rows: EpostWorkRow[]; total: number; page: number; pageSize: number; storeId: string
  summary: Record<string, number> | null; queue: string; query: string; staleDays: string; loading: boolean
  selected: number[]; selectAll: boolean; syncDays: string
  onQueue: (value: string) => void; onQuery: (value: string) => void; onStaleDays: (value: string) => void
  onSyncDays: (value: string) => void; onSync: () => void; onRefresh: () => void; onPage: (value: number) => void
  onSelected: (ids: number[]) => void; onSelectAll: (value: boolean) => void
  onRefund: (row: EpostWorkRow, status: "claimed" | "received" | "clear") => void
  onCopy: (row: EpostWorkRow) => void; onCopyCodes: (rows: EpostWorkRow[]) => void
  onNavigate: (page: string, order?: string) => void; formatMoney: (value: number) => string
  exports: ReactNode
}

export function EpostWorkspace(p: Props) {
  const [detailId, setDetailId] = useState<number | null>(null)
  const [showTools, setShowTools] = useState(false)
  const [showRules, setShowRules] = useState(false)
  const [draftQuery, setDraftQuery] = useState(p.query)
  const detail = p.rows.find(row => row.id === detailId)
  const current = queues.find(q => q[0] === p.queue) || queues[0]
  const selectedRows = p.rows.filter(row => p.selected.includes(row.id))
  const canCopyBatch = selectedRows.length > 0 && selectedRows.length <= 25 && selectedRows.length === p.selected.length && !p.selectAll && !p.loading
  useEffect(() => { setDraftQuery(p.query) }, [p.query])
  useEffect(() => { setDetailId(null) }, [p.queue, p.query, p.page, p.storeId])
  const count = (key: string) => p.summary ? (p.summary[key] || 0).toLocaleString() : "—"
  const toggleRow = (row: EpostWorkRow, checked: boolean) => {
    p.onSelectAll(false)
    p.onSelected(checked ? [...new Set([...p.selected, row.id])] : p.selected.filter(id => id !== row.id))
  }
  const carrierLink = (row: EpostWorkRow) => row.tracking_url || `https://epgtrack.com/${encodeURIComponent(row.tracking_code)}`
  return <div className="epost-workspace" aria-busy={p.loading}>
    <header className="epost-heading">
      <div><span className="epost-eyebrow">Shipment operations</span><h2>ePost Global</h2><p>Choose a work queue. Verify the carrier evidence. Take the next action.</p></div>
      <div className="epost-heading-actions">
        <Button variant="outline" onClick={() => setShowRules(!showRules)} aria-expanded={showRules}>How queues work</Button>
        <Button variant="outline" onClick={p.onRefresh} disabled={p.loading}>Reload saved results</Button>
        <Button onClick={() => setShowTools(!showTools)} aria-expanded={showTools}>Import & carrier checks</Button>
      </div>
    </header>

    {showRules && <section className="epost-notice"><strong>Evidence before action</strong><p>“Not delivered” is not the same as “lost”. A label, lookup error or old import date does not prove carrier possession. Movement stalls only when a physical-movement message has an old event date. Carrier-reported loss requires an explicit loss message. Queue owners are suggested teams, not assigned staff.</p><p>Carrier event times are shown as supplied. Imported age is not the fulfilment date. Reloading this page reads saved results; it does not scan the carrier.</p></section>}
    {showTools && <section className="epost-tools" aria-label="Import and carrier checks">
      <div><span className="epost-step">1 · Import codes</span><h3>Bring in Odoo fulfilments</h3><p>Imports EPG codes from completed pickings for the selected store. This is not a direct Shopify import.</p><div className="epost-inline"><label htmlFor="epost-import-days">Past days</label><Input id="epost-import-days" type="number" min={1} max={30} value={p.syncDays} onChange={e => p.onSyncDays(e.target.value)} /><Button disabled={p.loading || !p.storeId || Number(p.syncDays) < 1 || Number(p.syncDays) > 30} onClick={p.onSync}>Import codes</Button></div>{!p.storeId && <small>Select a store in the portal header to import.</small>}</div>
      <div><span className="epost-step">2 · Check the carrier</span><h3>Scan codes in ePost</h3><p>Select up to 25 visible shipments, copy their codes, and use the ePost tracking extension in the carrier portal to scan and save results.</p><div className="epost-inline"><Button variant="outline" disabled={!canCopyBatch} onClick={() => p.onCopyCodes(selectedRows)}>Copy selected codes ({selectedRows.length}/25)</Button><a className="epost-link-button" href="https://portal.epgshipping.com/ParcelTracker" target="_blank" rel="noreferrer">Open carrier portal ↗</a></div><small>All-matching export selection does not run a carrier scan. Reload saved results after the extension finishes.</small></div>
    </section>}

    <div className="epost-layout">
      <aside className="epost-queues" aria-label="Shipment work queues">
        <div className="epost-queue-title">Work queues <span>Store totals</span></div>
        {queues.map(([key, label], index) => <div key={key}>
          {index === 8 && <div className="epost-queue-divider">Monitor</div>}
          {index === 11 && <div className="epost-queue-divider">Carrier refunds</div>}
          <button className={`epost-queue ${p.queue === key ? "is-active" : ""}`} aria-pressed={p.queue === key} disabled={p.loading} onClick={() => p.onQueue(key)}><span>{label}</span><strong>{count(key)}</strong></button>
        </div>)}
        <div className="epost-related"><h3>Related work</h3><button onClick={() => p.onNavigate("after-order-care")}>Customer follow-up & approvals ↗</button><button onClick={() => p.onNavigate("fulfilment-pending")}>Orders awaiting fulfilment ↗</button><button onClick={() => p.onNavigate("duplicate-tracking")}>Duplicate tracking & charges ↗</button><button onClick={() => p.onNavigate("downloads")}>Export downloads ↗</button></div>
      </aside>
      <section className="epost-main" aria-label="Shipment results">
        <section className="epost-queue-context"><div><span className="epost-eyebrow">{current[2]}</span><h3>{current[1]}</h3><p>{current[3]}</p></div><span className="epost-result-count">{p.loading ? "Loading…" : `${p.total.toLocaleString()} matching`}</span></section>
        <form className="epost-filters" onSubmit={e => { e.preventDefault(); p.onQuery(draftQuery.trim()) }}>
          <div className="epost-search"><label htmlFor="epost-search">Find an order or shipment</label><Input id="epost-search" value={draftQuery} onChange={e => setDraftQuery(e.target.value)} placeholder="Order, tracking code, destination, AWB…" /></div>
          <div><label htmlFor="epost-threshold">Stalled after</label><select id="epost-threshold" value={p.staleDays} onChange={e => p.onStaleDays(e.target.value)}><option value="7">7 days without movement</option><option value="10">10 days without movement</option><option value="14">14 days without movement</option><option value="21">21 days without movement</option><option value="30">30 days without movement</option></select></div>
          <Button type="submit" variant="outline" disabled={p.loading}>Search</Button><Button type="button" variant="ghost" disabled={p.loading} onClick={() => { setDraftQuery(""); p.onQuery(""); p.onStaleDays("10"); p.onQueue("attention") }}>Reset filters</Button>
        </form>
        <div className="epost-selection"><span>{p.selectAll ? `All ${p.total} matching shipments selected for export` : `${p.selected.length} selected`} {p.query && <span> · Search: “{p.query}”</span>}</span><div><Button size="sm" variant="ghost" onClick={() => { p.onSelected([]); p.onSelectAll(false) }}>Clear selection</Button><Button size="sm" variant="outline" disabled={!canCopyBatch} onClick={() => p.onCopyCodes(selectedRows)}>Copy codes (up to 25)</Button></div></div>
        {p.loading && <div role="status" className="epost-loading">Loading saved shipment records…</div>}
        <Table className="epost-work-table"><TableHeader><TableRow>
          <TableHead><Checkbox aria-label="Select visible shipments" checked={p.rows.length > 0 && p.rows.every(row => p.selected.includes(row.id))} disabled={p.loading} onCheckedChange={checked => { p.onSelectAll(false); p.onSelected(checked ? [...new Set([...p.selected, ...p.rows.map(row => row.id)])] : p.selected.filter(id => !p.rows.some(row => row.id === id))) }} /></TableHead>
          <TableHead>Order / shipment</TableHead><TableHead>Carrier evidence</TableHead><TableHead>Activity</TableHead><TableHead>Next action</TableHead><TableHead>Manage</TableHead>
        </TableRow></TableHeader><TableBody>
          {p.rows.map(row => <TableRow key={row.id}>
            <TableCell><Checkbox aria-label={`Select ${row.odoo_order_name || row.tracking_code}`} checked={p.selected.includes(row.id)} disabled={p.loading} onCheckedChange={checked => toggleRow(row, Boolean(checked))} /></TableCell>
            <TableCell><a className="epost-order" href={row.odoo_order_url || carrierLink(row)} target="_blank" rel="noreferrer">{row.odoo_order_name || "Order not linked"} ↗</a><a className="epost-code" href={carrierLink(row)} target="_blank" rel="noreferrer">{row.tracking_code}</a><small>{row.store_name} · {row.destination || "Destination unknown"}</small></TableCell>
            <TableCell><span className={`epost-status ep-${row.workflow_queue || "needs_review"}`}>{row.workflow_label || "Status needs review"}</span><p>{row.status || "No carrier event recorded"}</p>{row.refund_status && <small>Carrier refund: {row.refund_status}</small>}</TableCell>
            <TableCell><strong>{row.last_update_at ? (row.days_since_update != null ? `${row.days_since_update}d since event` : "Event date unverified") : "No event date"}</strong><small>{row.last_update_at || "No dated carrier scan"}</small><small>Checked: {row.last_checked_at ? row.last_checked_at.replace("T", " ").replace(/\.\d+/, "") : "Never"}</small>{!row.last_update_at && row.days_since_import != null && <small>Imported {row.days_since_import}d ago (not fulfilment)</small>}</TableCell>
            <TableCell><strong>{row.suggested_owner || "Tracking team"}</strong><p>{row.next_action || "Review the carrier record."}</p></TableCell>
            <TableCell><Button size="sm" variant="outline" disabled={p.loading} onClick={() => setDetailId(row.id)}>Review shipment</Button></TableCell>
          </TableRow>)}
          {!p.rows.length && <TableRow><TableCell colSpan={6}><div className="epost-empty"><h3>{p.loading ? "Loading shipments…" : "No shipments in this view"}</h3><p>{p.query ? "Try a different order or tracking code, or clear your search." : "Choose another queue or import completed fulfilments for your selected store."}</p></div></TableCell></TableRow>}
        </TableBody></Table>
        <footer className="epost-table-footer"><span>{p.total ? `${(p.page - 1) * p.pageSize + 1}–${Math.min(p.page * p.pageSize, p.total)} of ${p.total}` : "0 results"}</span><div><Button variant="outline" size="sm" disabled={p.loading || p.page <= 1} onClick={() => p.onPage(p.page - 1)}>Previous</Button><span>Page {p.page}</span><Button variant="outline" size="sm" disabled={p.loading || p.page * p.pageSize >= p.total} onClick={() => p.onPage(p.page + 1)}>Next</Button></div></footer>
        <details className="epost-export"><summary>Export this queue or selected shipments</summary><div>{p.exports}</div></details>
      </section>
    </div>
    <Dialog open={Boolean(detail)} onOpenChange={open => { if (!open) setDetailId(null) }}><DialogContent className="epost-detail"><DialogHeader><DialogTitle>{detail?.odoo_order_name} · Shipment review</DialogTitle><DialogDescription>Review carrier evidence before recording a claim or requesting a customer action.</DialogDescription></DialogHeader>
      {detail && <><section><span className={`epost-status ep-${detail.workflow_queue}`}>{detail.workflow_label}</span><h3>{detail.status || "No carrier event recorded"}</h3><p>{detail.next_action}</p><dl><dt>Tracking code</dt><dd>{detail.tracking_code}</dd><dt>Last carrier event</dt><dd>{detail.last_update_at || "No dated event"}</dd><dt>Location / destination</dt><dd>{detail.location || "—"} / {detail.destination || "—"}</dd><dt>Picking / AWB</dt><dd>{detail.picking_name || "—"} / {detail.awb || "—"}</dd></dl><div className="epost-inline"><a className="epost-link-button" href={carrierLink(detail)} target="_blank" rel="noreferrer">Open carrier tracking ↗</a>{detail.odoo_order_url && <a className="epost-link-button" href={detail.odoo_order_url} target="_blank" rel="noreferrer">Open Odoo order ↗</a>}{detail.amazon_order_url && <a className="epost-link-button" href={detail.amazon_order_url} target="_blank" rel="noreferrer">Amazon {detail.amazon_order_id} ↗</a>}<Button variant="outline" onClick={() => p.onCopy(detail)}>Copy investigation details</Button></div></section>
      <section><h3>Customer follow-up</h3><p>Use After-order care to review communication, customer decisions and approved refunds or replacements. No message is sent from this page.</p><Button variant="outline" onClick={() => p.onNavigate("after-order-care", detail.odoo_order_name)}>Open After-order care</Button><small>Opens the existing care queue filtered to {detail.odoo_order_name}.</small></section>
      <section><h3>Carrier refund record</h3><p>Shipping charges: {p.formatMoney(Number(detail.shipping_total || 0))} · Shipping {p.formatMoney(Number(detail.shipping_fee || 0))} · Fulfilment {p.formatMoney(Number(detail.fulfilment_fee || 0))}</p><small>Charge match: {detail.shipping_match_type || "Not synced"}. Recording a claim does not submit it to the carrier or refund the customer.</small><p>Recorded status: <strong>{detail.refund_status || "No claim recorded"}</strong></p>{detail.refund_claimed_at && <small>Claim recorded: {detail.refund_claimed_at}</small>}{detail.refund_received_at && <small>Payment recorded: {detail.refund_received_at}</small>}<div className="epost-inline">{!detail.refund_status && <Button variant="outline" disabled={p.loading} onClick={() => p.onRefund(detail, "claimed")}>Record submitted claim</Button>}{detail.refund_status === "claimed" && <Button disabled={p.loading} onClick={() => p.onRefund(detail, "received")}>Record refund received</Button>}{detail.refund_status && <Button variant="ghost" disabled={p.loading} onClick={() => p.onRefund(detail, "clear")}>Clear refund record</Button>}</div></section></>}
    </DialogContent></Dialog>
  </div>
}
