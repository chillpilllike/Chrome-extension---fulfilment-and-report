import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
type API = <T>(path: string, options?: RequestInit) => Promise<T>
type Config = { enabled: boolean; provider: string; mappings: Record<string, unknown>; credentials: Record<string, boolean>; test_mode: boolean }
export function SmsSettings({ api }: { api: API }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [mapping, setMapping] = useState('{}')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api<Config>('/api/after-order/sms/settings').then(c => { setConfig(c); setMapping(JSON.stringify(c.mappings, null, 2)) }).catch(e => setNotice(String(e))) }, [api])
  async function save() {
    if (!config) return
    setBusy(true)
    try { setConfig(await api<Config>('/api/after-order/sms/settings', { method: 'POST', body: JSON.stringify({ enabled: config.enabled, provider: config.provider, mappings: JSON.parse(mapping) }) })); setNotice('Saved. Existing SMS keeps its original provider.'); }
    catch (e) { setNotice(String(e)) } finally { setBusy(false) }
  }
  return <section className="card p-4 grid gap-3"><h3 className="font-semibold">Customer SMS</h3><p>SMS has its own preview and approval. SMS failures never resend emails. Existing Odoo dispatch notifications remain unchanged.</p>
    {config && <><label><input type="checkbox" checked={config.enabled} onChange={e => setConfig({ ...config, enabled: e.target.checked })} /> Enable SMS companions</label>
      <label>SMS engine <select className="form-select" value={config.provider} onChange={e => setConfig({ ...config, provider: e.target.value })}><option value="odoo">Odoo — existing SMS service</option><option value="msg91">MSG91</option><option value="twilio">Twilio</option></select></label>
      <p>{config.test_mode ? 'Test: only +19296526393, no approval for initial sends.' : 'Live: individual SMS approval required.'}</p>
      <p>SMS only: expected dispatch delay, dispatch hurdle, unavailable items (with or without alternatives), first parcel movement, and delivery confirmation. Payment and refund SMS remain held until verified event connections are completed. No welcome, reminder or lost-package SMS.</p>
      <p>{config.provider === 'odoo' ? 'Uses the order’s Odoo installation. Requires its SMS module and credits.' : config.credentials[config.provider] ? 'Runtime credentials present; sender and delivery need verification.' : 'Runtime credentials are not configured.'}</p>
      <details><summary>Website sender and template mappings</summary><p>Keys are store_id:website_id. Enable transactional_sms_enabled only after reviewing consent and destination requirements. No secrets here.</p>
        <pre className="text-xs whitespace-pre-wrap">{'{"1:2":{"transactional_sms_enabled":false,"twilio":{"sender":"+15551234567"},"msg91":{"sender":"HEADER","templates":{"expected_dispatch":{"template_id":"MSG91_ID","dlt_template_id":"DLT_ID","text":"##brand##: Dispatch update for ##order##. ##url##"}}}}}'}</pre>
        <textarea aria-label="SMS website mappings JSON" className="form-control w-full min-h-40 font-mono" value={mapping} onChange={e => setMapping(e.target.value)} />
        <p>Coolify runtime secrets: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, MSG91_AUTH_KEY. Twilio mappings also support messaging_service_sid.</p>
      </details><Button disabled={busy} onClick={() => void save()}>Save SMS settings</Button></>}{notice && <p role="status">{notice}</p>}
  </section>
}
type Row = { id: number; provider: string; recipient: string; test_mode: boolean; body: string; status: string; last_error?: string; attempts: number; approval_digest: string }
export function SmsPreview({ api, emailId }: { api: API; emailId: number }) {
  const [row, setRow] = useState<Row | null>(null); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false)
  async function load() { setRow((await api<{ row: Row | null }>(`/api/after-order/sms/email/${emailId}`)).row) }
  useEffect(() => { void load().catch(e => setNotice(String(e))) }, [emailId])
  async function act(path: string, body = {}) { setBusy(true); setNotice(''); try { const d = await api<{ error?: string }>(path, { method: 'POST', body: JSON.stringify(body) }); if (d.error) setNotice(d.error); await load() } catch (e) { setNotice(String(e)) } finally { setBusy(false) } }
  return <section className="grid gap-3 border p-3"><h3>Companion SMS</h3>{row ? <><p>{row.test_mode ? 'TEST' : 'LIVE'} · {row.provider} · {row.recipient} · {row.status} · Attempts: {row.attempts}</p><pre className="whitespace-pre-wrap break-words">{row.body}</pre>{row.last_error && <p role="alert">{row.last_error}</p>}
      {['awaiting_approval', 'failed'].includes(row.status) && <Button disabled={busy} onClick={() => { if (window.confirm(`Send this exact SMS to ${row.recipient}? SMS charges may apply.`)) void act(`/api/after-order/sms/${row.id}/approve-send`, { approval_digest: row.approval_digest }) }}>Approve sending SMS only</Button>}
      <Button variant="outline" disabled={busy} onClick={() => void act(`/api/after-order/sms/${row.id}/refresh`)}>Refresh delivery status</Button>
    </> : <><p>No SMS prepared. Configure SMS in Settings first. Test preparation sends only to +19296526393.</p><Button disabled={busy} onClick={() => void act(`/api/after-order/sms/email/${emailId}/prepare`)}>Prepare companion SMS</Button></>}{notice && <p role="status">{notice}</p>}</section>
}
