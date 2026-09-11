import { useEffect, useState } from 'react'
import { CareActivityTimeline, type CareEvent } from './CareActivityTimeline'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'

type Fetcher = <T>(path: string, init?: RequestInit) => Promise<T>
type Product = { name: string; default_code: string; original_total?: number; alternative_total?: number; difference?: number; currency: string; pricing_error?: string }
type Offer = { line_id: number; recommendations: Product[]; selection?: { version: number; status: string; deadline_at: string; product: Product; last_error?: string; refund_status?: string; result: { quote_name?: string; email_status?: string; payment_verified?: boolean; cost_absorbed?: boolean; absorbed_amount?: number; approval_reason?: string } } }
type Requests = { tracking_code?: string; mapping_candidates?: {id:number;product_name:string;quantity:number}[]; removals: {line_id: number; version: number; status: string; origin: string}[]; deadlines: {line_id: number; deadline_at: string; state: string; outcome: string}[]; parcel_items: {line_id: number; quantity: number}[] }
type Event = CareEvent

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
      <p className="text-sm text-muted-foreground">Exact references are matched on this order’s store. After every affected line has recommendations, an email is prepared in the approval queue. It is not sent until the team approves it.</p>
      <label className="flex items-start gap-2 text-sm"><Checkbox checked={checked} onCheckedChange={value => setChecked(value === true)}/>I checked third-party and manual fulfilment; this item still needs a customer choice.</label>
      <Button disabled={busy || !checked || !references.trim() || !caseId} onClick={() => void save()}>{busy ? 'Checking…' : 'Save alternatives & prepare email'}</Button>
      {message && <p role="status" className="text-sm whitespace-pre-wrap">{message}</p>}
    </DialogContent></Dialog>
  </>
}

export function OrderCareTimeline({ caseId, orderNumber, request }: { caseId: number; orderNumber: string; request: Fetcher }) {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState<Event[]>([])
  const [offers, setOffers] = useState<Offer[]>([])
  const [costOffer,setCostOffer] = useState<Offer | null>(null)
  const [costReason,setCostReason] = useState('')
  const [costAmount,setCostAmount] = useState('')
  const [requests, setRequests] = useState<Requests>({removals:[],deadlines:[],parcel_items:[]})
  const [mappingOpen,setMappingOpen] = useState(false)
  const [mapping,setMapping] = useState<Record<number,number>>({})
  const [mappingChecked,setMappingChecked] = useState(false)
  const [notice,setNotice] = useState('')
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(false)
  async function load() {
    setLoading(true); setError('')
    try {
      const [activity, alternatives, decisions] = await Promise.all([
        request<{rows: Event[]}>(`/api/after-order/cases/${caseId}/events`),
        request<{rows: Offer[]}>(`/api/after-order/cases/${caseId}/line-alternatives`),
        request<Requests>(`/api/after-order/cases/${caseId}/requests`),
      ])
      setEvents(activity.rows); setOffers(alternatives.rows)
      setRequests(decisions)
    } catch (error) { setError(String(error)) } finally { setLoading(false) }
  }
  return <details className="mt-3 rounded-lg border bg-background p-3" onToggle={event => { const expanded = event.currentTarget.open; setOpen(expanded); if (expanded) void load() }}>
    <summary className="cursor-pointer text-sm font-semibold">Timeline & price differences · {orderNumber}</summary>
    {open && <div className="mt-3 space-y-4">
      <div className="flex gap-2"><Input aria-label="Filter timeline by line or event" placeholder="Filter by line, product or event…" value={filter} onChange={event => setFilter(event.target.value)}/><Button size="sm" variant="outline" onClick={() => void load()} disabled={loading}>Refresh</Button></div>
      {notice && <p role="status" className="text-sm">{notice}</p>}
      {requests.tracking_code && <Button size="sm" variant="outline" onClick={() => {setMapping(Object.fromEntries(requests.parcel_items.map(x=>[x.line_id,x.quantity])));setMappingChecked(false);setMappingOpen(true)}}>Verify contents of parcel {requests.tracking_code}</Button>}
      <Dialog open={mappingOpen} onOpenChange={setMappingOpen}><DialogContent><DialogHeader><DialogTitle>Verify parcel contents</DialogTitle><DialogDescription>{orderNumber} · {requests.tracking_code}. Include only quantities packed in this parcel; leave other lines at zero.</DialogDescription></DialogHeader>
        {requests.mapping_candidates?.map(line => <label key={line.id} className="grid grid-cols-[1fr_90px] items-center gap-3 text-sm">{line.product_name} · line {line.id}<Input type="number" min="0" max={line.quantity} step="any" value={mapping[line.id] || 0} onChange={e=>setMapping({...mapping,[line.id]:Number(e.target.value)})}/></label>)}
        <label className="flex gap-2 text-sm"><Checkbox checked={mappingChecked} onCheckedChange={v=>setMappingChecked(v===true)}/>I checked the packing records and verified these quantities belong to this parcel.</label>
        <Button disabled={loading || !mappingChecked} onClick={async()=>{setLoading(true);try{const result=await request<{message:string}>(`/api/after-order/cases/${caseId}/parcel-items`,{method:'POST',body:JSON.stringify({items:Object.entries(mapping).filter(([,qty])=>qty>0).map(([id,quantity])=>({line_id:Number(id),quantity})),verified_by:'Operations team'})});setNotice(result.message);setMappingOpen(false);await load()}catch(error){setError(String(error))}finally{setLoading(false)}}}>Save verified parcel contents</Button>
      </DialogContent></Dialog>
      {requests.deadlines.map(row => <p key={`${row.line_id}-${row.deadline_at}`} className="text-sm">Line {row.line_id} · respond by {date(row.deadline_at)} · {row.outcome || row.state}</p>)}
      {requests.removals.filter(row => row.status !== 'withdrawn').map(row => <div key={row.line_id} className="rounded border p-3 text-sm">
        <p>Line {row.line_id} · removal · {row.status.replaceAll('_',' ')} · {row.origin.replaceAll('_',' ')}</p>
        {['needs_approval','cancel_review'].includes(row.status) && <Button variant="outline" size="sm" disabled={loading} onClick={async () => {
          setLoading(true); try { const result = await request<{message:string}>(`/api/after-order/cases/${caseId}/lines/${row.line_id}/approve-removal`,{method:'POST',body:JSON.stringify({version:row.version,approved_by:'Operations team'})}); setNotice(result.message); await load() } catch(error) {setError(String(error))} finally {setLoading(false)}
        }}>Approve removal for finance review</Button>}
      </div>)}
      {requests.parcel_items.length > 0 && <p className="text-sm">Verified parcel contents: {requests.parcel_items.map(row => `line ${row.line_id} × ${row.quantity}`).join(', ')}</p>}
      {offers.map(offer => <section className="rounded border p-3 text-sm" key={offer.line_id}>
        <strong>Line {offer.line_id}</strong>
        <p className="text-muted-foreground">Best alternatives: {offer.recommendations.map(p => p.name).join(', ')}</p>
        {offer.selection && <><p className="mt-2 font-medium">{offer.selection.product.name} · {offer.selection.status.replaceAll('_',' ')}</p>
          <p>Selection deadline: {date(offer.selection.deadline_at)}</p>
          {offer.selection.product.difference != null && ['choosing','needs_review','waiting_payment','waiting_refund'].includes(offer.selection.status) && <Button size="sm" variant="outline" disabled={loading || new Date(offer.selection.deadline_at).getTime()>Date.now()} onClick={async () => {
            const selection=offer.selection!
            if(!window.confirm(`Approve processing ${orderNumber}, line ${offer.line_id}, ${selection.product.name}, difference ${selection.product.currency} ${selection.product.difference}? Any payment email requires a separate send approval. Refund execution requires recorded refund approval.`))return
            setLoading(true);try {
              const result=await request<{message:string}>(`/api/after-order/cases/${caseId}/lines/${offer.line_id}/approve-processing`,{method:'POST',body:JSON.stringify({version:selection.version,confirm_amount:selection.product.difference})})
              setNotice(result.message);await load()
            }catch(error){setError(String(error))}finally{setLoading(false)}
          }}>Approve processing / recheck payment</Button>}
          <p>Original paid line: {offer.selection.product.currency} {offer.selection.product.original_total?.toFixed(2) ?? 'Needs review'} · Replacement: {offer.selection.product.alternative_total?.toFixed(2) ?? 'Needs review'}</p>
          {offer.selection.product.difference != null ? <p className="font-medium">{offer.selection.product.difference < 0 ? 'Refund difference' : 'Additional payment'}: {offer.selection.product.currency} {Math.abs(offer.selection.product.difference).toFixed(2)}{offer.selection.refund_status && ` · ${offer.selection.refund_status.replaceAll('_',' ')}`}</p> : <p className="text-amber-800">Price needs review: {offer.selection.product.pricing_error}</p>}
          {offer.selection.result.quote_name && <p>Quotation {offer.selection.result.quote_name} · email {offer.selection.result.email_status || 'not verified'}</p>}
          {offer.selection.result.payment_verified && <p className="font-medium text-green-700">Payment verified</p>}
          {offer.selection.result.cost_absorbed && <p className="font-medium text-green-700">Extra cost accepted by team · {offer.selection.product.currency} {offer.selection.result.absorbed_amount?.toFixed(2)} · No additional customer payment. {offer.selection.result.approval_reason}</p>}
          {!!offer.selection.product.difference && offer.selection.product.difference > 0 && !offer.selection.result.cost_absorbed && !offer.selection.result.quote_name && ['choosing','needs_review'].includes(offer.selection.status) && <Button size="sm" variant="outline" disabled={loading} onClick={() => {setCostOffer(offer);setCostReason('');setCostAmount('')}}>Accept extra cost — no quotation</Button>}
          {offer.selection.product.difference != null && offer.selection.product.difference < 0 && ['waiting_refund','needs_review'].includes(offer.selection.status) && <Button size="sm" variant="outline" disabled={loading} onClick={async () => {
            const amount = Math.abs(offer.selection!.product.difference!)
            if (!window.confirm(`Approve ${offer.selection!.product.currency} ${amount.toFixed(2)} refund for ${orderNumber}, line ${offer.line_id}? Fulfilment waits for verified refund and accounting reconciliation.`)) return
            setLoading(true); try {
              const result = await request<{message:string}>(`/api/after-order/cases/${caseId}/lines/${offer.line_id}/approve-replacement-refund`,{method:'POST',body:JSON.stringify({version:offer.selection!.version,confirm_amount:amount})})
              setNotice(result.message); await load()
            } catch(error) {setError(String(error))} finally {setLoading(false)}
          }}>Approve / reconcile replacement refund</Button>}
          {['processed','manual_fulfilment'].includes(offer.selection.status) && <p className="font-medium text-green-700">Replacement applied · {offer.selection.status === 'processed' ? 'ready for fulfilment' : 'manual fulfilment required'}</p>}
          {offer.selection.status === 'needs_review' && <Button size="sm" variant="outline" disabled={loading} onClick={async () => {
            setLoading(true); try { await request(`/api/after-order/cases/${caseId}/lines/${offer.line_id}/retry-processing`,{method:'POST',body:JSON.stringify({version:offer.selection?.version,approved_by:'Operations team'})}); await load() } catch(error) {setError(String(error))} finally {setLoading(false)}
          }}>Retry unchanged request after review</Button>}
          {offer.selection.last_error && <p className="text-destructive">{offer.selection.last_error}</p>}
        </>}
      </section>)}
      {(offers.some(o=>o.selection?.status==='ready_to_release') || requests.removals.some(r=>r.status==='finance_review')) && <Button variant="outline" disabled={loading} onClick={async()=>{
        if(!window.confirm(`Approve release of the reviewed selections for ${orderNumber}? Current payment, refund and all-line checks still apply.`))return
        const versions=Object.fromEntries([...offers.filter(o=>o.selection).map(o=>[o.line_id,o.selection!.version]),...requests.removals.filter(r=>r.status!=='withdrawn').map(r=>[r.line_id,r.version])])
        setLoading(true);try {const result=await request<{message:string}>(`/api/after-order/cases/${caseId}/approve-release`,{method:'POST',body:JSON.stringify({versions})});setNotice(result.message);await load()}catch(error){setError(String(error))}finally{setLoading(false)}
      }}>Approve reviewed replacement release</Button>}
      {loading && <p role="status">Loading timeline…</p>}{error && <p role="alert" className="text-destructive">{error}</p>}
      <Dialog open={!!costOffer} onOpenChange={open => {if(!loading && !open)setCostOffer(null)}}><DialogContent>
        <DialogHeader><DialogTitle>Accept replacement extra cost</DialogTitle><DialogDescription>{orderNumber} · line {costOffer?.line_id} · {costOffer?.selection?.product.name}. The business absorbs this extra cost; no additional payment is requested. The 24-hour selection window stays unchanged.</DialogDescription></DialogHeader>
        <p>Extra cost: {costOffer?.selection?.product.currency} {costOffer?.selection?.product.difference?.toFixed(2)}</p>
        <label htmlFor={`cost-amount-${caseId}`}>Type the exact amount to confirm</label>
        <Input id={`cost-amount-${caseId}`} inputMode="decimal" value={costAmount} onChange={e=>setCostAmount(e.target.value)} disabled={loading}/>
        <label htmlFor={`cost-reason-${caseId}`}>Approval reason</label>
        <Input id={`cost-reason-${caseId}`} value={costReason} onChange={e=>setCostReason(e.target.value)} maxLength={500} disabled={loading}/>
        <Button disabled={loading || costReason.trim().length<3 || !costAmount.trim() || Number(costAmount)!==costOffer?.selection?.product.difference} onClick={async()=>{
          if(!costOffer?.selection)return
          setLoading(true);try {
            const result=await request<{ok:boolean;message:string}>(`/api/after-order/cases/${caseId}/lines/${costOffer.line_id}/accept-replacement-cost`,{method:'POST',body:JSON.stringify({version:costOffer.selection.version,confirm_amount:Number(costAmount),reason:costReason.trim()})})
            setNotice(result.message);if(result.ok)setCostOffer(null);await load()
          }catch(error){setError(String(error))}finally{setLoading(false)}
        }}>Confirm business absorbs extra cost</Button>
      </DialogContent></Dialog>
      <CareActivityTimeline events={events} filter={filter} loading={loading}/>
    </div>}
  </details>
}
