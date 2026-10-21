import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'

type API = <T>(path: string, options?: RequestInit) => Promise<T>
type Notice = { id: number; recipient: string; status: string; last_error: string | null }
type Status = { recent: Notice[] }

export function TeamDecisionSettings({ api }: { api: API }) {
  const [addresses, setAddresses] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [latest, setLatest] = useState<Notice | null>(null)
  useEffect(() => {
    let active = true
    api<{ settings: Record<string, string> }>('/api/settings/services')
      .then(result => { if (active) setAddresses(result.settings.after_order_team_emails || '') })
      .catch(error => { if (active) setMessage(String(error)) })
    const refresh = () => api<Status>('/api/after-order/team-notifications')
      .then(result => { if (active) setLatest(result.recent[0] || null) }).catch(() => {})
    void refresh()
    const timer = window.setInterval(() => { void refresh() }, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [api])
  async function save() {
    setBusy(true)
    try {
      const result = await api<{ settings: Record<string, string> }>('/api/settings/services', {
        method: 'POST', body: JSON.stringify({ settings: { after_order_team_emails: addresses } }),
      })
      setAddresses(result.settings.after_order_team_emails || '')
      setMessage('Team email addresses saved.')
    } catch (error) { setMessage(String(error)) } finally { setBusy(false) }
  }
  async function sendTest() {
    setBusy(true)
    try {
      const result = await api<{ message: string }>('/api/after-order/team-notifications/test', { method: 'POST', body: '{}' })
      setMessage(result.message)
      const status = await api<Status>('/api/after-order/team-notifications')
      setLatest(status.recent[0] || null)
    } catch (error) { setMessage(String(error)) } finally { setBusy(false) }
  }
  return <section className="card grid gap-3 p-4">
    <h3 className="font-semibold">Customer decision notifications</h3>
    <p className="text-sm text-muted-foreground">Email your team whenever a customer submits or changes an option from an order email. Each alert includes the customer, order, selected option and next action.</p>
    <label className="grid gap-2" htmlFor="after-order-team-emails">
      <span className="text-sm font-medium">Team email addresses — one per line</span>
      <textarea id="after-order-team-emails" rows={5} className="w-full rounded-md border bg-background px-3 py-2 text-sm" placeholder="amit@gofinch.com" value={addresses} onChange={event => setAddresses(event.target.value)} />
    </label>
    <p className="text-sm text-muted-foreground">Leave blank to turn off team alerts. Viewing a link does not send an alert; a saved customer choice does. Test-mode customer choices do not send alerts.</p>
    <div className="flex flex-wrap gap-2">
      <Button disabled={busy} onClick={() => void save()}>Save team email addresses</Button>
      <Button variant="outline" disabled={busy} onClick={() => void sendTest()}>Send test to saved addresses</Button>
    </div>
    {message && <p role="status" className="text-sm">{message}</p>}
    {latest && <p className="text-sm">Latest team email: {latest.recipient} — {latest.status === 'sent' ? 'Accepted by provider; delivery pending' : latest.status}{latest.last_error ? `. ${latest.last_error}` : ''}</p>}
  </section>
}
