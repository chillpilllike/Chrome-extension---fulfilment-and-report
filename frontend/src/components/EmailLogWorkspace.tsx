import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import "./epost-workspace.css"
import "./email-log.css"

type Email = {
  id: number; case_id: number; store_id: number; store_name: string; website_id?: number
  odoo_order_name: string; subject: string; recipient: string; sender: string
  provider: string; provider_message_id?: string; status: string; status_label: string
  test_mode: boolean; attempt_count: number; template_kind?: string
  last_error?: string; created_at: string; updated_at: string; html_preview?: string
  can_retry: boolean; retry_block_reason: string
}
type Attempt = { attempt_number: number; status: string; error?: string; provider_message_id?: string; created_at: string; updated_at: string }
type Result = { rows: Email[]; total: number; summary: Record<string, number>; test_mode: boolean; test_recipient: string }
type Props = { storeId: string; api: <T>(path: string, options?: RequestInit) => Promise<T>; onResult: (result: { ok: boolean; title: string; message: string }) => void; onNavigate: (page: string, order?: string) => void }

const queues = [
  ["all", "All emails", "Every saved email in the selected store, including test sends and previews."],
  ["attention", "Needs attention", "Confirmed failures and uncertain sends. Read the error before taking action."],
  ["failed", "Failed", "Requests that failed before sending or were explicitly rejected. Eligible records can be retried individually."],
  ["retrying", "Sending & retrying", "An attempt is in progress. Do not send another copy while its outcome is unknown."],
  ["sent", "Sent", "Accepted by the email provider. This is not proof of inbox delivery or that the customer read it."],
  ["uncertain", "Check delivery", "A timeout or incomplete response left acceptance uncertain. Check the provider; blind retries are disabled."],
  ["preview", "Preview only", "Saved previews. No email was sent and these records cannot be retried."],
] as const
const formatDate = (value: string) => value ? new Date(value).toLocaleString() : "—"
const signal = (status: string) => status === "failed" ? "failed" : ["sent", "sent_test"].includes(status) ? "sent" : ["sending", "retrying"].includes(status) ? "retrying" : status === "delivery_unknown" ? "uncertain" : "preview"
function Status({ row }: { row: Pick<Email, "status" | "status_label"> }) {
  return <span className={`email-signal signal-${signal(row.status)}`}><span aria-hidden="true" />{row.status_label}</span>
}

export function EmailLogWorkspace({ storeId, api, onResult, onNavigate }: Props) {
  const [queue, setQueue] = useState("all")
  const [mode, setMode] = useState("all")
  const [query, setQuery] = useState("")
  const [draft, setDraft] = useState("")
  const [from, setFrom] = useState("")
  const [to, setTo] = useState("")
  const [page, setPage] = useState(1)
  const [tick, setTick] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [data, setData] = useState<Result | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [detail, setDetail] = useState<{ row: Email; attempts: Attempt[] } | null>(null)
  const [detailError, setDetailError] = useState("")
  const [retryId, setRetryId] = useState<number | null>(null)
  const [retryTarget, setRetryTarget] = useState<Email | null>(null)
  const [rules, setRules] = useState(false)
  const pageSize = 30
  const current = queues.find(value => value[0] === queue) || queues[0]
  useEffect(() => { setPage(1); setDetailId(null); setData(null) }, [storeId])
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError("")
    const params = new URLSearchParams({ page: String(page), per_page: String(pageSize), status: queue, mode, q: query, date_from: from, date_to: to })
    if (storeId) params.set("store_id", storeId)
    api<Result>(`/api/after-order/emails?${params}`, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData(result)
    }).catch(reason => { if (!controller.signal.aborted) { setError(String(reason)); setData(null) } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [api, storeId, page, queue, mode, query, from, to, tick])
  useEffect(() => {
    setDetail(null); setDetailError("")
    if (detailId === null) return
    const controller = new AbortController()
    api<{ row: Email; attempts: Attempt[] }>(`/api/after-order/emails/${detailId}${storeId ? `?store_id=${encodeURIComponent(storeId)}` : ""}`, { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setDetail(result) })
      .catch(reason => { if (!controller.signal.aborted) setDetailError(String(reason)) })
    return () => controller.abort()
  }, [api, detailId, storeId, tick])
  useEffect(() => {
    if (!data?.rows.some(row => ["sending", "retrying"].includes(row.status))) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") setTick(value => value + 1)
    }, 10000)
    return () => window.clearInterval(timer)
  }, [data])
  async function retry(row: Email) {
    if (!row.can_retry || retryId !== null) return
    setRetryId(row.id)
    try {
      const result = await api<{ ok: boolean; message: string }>(`/api/after-order/emails/${row.id}/retry`, { method: "POST" })
      onResult({ ok: result.ok, title: result.ok ? "Retry sent" : "Retry needs attention", message: result.message })
    } catch (reason) { onResult({ ok: false, title: "Retry stopped", message: String(reason) }) }
    finally { setRetryId(null); setRetryTarget(null); setTick(value => value + 1) }
  }
  const total = data?.total || 0
  const change = (setter: (value: string) => void, value: string) => { setter(value); setPage(1) }
  let preview = ""
  if (detail?.row.html_preview) {
    const document = new DOMParser().parseFromString(detail.row.html_preview, "text/html")
    document.querySelectorAll("script,base,form,iframe,object,embed,meta[http-equiv='refresh']").forEach(element => element.remove())
    document.querySelectorAll("a,area").forEach(element => { element.removeAttribute("href"); element.removeAttribute("target"); element.setAttribute("tabindex", "-1") })
    preview = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: data:; style-src 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; form-action 'none'; base-uri 'none'">${document.documentElement.outerHTML}`
  }
  return <div className="epost-workspace email-log-workspace" aria-busy={loading}>
    <header className="epost-heading"><div><span className="epost-eyebrow">Customer communications</span><h2>Email log</h2><p>See what was sent. Understand failures. Retry with confidence.</p></div>
      <div className="epost-heading-actions"><Button variant="outline" onClick={() => setRules(!rules)} aria-expanded={rules}>How statuses work</Button><Button variant="outline" onClick={() => onNavigate("after-order-care")}>After-order care</Button><Button onClick={() => setTick(value => value + 1)} disabled={loading}>Refresh log</Button></div>
    </header>
    {rules && <section className="epost-notice"><strong>Provider acceptance is not inbox delivery</strong><p>Sent means the provider returned a message ID. Read receipts and inbox delivery are not inferred. Retry is available only for confirmed failures with a saved payload and current order context, up to five attempts. Uncertain sends, sent messages and legacy records cannot be blindly retried.</p><p>In test mode, only stored test messages to the configured test address can be retried. No bulk retry runs from this page.</p></section>}
    <div className="epost-layout">
      <aside className="epost-queues" aria-label="Email work queues"><div className="epost-queue-title">Work queues <span>Filtered totals</span></div>
        {queues.map(([key, label]) => <button key={key} className={`epost-queue ${queue === key ? "is-active" : ""}`} aria-pressed={queue === key} onClick={() => change(setQueue, key)}><span>{label}</span><strong>{data ? (data.summary[key] || 0).toLocaleString() : "—"}</strong></button>)}
        <div className="epost-related"><h3>Related work</h3><button onClick={() => onNavigate("after-order-care")}>Customer follow-up & approvals ↗</button><button onClick={() => onNavigate("epost")}>ePost Global tracking ↗</button></div>
      </aside>
      <section className="epost-main" aria-label="Email results">
        <section className="epost-queue-context"><div><span className="epost-step">Communication history</span><h3>{current[1]}</h3><p>{current[2]}</p></div><div className="epost-result-count">{loading ? "Loading…" : `${total.toLocaleString()} email${total === 1 ? "" : "s"}`}</div></section>
        <form className="epost-filters" onSubmit={event => { event.preventDefault(); change(setQuery, draft.trim()) }}>
          <div className="epost-search"><label htmlFor="email-search">Find an email</label><Input id="email-search" value={draft} onChange={event => setDraft(event.target.value)} placeholder="Order, email address, website or subject" /></div>
          <div><label htmlFor="email-mode">Mode</label><select id="email-mode" value={mode} onChange={event => change(setMode, event.target.value)}><option value="all">Test & live</option><option value="test">Test only</option><option value="live">Live only</option></select></div>
          <div><label htmlFor="email-from">From date</label><Input id="email-from" type="date" value={from} onChange={event => change(setFrom, event.target.value)} /></div>
          <div><label htmlFor="email-to">To date</label><Input id="email-to" type="date" value={to} onChange={event => change(setTo, event.target.value)} /></div><Button type="submit">Search</Button>
        </form>
        <div className="epost-selection"><span>{data?.test_mode ? `Test mode · retries only to ${data.test_recipient}` : "Live retries recheck the order, recipient and website."}</span><span>Dates shown in your local time · filters use saved UTC dates</span></div>
        {error && <div className="email-log-error" role="alert">{error}<Button variant="outline" onClick={() => setTick(value => value + 1)}>Reload</Button></div>}
        {loading && <div className="epost-loading" role="status">Loading email history…</div>}
        <Table className="email-log-table"><TableHeader><TableRow><TableHead>Email / order</TableHead><TableHead>Recipient / website</TableHead><TableHead>Status</TableHead><TableHead>Last activity</TableHead><TableHead>Actions</TableHead></TableRow></TableHeader><TableBody>
          {!loading && !error && !data?.rows.length && <TableRow><TableCell colSpan={5}><div className="epost-empty"><strong>No emails in this queue</strong><p>Change your filters or send a test email from After-order care.</p></div></TableCell></TableRow>}
          {data?.rows.map(row => <TableRow key={row.id}><TableCell><button className="email-order" onClick={() => onNavigate("after-order-care", row.odoo_order_name)}>{row.odoo_order_name || "Order unavailable"} ↗</button><strong className="email-subject">{row.subject || "Untitled email"}</strong><small>{(row.template_kind || "Email").replaceAll("_", " ")}</small></TableCell>
            <TableCell><span className="email-recipient">{row.recipient || "Not supplied"}</span><small>{row.store_name}</small><small>From {row.sender || "Not configured"}</small><span className={`email-mode ${row.test_mode ? "is-test" : ""}`}>{row.test_mode ? "TEST" : "LIVE"}</span></TableCell>
            <TableCell><Status row={retryId === row.id ? { status: "retrying", status_label: "Retrying" } : row} /><small>{row.status === "test_preview" ? "No send attempted" : `${row.attempt_count || 1} attempt${row.attempt_count > 1 ? "s" : ""}`}</small>{row.last_error && <p className="email-error-summary" title={row.last_error}>{row.last_error}</p>}</TableCell>
            <TableCell><span>{formatDate(row.updated_at)}</span><small>Created {formatDate(row.created_at)}</small><small>{row.provider}</small></TableCell>
            <TableCell><div className="email-row-actions"><Button variant="outline" onClick={() => setDetailId(row.id)}>View email</Button>{row.can_retry && <Button disabled={retryId !== null || loading} onClick={() => setRetryTarget(row)}>{retryId === row.id ? "Retrying…" : "Retry failed email"}</Button>}{row.status === "delivery_unknown" && <small>Check the provider before resending.</small>}</div></TableCell></TableRow>)}
        </TableBody></Table>
        <footer className="epost-table-footer"><span>{total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${total}` : "0 records"}</span><div><Button variant="outline" disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>Previous</Button><span>Page {page}</span><Button variant="outline" disabled={loading || page * pageSize >= total} onClick={() => setPage(value => value + 1)}>Next</Button></div></footer>
      </section>
    </div>
    <Dialog open={detailId !== null} onOpenChange={open => { if (!open) setDetailId(null) }}><DialogContent className="epost-detail email-log-detail"><DialogHeader><DialogTitle>{detail?.row.subject || "Email details"}</DialogTitle><DialogDescription>Saved content and delivery attempts. Preview links are disabled.</DialogDescription></DialogHeader>
      {detailError && <p role="alert">{detailError}</p>}{!detail && !detailError && <p role="status">Loading email…</p>}
      {detail && <><Status row={detail.row} /><dl><dt>Order / store</dt><dd>{detail.row.odoo_order_name} · {detail.row.store_name}</dd><dt>Recipient</dt><dd>{detail.row.recipient}</dd><dt>Sender</dt><dd>{detail.row.sender}</dd><dt>Provider message ID</dt><dd>{detail.row.provider_message_id || "No acceptance ID recorded"}</dd><dt>Mode</dt><dd>{detail.row.test_mode ? "Test" : "Live"}</dd></dl>
        <section><h3>Send attempts</h3>{!detail.attempts.length && <p>Detailed attempt history is unavailable for this older record. Its saved status is shown above.</p>}<ol className="email-attempts">{detail.attempts.map(attempt => <li key={attempt.attempt_number}><strong>Attempt {attempt.attempt_number} · {attempt.status.replaceAll("_", " ")}</strong><small>{formatDate(attempt.created_at)} → {formatDate(attempt.updated_at)}</small>{attempt.error && <p className="email-log-error">{attempt.error}</p>}</li>)}</ol>
          {detail.row.can_retry ? <Button disabled={retryId !== null} onClick={() => { setDetailId(null); setRetryTarget(detail.row) }}>Retry failed email</Button> : <p>{detail.row.retry_block_reason}</p>}</section>
        <section><h3>Email preview</h3>{preview ? <iframe title="Saved email preview" sandbox="" referrerPolicy="no-referrer" srcDoc={preview} className="email-preview-frame" tabIndex={-1} /> : <p>No HTML preview was saved.</p>}</section></>}
    </DialogContent></Dialog>
    <Dialog open={retryTarget !== null} onOpenChange={open => { if (!open && retryId === null) setRetryTarget(null) }}><DialogContent><DialogHeader><DialogTitle>Retry this email?</DialogTitle><DialogDescription>The saved email will be sent again only if the order and safety checks still pass.</DialogDescription></DialogHeader>
      {retryTarget && <><p><strong>{retryTarget.test_mode ? "TEST EMAIL" : "LIVE CUSTOMER EMAIL"}</strong></p><p>{retryTarget.subject}</p><p>To: <strong>{retryTarget.recipient}</strong></p><p>Order: {retryTarget.odoo_order_name} · Attempt {(retryTarget.attempt_count || 1) + 1}</p><div className="epost-inline"><Button variant="outline" disabled={retryId !== null} onClick={() => setRetryTarget(null)}>Cancel</Button><Button disabled={retryId !== null} onClick={() => void retry(retryTarget)}>{retryId !== null ? "Retrying…" : "Confirm retry"}</Button></div></>}
    </DialogContent></Dialog>
  </div>
}
