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
type Row = { id: number; provider: string; recipient: string; test_mode: boolean; body: string; status: string; last_error?: string; attempts: number; approval_digest: string; can_resend?: boolean; provider_id?: string }
export function SmsPreview({ api, emailId }: { api: API; emailId: number }) {
  const [row, setRow] = useState<Row | null>(null); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('Loading SMS…'); const [canPrepare, setCanPrepare] = useState(false)
  const [attempts, setAttempts] = useState<{attempt_number:number;status:string;created_at:string;error?:string;provider_id?:string}[]>([])
  async function load() { const d = await api<{ row: Row | null; reason?:string;can_prepare?:boolean;attempts?:typeof attempts }>(`/api/after-order/sms/email/${emailId}`); setRow(d.row);setReason(d.reason || '');setCanPrepare(!!d.can_prepare);setAttempts(d.attempts || []) }
  useEffect(() => { void load().catch(e => setNotice(String(e))) }, [emailId])
  async function act(path: string, body = {}) { setBusy(true); setNotice(''); try { const d = await api<{ error?: string }>(path, { method: 'POST', body: JSON.stringify(body) }); if (d.error) setNotice(d.error); await load() } catch (e) { setNotice(String(e)) } finally { setBusy(false) } }
  return <section className="grid gap-3 border p-3"><h3>SMS details</h3>{row ? <><p>{row.test_mode ? 'TEST' : 'LIVE'} · {row.provider} · {row.recipient} · {row.status} · Attempts: {row.attempts}</p><pre className="whitespace-pre-wrap break-words">{row.body}</pre>{row.last_error && <p role="alert">{row.last_error}</p>}
      <p>Provider reference: {row.provider_id || 'Not available'}. Accepted / queued / sent does not confirm delivery.</p>
      {['awaiting_approval', 'failed','provider_failed','undelivered'].includes(row.status) && row.attempts < 3 && <Button disabled={busy} onClick={() => { if (window.confirm(`Send this exact SMS to ${row.recipient}? SMS charges may apply.`)) void act(`/api/after-order/sms/${row.id}/approve-send`, { approval_digest: row.approval_digest }) }}>{row.attempts ? 'Retry failed SMS' : 'Approve sending SMS only'}</Button>}
      {row.can_resend && <Button variant="outline" disabled={busy} onClick={() => { if(window.confirm(`Send another copy to ${row.recipient}? This SMS was already delivered. Another charge may apply.`)) void act(`/api/after-order/sms/${row.id}/resend`,{approval_digest:row.approval_digest,confirm_duplicate_charge:true}) }}>Resend SMS — another copy</Button>}
      <Button variant="outline" disabled={busy} onClick={() => void act(`/api/after-order/sms/${row.id}/refresh`)}>Refresh delivery status</Button>
      {attempts.length>0 && <details><summary>SMS attempt history</summary>{attempts.map(a=><p key={a.attempt_number}>Attempt {a.attempt_number} · {a.status} · {new Date(a.created_at).toLocaleString()} {a.error} {a.provider_id}</p>)}</details>}
    </> : <><p>{reason}</p>{canPrepare && <Button disabled={busy} onClick={() => void act(`/api/after-order/sms/email/${emailId}/prepare`)}>Prepare companion SMS</Button>}</>}{notice && <p role="status">{notice}</p>}</section>
}

export function SmsLog({api,storeId}:{api:API;storeId:string}) {
  const [rows,setRows]=useState<(Row & {email_id:number;odoo_order_name:string;sender_domain:string;updated_at:string})[]>([])
  const [total,setTotal]=useState(0);const [page,setPage]=useState(1);const [status,setStatus]=useState('');const [query,setQuery]=useState('');const [selected,setSelected]=useState<number|null>(null);const [error,setError]=useState('');const [tick,setTick]=useState(0)
  useEffect(()=>{setPage(1);setSelected(null)},[storeId,status,query])
  useEffect(()=>{const c=new AbortController();setError('');api<{rows:typeof rows;total:number}>(`/api/after-order/sms/log?${new URLSearchParams({store_id:storeId||'0',page:String(page),status,q:query})}`,{signal:c.signal}).then(d=>{if(!c.signal.aborted){setRows(d.rows);setTotal(d.total)}}).catch(e=>{if(!c.signal.aborted)setError(String(e))});return()=>c.abort()},[api,storeId,page,status,query,tick])
  return <section className="epost-main grid gap-4" aria-label="SMS log"><div className="epost-queue-context"><div><h3>SMS log · last 30 days</h3><p>Includes messages created or updated in the last 30 days. Older audit history is retained. Refresh provider status from the message preview.</p></div><Button onClick={()=>setTick(t=>t+1)}>Refresh SMS log</Button></div>
    <div className="epost-filters"><label>Find order or phone<input className="form-control" value={query} onChange={e=>setQuery(e.target.value)}/></label><label>Status<select value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option>{['awaiting_approval','accepted','queued','sent','delivered','failed','provider_failed','undelivered','delivery_unknown','sending','blocked','rejected'].map(s=><option key={s}>{s}</option>)}</select></label></div>
    {error && <p role="alert">{error}</p>}<p>{total} SMS records</p>
    {rows.map(row=><article key={row.id} className="border p-4 grid gap-2"><div className="flex justify-between gap-4"><strong>{row.odoo_order_name} · {row.sender_domain}</strong><span>{row.status}</span></div><p>{row.test_mode?'TEST':'LIVE'} · {row.provider} · {row.recipient} · {row.attempts} attempts · {new Date(row.updated_at).toLocaleString()}</p><p>{row.last_error}</p><Button variant="outline" onClick={()=>setSelected(selected===row.email_id?null:row.email_id)}>Preview / retry / resend</Button>{selected===row.email_id && <SmsPreview api={api} emailId={row.email_id}/>}</article>)}
    <div className="flex gap-3"><Button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous</Button><span>Page {page}</span><Button disabled={page*30>=total} onClick={()=>setPage(p=>p+1)}>Next</Button></div>
  </section>
}
