import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type Settings = { enabled:boolean; test_mode:boolean; store_ids:number[]; receiving_address:string; forwarders:string; authserv_ids:string; public_base_url:string; worker_status?:Record<string,string> }
type Payment = {emails:{kind:string;state:string;error?:string}[];id:number;status:string;payment_link?:string;last_error?:string;snapshot:{order_number:string;customer_email:string;amount_cents:number;website_name:string}}
type Props = {stores:{id:number;name:string}[];storeId:string;api:<T>(path:string,options?:RequestInit)=>Promise<T>}
export function RelayPayments({stores,storeId,api}:Props){
 const [settings,setSettings]=useState<Settings|null>(null),[rows,setRows]=useState<Payment[]>([]),[receipts,setReceipts]=useState<{email_id:string;reason:string}[]>([]),[error,setError]=useState(''),[token,setToken]=useState('')
 const refresh=()=>api<{rows:Payment[];receipts:{email_id:string;reason:string}[]}>(`/api/relay/payments?store_id=${Number(storeId)||0}`).then(r=>{setRows(r.rows);setReceipts(r.receipts)}).catch(e=>setError(String(e)))
 useEffect(()=>{api<Settings>('/api/relay/settings').then(setSettings).catch(e=>setError(String(e)));void refresh();const t=setInterval(refresh,15000);return()=>clearInterval(t)},[storeId])
 const save=async()=>{try{await api('/api/relay/settings',{method:'POST',body:JSON.stringify(settings)});setError('Settings saved')}catch(e){setError(String(e))}}
 return <section className="card mb-4"><div className="card-body"><h2>Relay invoice payments</h2><p>Links are matched against the Odoo order, billing customer and exact USD amount. The worker checks receipts every minute while enabled.</p>
 {error&&<p role="status">{error}</p>}
 <details><summary>Connection and receipt settings</summary>{settings&&<div className="grid gap-3 py-3">
 <label><input type="checkbox" checked={settings.enabled} onChange={e=>setSettings({...settings,enabled:e.target.checked})}/> Enable Relay worker</label>
 <label><input type="checkbox" checked={settings.test_mode} onChange={e=>setSettings({...settings,test_mode:e.target.checked})}/> Hold customer emails and order confirmation (test mode)</label>
 <fieldset><legend>Connected websites</legend>{stores.map(store=><label key={store.id} className="block"><input type="checkbox" checked={settings.store_ids.includes(store.id)} onChange={e=>setSettings({...settings,store_ids:e.target.checked?[...settings.store_ids,store.id]:settings.store_ids.filter(id=>id!==store.id)})}/> {store.name}</label>)}<p>If an Odoo database has multiple websites, set its website ID in the app's store settings.</p></fieldset>
 <label>Public app URL<Input value={settings.public_base_url} onChange={e=>setSettings({...settings,public_base_url:e.target.value})}/></label>
 <label>Resend receiving address<Input value={settings.receiving_address} onChange={e=>setSettings({...settings,receiving_address:e.target.value})}/></label>
 <label>Trusted forwarding mailboxes, one per line<textarea className="form-control" value={settings.forwarders} onChange={e=>setSettings({...settings,forwarders:e.target.value})}/></label>
 <label>Verified receiving gateway authserv IDs, one per line<textarea className="form-control" value={settings.authserv_ids} onChange={e=>setSettings({...settings,authserv_ids:e.target.value})}/></label>
 <p>Gateway IDs must come from a verified Resend receipt header. A sender address alone is not proof of payment. The app’s global email test mode also holds live delivery.</p>
 <Button onClick={save}>Save settings</Button><Button variant="outline" onClick={async()=>{try{const r=await api<{token:string}>('/api/relay/extension-token',{method:'POST'});setToken(r.token)}catch(e){setError(String(e))}}}>Generate / replace extension upload token</Button>
 {token&&<label>Copy into the Relay extension (shown once)<Input readOnly value={token}/></label>}
 {Object.entries(settings.worker_status||{}).filter(([,v])=>v).map(([k,v])=><p key={k}>{k.replaceAll('_',' ')}: {v}</p>)}
 </div>}</details>
 <div style={{overflowX:'auto'}}><table className="table"><thead><tr><th>Order</th><th>Customer</th><th>USD</th><th>Status</th><th>Payment link</th></tr></thead><tbody>{rows.map(r=><tr key={r.id}><td>{r.snapshot.order_number}<br/>{r.snapshot.website_name}</td><td>{r.snapshot.customer_email}</td><td>{(r.snapshot.amount_cents/100).toFixed(2)}</td><td>{r.status}<br/>{r.last_error}{r.emails.map(e=><p key={e.kind}>{e.kind} email: {e.state}{e.error&&<span> — {e.error}</span>}</p>)}</td><td>{r.payment_link?<a href={r.payment_link} target="_blank" rel="noreferrer">Open payment link</a>:'Waiting for extension'}</td></tr>)}</tbody></table></div>
 {!rows.length&&<p>No imported Relay payment requests for this store.</p>}
 {receipts.length>0&&<details><summary>Payment notices requiring review ({receipts.length})</summary>{receipts.map(r=><p key={r.email_id}>{r.reason} — {r.email_id} <Button variant="outline" onClick={async()=>{try{await api(`/api/relay/receipts/${encodeURIComponent(r.email_id)}/recheck`,{method:'POST'});await refresh()}catch(e){setError(String(e))}}}>Recheck receipt</Button></p>)}</details>}
 </div></section>
}
