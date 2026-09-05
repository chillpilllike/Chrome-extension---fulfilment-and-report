import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'

type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>
type Product = { name: string; default_code: string; original_total?: number; alternative_total?: number; difference?: number; currency: string; pricing_error?: string }
type Offer = { line_id: number; recommendations: Product[]; selection?: { status: string; deadline_at: string; product: Product; last_error?: string; refund_status?: string; result: { quote_name?: string; email_status?: string } } }
type Event = { id: number; event_type: string; created_at: string; actor_label?: string; actor_type?: string; decision?: string; details?: Record<string, unknown> }

const date = (value: string) => value ? new Date(value).toLocaleString() : '—'

export function LineAlternativeButton({ lineId, name, request }: { lineId: number; name: string; request: Fetcher }) {
  const [open, setOpen] = useState(false)
  const [references, setReferences] = useState('')
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [caseId, setCaseId] = useState<number>()
  useEffect(() => {
    if (!open) return
    let current = true
    setMessage(''); setCaseId(undefined); setBusy(true); setChecked(false)
    request<{case_id: number; rows: Offer[]}>(`/api/after-order/lines/${lineId}/case`).then(result => {
      if (!current) return
      setCaseId(result.case_id)
      setReferences(result.rows.find(row => row.line_id === lineId)?.recommendations.map(p => p.default_code).join('\n') || '')
    }).catch(error => current && setMessage(String(error))).finally(() => current && setBusy(false))
    return () => { current = false }
  }, [open, lineId, request])
  async function save() {
    setBusy(true); setMessage('')
    try {
      const result = await request<{message: string}>(`/api/after-order/cases/${caseId}/lines/${lineId}/alternatives`, {
        method: 'POST', body: JSON.stringify({references: references.split(/[\n,]+/).map(v => v.trim()).filter(Boolean), sourcing_checked: checked}),
      })
      setMessage(result.message)
    } catch (error) { setMessage(String(error)) } finally { setBusy(false) }
  }
  return <>
    <Button size="sm" variant="outline" onClick={() => setOpen(true)}>Choose customer alternatives</Button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent>
      <DialogHeader><DialogTitle>Alternatives for this line</DialogTitle><DialogDescription>{name} · line {lineId}</DialogDescription></DialogHeader>
      <label className="text-sm font-medium" htmlFor={`alternative-refs-${lineId}`}>Odoo Internal References, in recommendation order</label>
      <textarea id={`alternative-refs-${lineId}`} className="min-h-32 w-full rounded-md border p-3 text-sm" value={references} onChange={event => setReferences(event.target.value)} placeholder="One reference per line" disabled={busy || !caseId}/>
      <p className="text-sm text-muted-foreground">Exact references are matched on this order’s store. These products appear under “Best alternatives”. The email is sent only after every affected line has recommendations. In test mode, it goes only to the test recipient.</p>
      <label className="flex items-start gap-2 text-sm"><Checkbox checked={checked} onCheckedChange={value => setChecked(value === true)}/>I checked third-party and manual fulfilment; this item still needs a customer choice.</label>
      <Button disabled={busy || !checked || !references.trim() || !caseId} onClick={() => void save()}>{busy ? 'Checking…' : 'Save alternatives & notify customer'}</Button>
      {message && <p role="status" className="text-sm whitespace-pre-wrap">{message}</p>}
    </DialogContent></Dialog>
  </>
}

export function OrderCareTimeline({ caseId, orderNumber, request }: { caseId: number; orderNumber: string; request: Fetcher }) {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState<Event[]>([])
  const [offers, setOffers] = useState<Offer[]>([])
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(false)
  async function load() {
    setLoading(true); setError('')
    try {
      const [activity, alternatives] = await Promise.all([
        request<{rows: Event[]}>(`/api/after-order/cases/${caseId}/events`),
        request<{rows: Offer[]}>(`/api/after-order/cases/${caseId}/line-alternatives`),
      ])
      setEvents(activity.rows); setOffers(alternatives.rows)
    } catch (error) { setError(String(error)) } finally { setLoading(false) }
  }
  return <details className="mt-3 rounded-lg border bg-background p-3" onToggle={event => { const expanded = event.currentTarget.open; setOpen(expanded); if (expanded) void load() }}>
    <summary className="cursor-pointer text-sm font-semibold">Timeline & price differences · {orderNumber}</summary>
    {open && <div className="mt-3 space-y-4">
      <div className="flex gap-2"><Input aria-label="Filter timeline by line or event" placeholder="Filter by line, product or event…" value={filter} onChange={event => setFilter(event.target.value)}/><Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>Refresh</Button></div>
      {offers.map(offer => <section className="rounded border p-3 text-sm" key={offer.line_id}>
        <strong>Line {offer.line_id}</strong>
        <p className="text-muted-foreground">Best alternatives: {offer.recommendations.map(p => p.name).join(', ')}</p>
        {offer.selection && <><p className="mt-2 font-medium">{offer.selection.product.name} · {offer.selection.status.replaceAll('_',' ')}</p>
          <p>Selection deadline: {date(offer.selection.deadline_at)}</p>
          <p>Original paid line: {offer.selection.product.currency} {offer.selection.product.original_total?.toFixed(2) ?? 'Needs review'} · Replacement: {offer.selection.product.alternative_total?.toFixed(2) ?? 'Needs review'}</p>
          {offer.selection.product.difference != null ? <p className="font-medium">{offer.selection.product.difference < 0 ? 'Refund difference' : 'Additional payment'}: {offer.selection.product.currency} {Math.abs(offer.selection.product.difference).toFixed(2)}{offer.selection.refund_status && ` · ${offer.selection.refund_status.replaceAll('_',' ')}`}</p> : <p className="text-amber-800">Price needs review: {offer.selection.product.pricing_error}</p>}
          {offer.selection.result.quote_name && <p>Quotation {offer.selection.result.quote_name} · email {offer.selection.result.email_status || 'not verified'}</p>}
          {offer.selection.last_error && <p className="text-destructive">{offer.selection.last_error}</p>}
        </>}
      </section>)}
      {loading && <p role="status">Loading timeline…</p>}{error && <p role="alert" className="text-destructive">{error}</p>}
      <ol className="space-y-3 border-l-2 border-primary/20 pl-4">
        {events.filter(event => JSON.stringify(event).toLowerCase().includes(filter.toLowerCase())).map(event => <li key={event.id} className="text-sm">
          <div className="flex flex-wrap justify-between gap-2"><strong>{event.event_type.replaceAll('_',' ')}</strong><time dateTime={event.created_at}>{date(event.created_at)}</time></div>
          <p className="text-muted-foreground">{event.actor_label || event.actor_type}{event.decision && ` · ${event.decision.replaceAll('_',' ')}`}</p>
          <dl>{Object.entries(event.details || {}).filter(([key]) => !['signature','request_fingerprint','allowed_actions'].includes(key)).map(([key,value]) => value != null && <div key={key} className="break-words"><dt className="inline text-muted-foreground">{key.replaceAll('_',' ')}: </dt><dd className="inline">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
        </li>)}
      </ol>
      {!loading && !events.length && <p className="text-sm text-muted-foreground">No recorded activity yet.</p>}
    </div>}
  </details>
}
