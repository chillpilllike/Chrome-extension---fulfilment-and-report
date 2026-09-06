import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { TeamWorkspaceHeader } from "./TeamWorkspace"
import "./support-workspace.css"

type Relation = [number, string] | false
type Order = { id: number; name: string; state: string; date_order: string; amount_total: number; amount_tax: number; currency_id: Relation; partner_id: Relation; odoo_url?: string; items?: { id: number; name: string; product_uom_qty: number; price_total: number }[]; items_truncated?: boolean }
type Detail = { order: Order; customer_preview: { reference: string; reply: string; status: string }; observed_at: string; warnings: string[]; internal_fulfilment: { id: number; asin: string; quantity: number; state: string; ordered_at: string; amazon_order_id: string; amazon_status: string }[]; internal_truncated: boolean }
type Timeline = { events: { id: number; date: string; author_id: Relation; body: string; subject: string }[]; has_more: boolean }
type Props = { api: <T>(path: string, options?: RequestInit) => Promise<T> }
const label = (v: Relation) => v ? v[1] : "—"
const status = (v: string) => ({ draft: "Not confirmed", sent: "Not confirmed", sale: "Confirmed", done: "Confirmed", cancel: "Cancelled" }[v] || v)
const safeLink = (url?: string) => { try { const u = new URL(url || ""); return u.protocol === "https:" ? u.href : undefined } catch { return undefined } }

export function SupportWorkspace({ api }: Props) {
  const [stores, setStores] = useState<{ id: number; name: string }[]>([])
  const [store, setStore] = useState("8")
  const [websites, setWebsites] = useState<{ id: number; name: string; domain: string }[]>([])
  const [website, setWebsite] = useState("")
  const [query, setQuery] = useState(""); const [draft, setDraft] = useState("")
  const [page, setPage] = useState(1); const [result, setResult] = useState<{ orders: Order[]; has_more: boolean } | null>(null)
  const [selected, setSelected] = useState<number | null>(null); const [detail, setDetail] = useState<Detail | null>(null)
  const [history, setHistory] = useState<Timeline | null>(null); const [historyPage, setHistoryPage] = useState(1)
  const [error, setError] = useState(""); const [detailError, setDetailError] = useState(""); const [historyError, setHistoryError] = useState("")
  const [loading, setLoading] = useState(false); const [refresh, setRefresh] = useState(0)
  useEffect(() => { const c = new AbortController(); api<{ stores: typeof stores }>("/api/support/status", { signal: c.signal }).then(r => { if (!c.signal.aborted) setStores(r.stores) }).catch(() => { if (!c.signal.aborted) setError("Staff sign-in is required to load support data.") }); return () => c.abort() }, [api])
  useEffect(() => {
    const c = new AbortController(); setWebsites([]); setWebsite(""); setSelected(null); setResult(null); setError("")
    api<{ websites: typeof websites }>(`/api/support/websites?store_id=${store}`, { signal: c.signal }).then(r => { if (!c.signal.aborted) { setWebsites(r.websites); if (r.websites.length === 1) setWebsite(String(r.websites[0].id)) } }).catch(() => { if (!c.signal.aborted) setError("Website lookup is unavailable. Try refreshing.") })
    return () => c.abort()
  }, [store, api])
  useEffect(() => {
    const c = new AbortController(); setResult(null); setSelected(null); setError("")
    if (!website) return () => c.abort()
    setLoading(true)
    const params = new URLSearchParams({ store_id: store, website_id: website, q: query, page: String(page) })
    api<{ orders: Order[]; has_more: boolean }>(`/api/support/orders?${params}`, { signal: c.signal }).then(r => { if (!c.signal.aborted) setResult(r) }).catch(() => { if (!c.signal.aborted) setError("Order lookup is unavailable. This does not mean there are no matching orders.") }).finally(() => { if (!c.signal.aborted) setLoading(false) })
    return () => c.abort()
  }, [store, website, query, page, refresh, api])
  useEffect(() => {
    const c = new AbortController(); setDetail(null); setDetailError("")
    if (!selected || !website) return () => c.abort()
    api<Detail>(`/api/support/orders/${selected}?store_id=${store}&website_id=${website}`, { signal: c.signal }).then(r => { if (!c.signal.aborted) setDetail(r) }).catch(() => { if (!c.signal.aborted) setDetailError("Order details could not be loaded. Refresh orders to retry.") })
    return () => c.abort()
  }, [selected, store, website, api])
  useEffect(() => {
    const c = new AbortController(); setHistory(null); setHistoryError("")
    if (!selected || !website) return () => c.abort()
    api<Timeline>(`/api/support/orders/${selected}/timeline?store_id=${store}&website_id=${website}&page=${historyPage}`, { signal: c.signal }).then(r => { if (!c.signal.aborted) setHistory(r) }).catch(() => { if (!c.signal.aborted) setHistoryError("Odoo history is unavailable.") })
    return () => c.abort()
  }, [selected, store, website, historyPage, api])
  return <div className="support-workspace">
    <TeamWorkspaceHeader title="Customer support" description="Secretgreen pilot · Review the original order, check internal evidence, and prepare a customer-safe reply." current="support"><a href="https://libredesk.185.194.236.161.sslip.io" target="_blank" rel="noreferrer">Open LibreDesk ↗</a></TeamWorkspaceHeader>
    <div className="support-mode"><strong>Agent preview</strong><span>Customer chat and automatic sending are not enabled. Order selection here does not change a LibreDesk conversation.</span></div>
    <form className="support-filters" onSubmit={e => { e.preventDefault(); setPage(1); setQuery(draft) }}>
      <label>Odoo connection<select value={store} onChange={e => { setStore(e.target.value); setWebsite(""); setDetail(null); setHistory(null); setPage(1) }}>{stores.length ? stores.map(s => <option key={s.id} value={s.id}>{s.name}</option>) : <option value="8">Secretgreen</option>}</select></label>
      <label>Website<select value={website} onChange={e => { setWebsite(e.target.value); setDetail(null); setHistory(null); setPage(1) }}><option value="">Choose a website</option>{websites.map(s => <option key={s.id} value={s.id}>{s.name} · {s.domain}</option>)}</select></label>
      <label>Order reference or customer email<Input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Search this website" maxLength={120} /></label><Button type="submit" disabled={!website}>Search</Button><Button type="button" variant="outline" onClick={() => setRefresh(x => x+1)}>Refresh orders</Button>
    </form>
    {error && <p role="alert">{error}</p>}
    <div className="support-columns"><section className="support-panel"><h3>Orders <small>Including unconfirmed</small></h3>{loading && <p role="status">Loading orders…</p>}{result?.orders.length === 0 && <p>No matching orders on this website.</p>}
      <div className="support-orders">{result?.orders.map(order => <button key={order.id} aria-pressed={selected === order.id} onClick={() => { if (selected !== order.id) { setDetail(null); setHistory(null); setSelected(order.id); setHistoryPage(1) } }}><span><strong>{order.name}</strong><small>{label(order.partner_id)}</small></span><span>{status(order.state)}<small>{order.amount_total} {label(order.currency_id)}</small></span></button>)}</div>
      <div className="support-pages"><Button variant="outline" disabled={page === 1 || loading} onClick={() => setPage(p => p-1)}>Previous</Button><span>Page {page}</span><Button variant="outline" disabled={!result?.has_more || loading} onClick={() => setPage(p => p+1)}>Next</Button></div>
    </section><section className="support-panel" aria-live="polite">
      {!selected && <p>Select an order to review its details.</p>}{detailError && <p role="alert">{detailError}</p>}{selected && !detail && !detailError && <p>Loading order details…</p>}
      {detail && <><div className="support-heading"><h3>{detail.order.name}</h3>{safeLink(detail.order.odoo_url) && <a href={safeLink(detail.order.odoo_url)} target="_blank" rel="noreferrer">Open in Odoo ↗</a>}</div><p>{label(detail.order.partner_id)} · {status(detail.order.state)} · {detail.order.date_order}</p><p><strong>{detail.order.amount_total} {label(detail.order.currency_id)}</strong> · Tax {detail.order.amount_tax}</p>
        <div className="support-preview"><h4>Customer reply preview</h4><p>{detail.customer_preview.reply}</p><small>Only this preview is customer-safe. The remaining panels contain internal information.</small></div>
        {detail.warnings.map(w => <p key={w} role="alert">{w}</p>)}
        <h4>Original order items</h4><div className="support-table"><table><thead><tr><th>Item</th><th>Quantity</th><th>Total</th></tr></thead><tbody>{detail.order.items?.map(i => <tr key={i.id}><td>{i.name}</td><td>{i.product_uom_qty}</td><td>{i.price_total}</td></tr>)}</tbody></table></div>{detail.order.items_truncated && <p>Showing the first 500 lines. Open Odoo for the complete order.</p>}
        <details><summary>Internal fulfilment · staff only</summary>{!detail.internal_fulfilment.length && <p>No imported procurement lines available.</p>}{detail.internal_fulfilment.map(i => <p key={i.id}>{i.asin} · Quantity {i.quantity} · {i.state} · Purchased {i.ordered_at || "—"} · Amazon {i.amazon_order_id || "—"} · {i.amazon_status || "—"}</p>)}{detail.internal_truncated && <p>Showing the first 500 procurement lines.</p>}</details>
        <h4>Odoo communications · staff only</h4><p className="support-hint">Original history, newest first. Internal notes remain internal. Nothing is replayed or sent.</p>{historyError && <p role="alert">{historyError}</p>}{history?.events.map(event => <article className="support-event" key={event.id}><small>{event.date} · {label(event.author_id)}</small><strong>{event.subject || "Odoo message"}</strong><p>{event.body}</p></article>)}{history?.events.length === 0 && <p>No Odoo messages found.</p>}
        <div className="support-pages"><Button variant="outline" disabled={historyPage === 1} onClick={() => setHistoryPage(p => p-1)}>Newer</Button><span>History page {historyPage}</span><Button variant="outline" disabled={!history?.has_more} onClick={() => setHistoryPage(p => p+1)}>Older</Button></div><small>Order checked {new Date(detail.observed_at).toLocaleString()}</small>
      </>}
    </section></div>
  </div>
}
