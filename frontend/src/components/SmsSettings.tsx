import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import './sms-log.css'
type API = <T>(path: string, options?: RequestInit) => Promise<T>
type Config = { enabled: boolean; approval_required: boolean; provider: string; mappings: Record<string, unknown>; credentials: Record<string, boolean>; test_mode: boolean }
export function SmsSettings({ api }: { api: API }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [mapping, setMapping] = useState('{}')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [languages,setLanguages] = useState<{code:string;name:string;sent_language:string;fallback_reason:string;translation_method?:string;human_reviewed?:boolean}[]>([])
  useEffect(()=>{api<{languages:typeof languages}>('/api/after-order/languages').then(d=>setLanguages(d.languages)).catch(()=>{})},[api])
  useEffect(() => { api<Config>('/api/after-order/sms/settings').then(c => { setConfig(c); setMapping(JSON.stringify(c.mappings, null, 2)) }).catch(e => setNotice(String(e))) }, [api])
  async function save() {
    if (!config) return
    if (!config.approval_required && !window.confirm('Send eligible queued and future live SMS automatically? Recipient, website, cutoff, template, duplicate and financial-event checks still apply. This does not execute refunds or charges.')) return
    setBusy(true)
    try { setConfig(await api<Config>('/api/after-order/sms/settings', { method: 'POST', body: JSON.stringify({ enabled: config.enabled, approval_required: config.approval_required, confirm_release_pending: !config.approval_required, provider: config.provider, mappings: JSON.parse(mapping) }) })); setNotice('Saved. Existing SMS keeps its original provider. Eligible live drafts are processed by notification scheduling when approval bypass is enabled.'); }
    catch (e) { setNotice(String(e)) } finally { setBusy(false) }
  }
  return <section className="card p-4 grid gap-3"><h3 className="font-semibold">Customer SMS</h3><p>SMS has its own preview and approval setting. SMS failures never resend emails. Existing Odoo dispatch notifications remain unchanged.</p>
    {config && <><label><input type="checkbox" checked={config.enabled} onChange={e => setConfig({ ...config, enabled: e.target.checked })} /> Enable SMS companions</label>
      <label><input type="checkbox" checked={!config.approval_required} onChange={e => setConfig({ ...config, approval_required: !e.target.checked })} /> Bypass team approval for queued and future live SMS</label>
      <label>SMS engine <select className="form-select" value={config.provider} onChange={e => setConfig({ ...config, provider: e.target.value })}><option value="odoo">Odoo — existing SMS service</option><option value="msg91">MSG91</option><option value="twilio">Twilio</option></select></label>
      <p>{config.test_mode ? 'Test: only +19296526393, no approval for initial sends.' : config.approval_required ? 'Live: new-order, Shopify DTC dispatch and eligible delivery follow-up SMS send automatically. Other SMS require individual approval.' : 'Live: eligible queued and future SMS send automatically after safety checks. Test drafts are excluded from queue release.'}</p>
      <p>Supported events include new orders, dispatch delays, unavailable items, first parcel movement, delivery confirmation, reviews, delivery issues and verified payment/refund notices. Message approval bypass never executes refunds, charges or replacements. No reminder or lost-package SMS.</p>
      <p>{config.provider === 'odoo' ? 'Uses the order’s Odoo installation. Requires its SMS module and credits.' : config.credentials[config.provider] ? 'Runtime credentials present; sender and delivery need verification.' : 'Runtime credentials are not configured.'}</p>
      <p>MSG91 checks template approval again before sending: an approved customer-language template is preferred; otherwise approved English is used, including when a translation returns to pending. If neither is approved, sending is blocked. A changed manual preview needs fresh team approval.</p>
      <details><summary>Notification languages · {languages.length} configured locales</summary><p>The Odoo customer language is used. Incomplete translations and unapproved localized MSG91 templates fall back to English. Language is recorded in each message preview. Email coverage below does not imply MSG91 template approval. Machine-generated catalogs pass automated checks but have not been reviewed by a native-language speaker.</p><div className="grid gap-1">{languages.map(l=><p key={l.code}>{l.name} ({l.code}) — {l.fallback_reason?'English fallback':l.translation_method==='machine_generated'?'Translated · machine-generated':'Email catalog ready'}</p>)}</div></details>
      <details><summary>Website sender and template mappings</summary><p>Keys are store_id:website_id. Enable transactional_sms_enabled only after reviewing consent and destination requirements. No secrets here.</p>
        <pre className="text-xs whitespace-pre-wrap">{'{"1:2":{"transactional_sms_enabled":false,"twilio":{"sender":"+15551234567"},"msg91":{"sender":"HEADER","templates":{"expected_dispatch":{"template_id":"MSG91_ID","dlt_template_id":"DLT_ID","text":"##brand##: Dispatch update for ##order##. ##url##"}}}}}'}</pre>
        <textarea aria-label="SMS website mappings JSON" className="form-control w-full min-h-40 font-mono" value={mapping} onChange={e => setMapping(e.target.value)} />
        <p>Coolify runtime secrets: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, MSG91_AUTH_KEY. Twilio mappings also support messaging_service_sid.</p>
      </details><Button disabled={busy} onClick={() => void save()}>Save SMS settings</Button></>}{notice && <p role="status">{notice}</p>}
  </section>
}
type Row = { id: number; provider: string; recipient: string; test_mode: boolean; body: string; status: string; last_error?: string; attempts: number; approval_digest: string; can_resend?: boolean; provider_id?: string; language?:{requested_language?:string;sent_language?:string;fallback_reason?:string}; segment_estimate?:{encoding:string;parts:number;note:string} }
const smsLabels:Record<string,string> = {awaiting_approval:'Awaiting approval',accepted:'Accepted by provider',queued:'Queued',sent:'Sent',delivered:'Delivered',failed:'Failed',provider_failed:'Delivery failed',undelivered:'Undelivered',delivery_unknown:'Check delivery',sending:'Sending',blocked:'Blocked',rejected:'Rejected',processing:'Processing',cancelled:'Cancelled',canceled:'Cancelled'}
function SmsStatus({status}:{status:string}) { return <span className={`sms-status ${status==='delivered'?'is-success':['failed','provider_failed','undelivered','rejected','blocked'].includes(status)?'is-danger':status==='awaiting_approval'?'is-pending':''}`}>{smsLabels[status] || status}</span> }
export function SmsPreview({ api, emailId, onChange }: { api: API; emailId: number; onChange?:()=>void }) {
  const [row, setRow] = useState<Row | null>(null); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false)
  const [reason, setReason] = useState('Loading SMS…'); const [canPrepare, setCanPrepare] = useState(false)
  const [attempts, setAttempts] = useState<{attempt_number:number;status:string;created_at:string;error?:string;provider_id?:string}[]>([])
  const [confirm,setConfirm]=useState<'send'|'resend'|null>(null)
  async function load() { const d = await api<{ row: Row | null; reason?:string;can_prepare?:boolean;attempts?:typeof attempts }>(`/api/after-order/sms/email/${emailId}`); setRow(d.row);setReason(d.reason || '');setCanPrepare(!!d.can_prepare);setAttempts(d.attempts || []) }
  useEffect(() => { void load().catch(e => setNotice(String(e))) }, [emailId])
  async function act(path: string, body = {}) { setBusy(true); setNotice(''); try { const d = await api<{ error?: string }>(path, { method: 'POST', body: JSON.stringify(body) }); setNotice(d.error || 'SMS record updated.'); await load();onChange?.() } catch (e) { setNotice(String(e)) } finally { setBusy(false);setConfirm(null) } }
  return <section className="sms-preview" aria-busy={busy}>{row ? <>
      <div className="sms-preview-status"><SmsStatus status={row.status}/><span className="sms-mode">{row.test_mode?'TEST':'LIVE'}</span></div>
      <dl className="sms-metadata"><div><dt>Recipient</dt><dd>{row.recipient}</dd></div><div><dt>Provider</dt><dd>{row.provider.toUpperCase()}</dd></div></dl>
      <div><h3 className="sms-section-label">Message preview</h3><div className="sms-message-bubble">{row.body}</div></div>
      <p className="sms-help">Language: {row.language?.sent_language || 'Not recorded (legacy SMS)'}{row.language?.requested_language && <> · Requested: {row.language.requested_language}</>}{row.language?.fallback_reason && <><br/>{row.language.fallback_reason}</>}</p>
      {row.last_error && <p className="sms-error" role="alert">{row.last_error}</p>}
      {row.segment_estimate && <p className="sms-help">Estimated SMS parts: {row.segment_estimate.parts} ({row.segment_estimate.encoding}). {row.segment_estimate.note}</p>}
      <p className="sms-help">Accepted, queued or sent does not confirm delivery. Opening this preview does not send anything.</p>
      <div className="sms-actions">
      {['awaiting_approval', 'failed','provider_failed','undelivered'].includes(row.status) && row.attempts < 3 && <Button disabled={busy||!!confirm} onClick={()=>setConfirm('send')}>{row.attempts ? 'Retry failed SMS' : 'Approve & send SMS'}</Button>}
      {row.can_resend && <Button variant="outline" disabled={busy||!!confirm} onClick={()=>setConfirm('resend')}>Resend SMS</Button>}
      <Button variant="outline" disabled={busy||!!confirm} onClick={() => void act(`/api/after-order/sms/${row.id}/refresh`)}>Refresh delivery status</Button>
      </div>
      {confirm && <div className="sms-confirm"><strong>{confirm==='resend'?'Send another copy?':'Confirm this SMS'}</strong><p>This exact message will be sent to <strong>{row.recipient}</strong>. {confirm==='resend'?'It was already delivered. Another charge may apply.':'SMS charges may apply.'}</p><div className="sms-actions"><Button disabled={busy} onClick={()=>void act(`/api/after-order/sms/${row.id}/${confirm==='resend'?'resend':'approve-send'}`,{approval_digest:row.approval_digest,...(confirm==='resend'?{confirm_duplicate_charge:true}:{})})}>{busy?'Sending…':'Confirm & send'}</Button><Button variant="outline" disabled={busy} onClick={()=>setConfirm(null)}>Cancel</Button></div></div>}
      <details className="sms-technical"><summary>Delivery details & attempt history ({row.attempts})</summary><p>Provider reference: {row.provider_id || 'Not available'}</p>{attempts.length?attempts.map(a=><div className="sms-attempt" key={a.attempt_number}><strong>Attempt {a.attempt_number} · {smsLabels[a.status] || a.status}</strong><small>{new Date(a.created_at).toLocaleString()}</small>{a.error && <p>{a.error}</p>}</div>):<p>Detailed attempt history is unavailable for this older record.</p>}</details>
    </> : <><p>{reason}</p>{canPrepare && <Button disabled={busy} onClick={() => void act(`/api/after-order/sms/email/${emailId}/prepare`)}>Prepare companion SMS</Button>}</>}{notice && <p role="status">{notice}</p>}</section>
}

export function SmsLog({api,storeId}:{api:API;storeId:string}) {
  const [rows,setRows]=useState<(Row & {email_id:number;odoo_order_name:string;sender_domain:string;updated_at:string})[]>([])
  const [total,setTotal]=useState(0);const [page,setPage]=useState(1);const [status,setStatus]=useState('');const [query,setQuery]=useState('');const [selected,setSelected]=useState<number|null>(null);const [error,setError]=useState('');const [tick,setTick]=useState(0)
  const [loading,setLoading]=useState(true)
  useEffect(()=>{setPage(1);setSelected(null)},[storeId,status,query])
  useEffect(()=>{const c=new AbortController();setError('');setLoading(true);api<{rows:typeof rows;total:number}>(`/api/after-order/sms/log?${new URLSearchParams({store_id:storeId||'0',page:String(page),status,q:query})}`,{signal:c.signal}).then(d=>{if(!c.signal.aborted){setRows(d.rows);setTotal(d.total)}}).catch(e=>{if(!c.signal.aborted){setError(String(e));setRows([])}}).finally(()=>{if(!c.signal.aborted)setLoading(false)});return()=>c.abort()},[api,storeId,page,status,query,tick])
  return <section className="sms-log-panel" aria-label="SMS log" aria-busy={loading}>
    <div className="sms-log-toolbar"><label>Search<input className="form-control" placeholder="Order, phone or website" value={query} onChange={e=>setQuery(e.target.value)}/></label><label>Status<select className="form-select" value={status} onChange={e=>setStatus(e.target.value)}><option value="">All statuses</option>{Object.entries(smsLabels).filter(([s])=>s!=='canceled').map(([s,label])=><option key={s} value={s}>{label}</option>)}</select></label><Button variant="outline" disabled={loading} onClick={()=>setTick(t=>t+1)}>Refresh log</Button></div>
    <div className="sms-log-summary"><span>{loading?'Loading messages…':`${total} messages`}</span><span>Last 30 days · older history retained</span></div>
    {error && <p className="sms-error" role="alert">{error}</p>}
    {!loading && !error && !rows.length && <div className="sms-empty"><h3>No messages found</h3><p>{status==='awaiting_approval'?'There are no SMS messages waiting for approval.':'Try another status or search term.'}</p></div>}
    <div className="sms-log-rows">{rows.map(row=><article key={row.id} className="sms-log-row"><div><strong>{row.odoo_order_name}</strong><small>{row.sender_domain}</small></div><div><strong className="sms-phone">{row.recipient}</strong><small>{row.test_mode?'Test message':'Customer message'} · {row.provider.toUpperCase()}</small></div><div><SmsStatus status={row.status}/><small>{new Date(row.updated_at).toLocaleString()}</small></div><Button variant={row.status==='awaiting_approval'?'default':'outline'} onClick={()=>setSelected(row.email_id)}>{row.status==='awaiting_approval'?'Review & approve':'View message'}</Button></article>)}</div>
    <div className="sms-log-pagination"><span>Page {page}</span><div className="sms-actions"><Button variant="outline" disabled={loading||page<=1} onClick={()=>setPage(p=>p-1)}>Previous</Button><Button variant="outline" disabled={loading||page*30>=total} onClick={()=>setPage(p=>p+1)}>Next</Button></div></div>
    <Dialog open={selected!==null} onOpenChange={open=>{if(!open)setSelected(null)}}><DialogContent className="sms-message-dialog"><DialogHeader><DialogTitle>{rows.find(r=>r.email_id===selected)?.odoo_order_name || 'SMS'} · Message details</DialogTitle><DialogDescription>Preview the message and review its delivery or approval status.</DialogDescription></DialogHeader>{selected!==null && <SmsPreview key={selected} api={api} emailId={selected} onChange={()=>setTick(t=>t+1)}/>}</DialogContent></Dialog>
  </section>
}
