import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

type API = <T>(path: string, options?: RequestInit) => Promise<T>
type Config = { bypass_approval: boolean; include_existing: boolean; enabled_at: string | null }
type Monitor = { enabled: boolean; dispatch_handling_days: string; last_check_at: string | null; errors: Record<string,string> }

export function EmailApprovalSettings({ api }: { api: API }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [existing, setExisting] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [monitor, setMonitor] = useState<Monitor | null>(null)
  const [monitorEnabled, setMonitorEnabled] = useState(false)
  const [handling, setHandling] = useState(2)
  useEffect(() => {
    api<Config>('/api/after-order/settings/email-approval').then(c => {
      setConfig(c); setEnabled(c.bypass_approval); setExisting(c.include_existing)
    }).catch(e => setNotice(String(e)))
    api<Monitor>('/api/after-order/notifications/status').then(m => {
      setMonitor(m); setMonitorEnabled(m.enabled); setHandling(Number(m.dispatch_handling_days ?? 2))
    }).catch(e => setNotice(String(e)))
  }, [api])
  async function save() {
    if (enabled && !window.confirm(existing
      ? 'Send eligible new and already queued customer emails automatically, without team approval? Safety checks and financial approvals remain in place.'
      : 'Send newly prepared customer emails automatically, without team approval? Existing queued emails stay held.')) return
    setBusy(true); setNotice('')
    try {
      const c = await api<Config>('/api/after-order/settings/email-approval', { method: 'POST', body: JSON.stringify({
        bypass_approval: enabled, include_existing: existing, confirm_live_sends: enabled,
      }) })
      setConfig(c)
      setNotice(c.bypass_approval ? 'Saved. Eligible emails will send automatically after safety checks.' : 'Saved. Other emails require approval again; the always-automatic notifications remain automatic.')
    } catch (e) { setNotice(String(e)) } finally { setBusy(false) }
  }
  async function saveMonitor() {
    if (monitorEnabled && !window.confirm('Prepare eligible customer notifications, reminders and safe retries automatically? Email and SMS approval settings still apply. No payments, refunds or replacements will be executed.')) return
    setBusy(true)
    try {
      const m=await api<Monitor>('/api/after-order/notifications/settings',{method:'POST',body:JSON.stringify({
        enabled:monitorEnabled, dispatch_handling_days:handling, confirm_notifications:monitorEnabled,
      })})
      setMonitor(m); setNotice('Notification scheduling saved. Financial approvals remain required.')
    } catch(e) {setNotice(String(e))} finally {setBusy(false)}
  }
  return <section className="card grid gap-3 p-4">
    <h3 className="font-semibold">Customer email approval</h3>
    <p className="text-sm text-muted-foreground">New-order welcomes, Shopify DTC dispatch notices, eligible delivery follow-ups and manually confirmed refund notices remain automatic.</p>
    {config && <>
      <label className="flex items-center gap-2"><input type="checkbox" role="switch" checked={enabled} disabled={busy} onChange={e => setEnabled(e.target.checked)} /> Bypass team approval for other emails</label>
      <label className="flex items-center gap-2"><input type="checkbox" checked={existing} disabled={busy || !enabled} onChange={e => setExisting(e.target.checked)} /> Also release eligible emails already awaiting approval</label>
      <p className="text-sm text-muted-foreground">Test mode still uses only the test inbox. Suppression, opt-outs, order cutoff, sourcing review, current tracking and duplicate checks still apply. Stale or blocked messages stay in Email log with the reason. This does not approve refunds, create payment requests, or bypass SMS approval.</p>
      <div><Button disabled={busy} onClick={() => void save()}>{busy ? 'Saving…' : 'Save email approval settings'}</Button></div>
    </>}
    {monitor && <div className="grid gap-3 border-t pt-4">
      <h4 className="font-semibold">Notification scheduling</h4>
      <label className="flex items-center gap-2"><input type="checkbox" role="switch" checked={monitorEnabled} onChange={e=>setMonitorEnabled(e.target.checked)} disabled={busy}/> Prepare notifications, reminders and safe retries</label>
      <label className="flex items-center gap-2">Dispatch handling allowance (calendar days)<input className="w-20 rounded border p-2" type="number" min="0" max="14" value={handling} onChange={e=>setHandling(Number(e.target.value))} disabled={busy}/></label>
      <p className="text-sm text-muted-foreground">Expired customer choices go to team review. This scheduler never executes a refund, charge or replacement. SMS follows its separate approval setting.</p>
      <p className="text-sm">Last check: {monitor.last_check_at || 'Not run yet'}</p>
      {Object.entries(monitor.errors).map(([stage,error])=><p role="alert" key={stage} className="text-sm text-red-600">{stage}: {error}</p>)}
      <div><Button disabled={busy} onClick={()=>void saveMonitor()}>Save notification scheduling</Button></div>
    </div>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
  </section>
}
