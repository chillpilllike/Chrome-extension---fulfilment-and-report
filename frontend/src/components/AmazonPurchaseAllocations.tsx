import './amazon-purchase-allocations.css'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type Purchase = { amazon_order_id: string; quantity: number; amazon_account_name: string; amazon_account_type?: string; total_cost?: number | null; state?: string; tracking_status?: string }
type Detail = { order_name: string; product_name: string; asin: string; required_quantity: number; existing_order_id?: string; allocations: Purchase[] }
type Props = { lineId: number | null; storeId: number; api: <T>(path: string, options?: RequestInit) => Promise<T>; onClose: () => void; onSaved: () => void }
export function AmazonPurchaseAllocations({ lineId, storeId, api, onClose, onSaved }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [rows, setRows] = useState<Purchase[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    setDetail(null); setError(''); setRows([])
    if (!lineId || !storeId) return
    const controller = new AbortController()
    api<Detail>(`/api/lines/${lineId}/amazon-purchases?store_id=${storeId}`, { signal: controller.signal }).then(data => {
      setDetail(data)
      setRows(data.allocations.length ? data.allocations : [{ amazon_order_id: data.existing_order_id || '', quantity: 1, amazon_account_name: '' }])
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [lineId, storeId])
  const allocated = rows.filter(r => r.state !== 'cancelled').reduce((sum, r) => sum + Number(r.quantity || 0), 0)
  const update = (index: number, patch: Partial<Purchase>) => setRows(old => old.map((r, i) => i === index ? { ...r, ...patch } : r))
  return <Dialog open={lineId !== null} onOpenChange={open => !open && onClose()}><DialogContent className="amazon-purchase-dialog">
    <DialogHeader><DialogTitle>Amazon purchases · {detail?.order_name}</DialogTitle><DialogDescription>Assign the units of this product to the Amazon orders already placed. This does not buy anything.</DialogDescription></DialogHeader>
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {detail && <>
      <p className="text-sm">{detail.product_name}<br /><b>{detail.asin} · Required: {detail.required_quantity} units · Allocated: {allocated}</b></p>
      <p className="text-sm text-muted-foreground">Enter the actual quantity in each Amazon order. Every purchase is tracked separately. Enter all purchase costs to confirm total cost and profit. Pickup remains on hold until all required units are linked, delivered and scanned.</p>
      <div style={{ minHeight: 0, overflowY: 'auto', flex: 1, display: 'grid', gap: '12px' }}>{rows.map((row, i) => <div key={i} className="rounded border p-3 grid gap-2">
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 90px', gap: '12px' }}><label className="text-sm">Amazon order ID<Input aria-label={`Amazon order ${i + 1}`} value={row.amazon_order_id} disabled={Boolean(detail.allocations[i])} onChange={e => update(i, { amazon_order_id: e.target.value })} /></label><label className="text-sm">Quantity<Input aria-label={`Quantity ${i + 1}`} type="number" min={1} step={1} value={row.quantity} onChange={e => update(i, { quantity: Number(e.target.value) })} /></label></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '12px' }}><label className="text-sm">Amazon account (optional)<Input value={row.amazon_account_name} onChange={e => update(i, { amazon_account_name: e.target.value })} placeholder="Use the account name shown in tracking" /></label><label className="text-sm">Purchase cost (optional)<Input type="number" min={0} step="0.01" value={row.total_cost ?? ''} onChange={e => update(i, { total_cost: e.target.value === '' ? null : Number(e.target.value) })} /></label></div>
        {row.tracking_status && <small>{row.tracking_status}</small>}
        {!detail.allocations[i] && <Button variant="ghost" onClick={() => setRows(old => old.filter((_, n) => n !== i))}>Remove this unsaved purchase</Button>}
      </div>)}</div>
      <Button variant="outline" onClick={() => setRows(old => [...old, { amazon_order_id: '', quantity: 1, amazon_account_name: '' }])}>Add another Amazon order</Button>
      {allocated < detail.required_quantity && <p className="text-sm">{detail.required_quantity - allocated} units still need an Amazon purchase. You can save now and add them later.</p>}
    </>}
    <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy || !detail || !rows.length || allocated > detail.required_quantity} onClick={async () => {
      setBusy(true); setError('')
      try { await api(`/api/lines/${lineId}/amazon-purchases`, { method: 'POST', body: JSON.stringify({ store_id: storeId, allocations: rows }) }); onSaved(); onClose() }
      catch (e) { setError(String(e)) } finally { setBusy(false) }
    }}>{busy ? 'Saving…' : 'Save purchase allocations'}</Button></DialogFooter>
  </DialogContent></Dialog>
}
