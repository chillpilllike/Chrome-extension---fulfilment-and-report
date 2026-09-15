import './shopify-title-review.css'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

type Rules = { clean_titles: boolean; require_approval: boolean; remove_keywords: string }
type Item = { key: string; original_title: string; prepared_title: string; sku: string; quantity: number; brands: string[] }
type Review = { job_id: string; odoo_order_name: string; route: string; revision: number; last_error: string; snapshot: { clean: boolean; items: Item[] } }
type Props = { storeId: string; api: <T>(path: string, options?: RequestInit) => Promise<T> }
export function ShopifyTitleReview({ storeId, api }: Props) {
  const [rules, setRules] = useState<Rules | null>(null)
  const [reviews, setReviews] = useState<Review[]>([])
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [tick, setTick] = useState(0)
  useEffect(() => { api<Rules>('/api/shopify/fulfilment/title-settings').then(setRules).catch(e => setError(String(e))) }, [])
  useEffect(() => { setPage(1); setDrafts({}); setReviews([]) }, [storeId])
  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ page: String(page) })
    if (storeId) query.set('store_id', storeId)
    api<{ reviews: Review[]; total: number }>(`/api/shopify/fulfilment/title-reviews?${query}`, { signal: controller.signal })
      .then(result => { setReviews(result.reviews); setTotal(result.total) }).catch(e => { if (!controller.signal.aborted) setError(String(e)) })
    return () => controller.abort()
  }, [storeId, page, tick])
  useEffect(() => { const timer = window.setInterval(() => setTick(t => t + 1), 15000); return () => window.clearInterval(timer) }, [])
  async function save() {
    setBusy('settings'); setError(''); setMessage('')
    try {
      await api('/api/shopify/fulfilment/title-settings', { method: 'POST', body: JSON.stringify(rules) })
      setMessage('Settings saved for DTB and DTC. Orders already awaiting approval remain held until approved.')
    } catch(e) { setError(String(e)) } finally { setBusy('') }
  }
  async function refreshSource(review: Review) {
    setBusy(review.job_id); setError('')
    try {
      await api(`/api/shopify/fulfilment/title-reviews/${review.job_id}/refresh`, { method: 'POST' })
      setReviews(rows => rows.filter(row => row.job_id !== review.job_id)); setTick(t => t + 1)
      setMessage(`${review.odoo_order_name}: refreshing Odoo data for another review.`)
    } catch(e) { setError(String(e)) } finally { setBusy('') }
  }
  async function approve(review: Review) {
    setBusy(review.job_id); setError(''); setMessage('')
    const key = `${review.job_id}:${review.revision}`
    const titles = Object.fromEntries(review.snapshot.items.map(item => [item.key, drafts[key]?.[item.key] ?? item.prepared_title]))
    try {
      await api(`/api/shopify/fulfilment/title-reviews/${review.job_id}/approve`, { method: 'POST', body: JSON.stringify({ revision: review.revision, titles }) })
      setReviews(rows => rows.filter(row => row.job_id !== review.job_id)); setTick(t => t + 1)
      setMessage(`${review.odoo_order_name}: titles approved and queued for ${review.route.toUpperCase()}.`)
    } catch(e) { setError(String(e)) } finally { setBusy('') }
  }
  return <Card className="shopify-title-review">
    <CardHeader><CardTitle>Product titles & approval</CardTitle><CardDescription>Prepare titles for DTB and DTC, then let the team review each order before it is sent.</CardDescription></CardHeader>
    <CardContent className="grid gap-4">
      {rules && <div className="form-fieldset grid gap-3">
        <label className="title-toggle"><input className="title-switch" type="checkbox" role="switch" checked={rules.clean_titles} onChange={e => setRules({ ...rules, clean_titles: e.target.checked })} />Use cleaned product titles</label>
        <p className="text-xs text-muted-foreground">Remove the product’s Brand and the keywords below, then automatically prepare up to 6 words. Manual edits can use more than 6 words (up to 255 characters). SKU uses the full Odoo Internal Reference. This takes priority over typed-title mode.</p>
        <label className="title-toggle"><input className="title-switch" type="checkbox" role="switch" checked={rules.require_approval} onChange={e => setRules({ ...rules, require_approval: e.target.checked })} />Require title approval before sending to Shopify</label>
        <p className="text-xs text-muted-foreground">New exports wait below when approval is enabled. Existing pending reviews still require approval if you turn it off. Orders already being sent may finish.</p>
        <label className="grid gap-1 text-sm">Remove keywords — one per line<textarea className="form-control min-h-[140px]" rows={5} value={rules.remove_keywords} onChange={e => setRules({ ...rules, remove_keywords: e.target.value })} /></label>
        <div><Button variant="outline" onClick={save} disabled={!!busy}>{busy === 'settings' ? 'Saving…' : 'Save title & approval settings'}</Button></div>
      </div>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {message && <p role="status" className="text-sm text-green-700">{message}</p>}
      <div className="flex items-center justify-between"><h3 className="font-semibold">Pending title approval ({total})</h3><Button variant="outline" onClick={() => setTick(t => t + 1)}>Refresh queue</Button></div>
      {reviews.map(review => {
        const key = `${review.job_id}:${review.revision}`
        return <div key={key} className="form-fieldset grid gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><strong>{review.odoo_order_name} · {review.route.toUpperCase()}</strong><span className="badge bg-yellow-lt">Pending approval</span></div>
          <div className="overflow-x-auto"><table className="table"><thead><tr><th>Original product title</th><th>Prepared title</th><th>SKU / Internal Reference</th><th>Qty</th></tr></thead><tbody>
            {review.snapshot.items.map(item => {
              const value = drafts[key]?.[item.key] ?? item.prepared_title
              const words = value.trim() ? value.trim().split(/\s+/).length : 0
              return <tr key={item.key}><td className="min-w-[240px] max-w-[400px]"><div>{item.original_title}</div>{item.brands.length > 0 && <small className="text-muted-foreground">Brand: {item.brands.join(', ')}</small>}</td><td className="min-w-[300px]"><Input aria-label={`Prepared title ${review.odoo_order_name} ${item.key}`} value={value} onChange={e => setDrafts(d => ({ ...d, [key]: { ...d[key], [item.key]: e.target.value } }))} /><small className="text-muted-foreground">{words} words · Manual edits can exceed 6 words</small></td><td>{item.sku || 'Missing reference'}</td><td>{item.quantity}</td></tr>
            })}
          </tbody></table></div>
          <p className="text-xs text-muted-foreground">{review.last_error}</p><div className="flex justify-end gap-2"><Button variant="outline" disabled={!!busy} onClick={() => refreshSource(review)}>Refresh from Odoo</Button><Button onClick={() => approve(review)} disabled={!!busy}>{busy === review.job_id ? 'Approving…' : `Approve & send to ${review.route.toUpperCase()}`}</Button></div>
        </div>
      })}
      {!reviews.length && <p className="text-sm text-muted-foreground">No orders awaiting title approval. Eligible orders appear here after preparation.</p>}
      {total > 25 && <div className="flex items-center gap-3"><Button variant="outline" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</Button><span>Page {page} of {Math.ceil(total / 25)}</span><Button variant="outline" disabled={page * 25 >= total} onClick={() => setPage(p => p + 1)}>Next</Button></div>}
    </CardContent>
  </Card>
}
