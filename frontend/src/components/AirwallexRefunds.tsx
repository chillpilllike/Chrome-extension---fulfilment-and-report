import { type Option, type Schema, schemaSelectors, schemaParams, reconcileSchema, changedSchemaValues } from './refundSchema'
import { useEffect, useId, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type Props = { stores: {id:number;name:string}[]; storeId:string; api:<T>(path:string, options?:RequestInit)=>Promise<T> }
type Order = {id:number;name:string;partner_id:[number,string];amount_total:number;currency_id:[number,string];state:string}
type History = {id:string;amount:string;currency:string;status:string;reference:string;source:string;created_at:string}
type DailyLimit = {successful:number;pending:number;limit:number;used:number;remaining:number;timezone:string;date:string;resets_at:string}
type PaymentMatch = {provider:string;method:string;customer:string;transaction:string;amount?:string;currency?:string;deposit_reference?:string;deposit_id?:string;payer?:string}
type Snapshot = {order_equivalent:string;conversion_rate:string;payment_matches:PaymentMatch[];store_id:number;order_id:number;order_name:string;customer:string;order_value:string;order_currency:string;currency:string;paid:string;remaining:string;rounding:string;refunded_reserved:string;accounting_deduction:string;evidence:string;defaults:Record<string,string>;history:History[]}
type FundingCurrency = {currency:string;status:'available'|'empty'}
type Review = {source_currency:string;estimated_source_amount:string;conversion:boolean;review_token:string;order_name:string;amount:string;currency:string;recipient:string;destination:string;method:string;fees:string}
type Payout = {email_status?:string;email_error?:string;email_message_id?:number;funding_currency?:string;mapping_note?:string;source?:string;mapping_status?:string;store_id?:number;store_name?:string;order_id?:number;request_id:string;order_name:string;amount:string;currency:string;status:string;recipient:string;last_error:string;transfer_id:string;created_at:string;fee_amount?:string;fee_currency?:string}
const errorMessage = (error:unknown) => {
 const message=error instanceof Error?error.message:String(error)
 if(/failed to fetch|networkerror|load failed/i.test(message))return 'Unable to reach the app. Check your connection, then check refund history before retrying.'
 if(/<html|<!doctype|bad gateway|gateway timeout|internal server error|service unavailable/i.test(message))return 'The server could not finish this request. Check refund history and transfer status before trying again. If a refund is pending or needs a status check, do not send another one.'
 if(message.trim().startsWith('{')||message.trim().startsWith('['))return 'Some refund details could not be accepted. Check the form and try again.'
 return message.replace(/^Error:\s*/, '')
}
const statusLabel = (status:string) => ({PAID:'Paid',PROCESSING:'Processing',SCHEDULED:'Scheduled',SUBMITTING:'Sending',UNKNOWN:'Needs status check',FAILED:'Failed',CANCELLED:'Cancelled',CANCELED:'Cancelled',PENDING_APPROVAL:'Awaiting approval',PENDING:'Pending'}[status]||'Status needs review')
const format = (amount:string|number,currency:string) => {
 try { return new Intl.NumberFormat(undefined,{style:'currency',currency}).format(Number(amount)) + ` ${currency}` }
 catch { return `${amount} ${currency}` }
}
function InstitutionSelect({options,value,onChange,label,description}:{options:Option[];value:string;onChange:(value:string)=>void;label:string;description?:string}) {
 const id=useId()
 const [search,setSearch]=useState('')
 const query=search.trim().toLowerCase()
 const matches=options.filter(o=>`${o.value} ${o.label}`.toLowerCase().includes(query))
 const selected=options.find(o=>o.value===value)
 return <div className="grid content-start gap-1 text-sm">
  <label htmlFor={id}>{label}</label>
  <Input aria-label="Search financial institution" placeholder="Search institution number or bank name" value={search} onChange={e=>setSearch(e.target.value)} autoComplete="off"/>
  <select id={id} className="w-full rounded-md border bg-background p-2" value={value} onChange={e=>{onChange(e.target.value);setSearch('')}}>
   <option value="">Select…</option>
   {selected&&!matches.includes(selected)&&<option value={selected.value} hidden>{selected.value} — {selected.label}</option>}
   {matches.map(o=><option key={o.value} value={o.value}>{o.value} — {o.label}</option>)}
  </select>
  {query&&<span role="status" className="text-xs text-muted-foreground">{matches.length?`${matches.length} matching institution${matches.length===1?'':'s'}`:'No matching institutions. Try another code or bank name.'}</span>}
  {description&&<span className="text-xs text-muted-foreground">{description}</span>}
 </div>
}
export function AirwallexRefunds({stores,storeId,api}:Props) {
 const [selectedStore,setSelectedStore]=useState(storeId || ''), [query,setQuery]=useState('')
 const [orders,setOrders]=useState<Order[]>([]),[snapshot,setSnapshot]=useState<Snapshot|null>(null)
 const [schema,setSchema]=useState<Schema|null>(null),[values,setValues]=useState<Record<string,string>>({})
 const [amount,setAmount]=useState(''),[editing,setEditing]=useState(false),[editDialog,setEditDialog]=useState(false),[editReason,setEditReason]=useState('')
 const [recipientConfirmed,setRecipientConfirmed]=useState(false),[otherChecked,setOtherChecked]=useState(false)
 const [review,setReview]=useState<Review|null>(null),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('')
 const [busySeconds,setBusySeconds]=useState(0)
 const [history,setHistory]=useState<Payout[]>([]),[connected,setConnected]=useState(false),[loadingSchema,setLoadingSchema]=useState(false),[schemaError,setSchemaError]=useState(false)
 const [funding,setFunding]=useState<FundingCurrency[]>([]),[sourceCurrency,setSourceCurrency]=useState('')
 const refreshFunding=()=>api<{currencies:FundingCurrency[]}>('/api/airwallex/refunds/funding-currencies').then(r=>{setFunding(r.currencies);setConnected(true)}).catch(e=>{setConnected(false);setFunding([]);setError(errorMessage(e))})
 const [daily,setDaily]=useState<DailyLimit|null>(null)
 const refreshLimits=()=>api<DailyLimit>('/api/airwallex/refunds/limits').then(setDaily).catch(e=>{setDaily(null);setError(errorMessage(e))})
 const errorPanel=useRef<HTMLDivElement>(null)
 const generation=useRef(0), schemaGeneration=useRef(0), actionLock=useRef(false)
 const post=<T,>(path:string,data:unknown)=>api<T>(`/api/airwallex/refunds${path}`,{method:'POST',body:JSON.stringify(data)})
 const refreshHistory=()=>api<{rows:Payout[]}>('/api/airwallex/refunds/history').then(r=>setHistory(r.rows)).catch(e=>setError(errorMessage(e)))
 useEffect(()=>{void refreshHistory();void refreshLimits();void refreshFunding();const timer=setInterval(()=>{if(!actionLock.current&&document.visibilityState==='visible'){void refreshHistory();void refreshLimits()}},30000);return()=>clearInterval(timer)},[])
 useEffect(()=>{setBusySeconds(0);if(!busy)return;const started=Date.now();const timer=setInterval(()=>setBusySeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>clearInterval(timer)},[busy])
 const reset=()=>{generation.current++;schemaGeneration.current++;setSnapshot(null);setSchema(null);setValues({});setReview(null);setOrders([]);setError('');setNotice('');setRecipientConfirmed(false);setOtherChecked(false);setEditing(false);setEditReason('')}
 useEffect(()=>{reset();setSelectedStore(storeId||'')},[storeId])
 async function loadSchema(next:Record<string,string>, initial=false) {
  const ticket=++schemaGeneration.current
  setLoadingSchema(true);setReview(null);setError('')
  try {
   let current=initial?null:schema, merged=next
   for(let attempt=0;attempt<5;attempt++){
    const params=schemaParams(merged,current)
    const result=await post<Schema>('/schema',params)
    if(ticket!==schemaGeneration.current)return
    merged=reconcileSchema(merged,result)
    const updated=schemaParams(merged,result)
    if(Object.keys({...params,...updated}).every(k=>params[k]===updated[k])){
     setSchema(result);setValues(merged);setSchemaError(false);return
    }
    current=result
   }
   throw new Error('Airwallex could not confirm the recipient fields. Select the country and transfer method again.')
  }catch(e){if(ticket===schemaGeneration.current){setSchemaError(true);setError(errorMessage(e))}}
  finally{if(ticket===schemaGeneration.current)setLoadingSchema(false)}
 }
 async function run(label:string, fn:()=>Promise<void>) {
  if(actionLock.current)return
  actionLock.current=true;setBusy(label);setError('');setNotice('')
  try{await fn()}catch(e){setError(errorMessage(e));requestAnimationFrame(()=>errorPanel.current?.scrollIntoView({behavior:'smooth',block:'center'}))}finally{actionLock.current=false;setBusy('')}
 }
 async function choose(order:Order, targetStore=selectedStore){
  const ticket=++generation.current
  setSnapshot(null);setSchema(null);setReview(null);setEditing(false);setEditReason('');setRecipientConfirmed(false);setOtherChecked(false)
  await run('Checking payment and previous refunds',async()=>{
   const s=await api<Snapshot>(`/api/airwallex/refunds/orders/${targetStore}/${order.id}`)
   if(ticket!==generation.current)return
   setSnapshot(s);setAmount(s.remaining);setSourceCurrency(s.currency);setOrders([]);void refreshFunding()
   if(Number(s.remaining)>0)await loadSchema(s.defaults,true)
  })
 }
 function changeField(path:string,value:string){
  const next=changedSchemaValues(values,schema,path,value)
  setReview(null);setRecipientConfirmed(false);setValues(next)
  if(schemaSelectors(schema)[path]&&schema?.fields.find(f=>f.path===path)?.field.type!=='INPUT')void loadSchema(next)
 }

 const renderedFields=(schema?.fields||[]).filter(f=>f.enabled!==false&&f.path!=='nickname'&&f.path!=='beneficiary.type')
 const missingFields=renderedFields.filter(f=>f.required&&!(values[f.path==='transfer_methods'?'transfer_method':f.path]||f.field.default||'').trim()).map(f=>f.field.label)
 const canReview=missingFields.length===0&&funding.some(f=>f.currency===sourceCurrency&&f.status==='available')&&!!daily&&daily.remaining>0&&!!snapshot&&!!schema&&!schemaError&&!loadingSchema&&!busy&&recipientConfirmed&&otherChecked&&Number(amount)>0&&Number(amount)<=Number(snapshot.remaining)&&(!editing||!!editReason.trim())

 return <div className="space-y-5">
  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-card p-5"><div><h2 className="text-xl font-semibold">Refund an order</h2><p className="mt-1 text-sm text-muted-foreground">Return customer funds using your shared Airwallex account. After success, the customer receives a confirmation from the order’s website with masked account details.</p></div><span className={`rounded-full px-3 py-1 text-xs font-medium ${connected?'bg-emerald-50 text-emerald-800':'bg-amber-50 text-amber-800'}`}>{connected?'Airwallex connected':'Checking Airwallex connection'}</span></div>
  <div role="status" className="rounded-lg border bg-muted/40 p-4 text-sm">{daily?<><strong>Daily refund limit: {daily.successful} / {daily.limit} successful · {daily.pending} pending · {daily.remaining} available</strong><p className="mt-1 text-muted-foreground">Shared across all stores and staff. Resets at midnight India time (Asia/Kolkata). Up to 5 successful refunds from each day’s submissions. Pending or uncertain transfers temporarily reserve a slot; confirmed failed and cancelled transfers do not count. Airwallex transfers labelled “refund” are included.</p>{daily.remaining===0&&<p className="mt-2 font-medium text-red-700">All daily slots are used or reserved. A failed or cancelled pending transfer frees a slot; otherwise the limit resets at midnight.</p>}</>:"Checking the daily refund limit…"}</div>
  {busy&&busySeconds>=5&&<p role="status" className="text-sm text-muted-foreground">{busy} · {busySeconds}s. {busySeconds>=15?"Odoo or Airwallex is taking longer than usual. Keep this page open; do not start a second refund.":"Checking with Odoo and Airwallex…"}</p>}
  {error&&<div ref={errorPanel} role="alert" className="whitespace-pre-line rounded-lg border border-red-200 bg-red-50 p-4 text-red-800">{error}</div>}
  {notice&&<div role="status" className="rounded-lg border bg-blue-50 p-4 text-blue-900">{notice}</div>}
  {busy&&<p role="status" className="text-sm text-muted-foreground">{busy}…</p>}
  <section className="rounded-xl border bg-card p-5 space-y-4"><h3 className="font-semibold">1. Select an order</h3>
   <form className="flex flex-wrap items-end gap-3" onSubmit={e=>{e.preventDefault();void run('Searching orders',async()=>{const ticket=generation.current;const rows=(await api<{rows:Order[]}>(`/api/airwallex/refunds/orders?store_id=${selectedStore}&q=${encodeURIComponent(query)}`)).rows;if(ticket!==generation.current)return;setOrders(rows);if(!rows.length)setNotice('No matching orders found in this store.')})}}>
    <label className="grid gap-1 text-sm">Store<select aria-label="Refund store" className="rounded-md border bg-background p-2" value={selectedStore} disabled={!!busy} onChange={e=>{reset();setSelectedStore(e.target.value)}}><option value="">Select store</option>{stores.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
    <label className="grid gap-1 text-sm">Order number<Input placeholder="e.g. NC28342" value={query} disabled={!!busy} onChange={e=>setQuery(e.target.value)} /></label>
    <Button type="submit" disabled={!selectedStore||query.trim().length<2||!!busy}>Find order</Button>
   </form>
   {orders.length>0&&<div className="divide-y rounded-lg border">{orders.map(o=><button type="button" className="flex w-full flex-wrap justify-between gap-2 p-3 text-left hover:bg-muted" key={o.id} disabled={!!busy} onClick={()=>void choose(o)}><span><strong>{o.name}</strong> · {o.partner_id[1]}</span><span>{format(o.amount_total,o.currency_id[1])} · {o.state}</span></button>)}</div>}
  </section>
  {snapshot&&<>
   <section className="rounded-xl border bg-card p-5"><div className="flex flex-wrap justify-between gap-3"><div><h3 className="font-semibold">{snapshot.order_name} · {snapshot.customer}</h3><p className="mt-1 text-xs text-muted-foreground">Verified: {snapshot.evidence}</p></div><Button variant="outline" disabled={!!busy} onClick={()=>void choose({id:snapshot.order_id} as Order)}>Refresh payment checks</Button></div>
    <div className="mt-5 grid gap-4" style={{gridTemplateColumns:"repeat(auto-fit, minmax(160px, 1fr))"}}>{[['Order value',format(snapshot.order_value,snapshot.order_currency)],['Order value in refund currency',format(snapshot.order_equivalent,snapshot.currency)],['Verified payment received',format(snapshot.paid,snapshot.currency)],['Refunded / reserved',format(snapshot.refunded_reserved,snapshot.currency)],['Available to refund',format(snapshot.remaining,snapshot.currency)]].map(([label,value])=><div className="rounded-lg bg-muted/50 p-3" key={label}><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-lg font-semibold">{value}</p></div>)}</div>
    <p className="mt-3 text-sm text-muted-foreground">Refunds cannot exceed the lower of the order value in {snapshot.currency} and verified funds received, less previous refunds and reservations. {snapshot.currency!==snapshot.order_currency?`Original payment rate: 1 ${snapshot.order_currency} = ${snapshot.conversion_rate} ${snapshot.currency}.`:''}</p>
    <div className="mt-4 rounded-lg border p-4 space-y-3"><h4 className="font-medium">Original payment and customer match</h4>{snapshot.payment_matches.map((p,i)=><div className="text-sm space-y-1" key={i}><p><strong>Paid by: {p.provider}{p.method?` · ${p.method}`:''}</strong></p><p>Payment: {p.transaction}{p.amount&&p.currency?` · ${format(p.amount,p.currency)}`:''}</p><p>Matched Odoo customer: {p.customer} · Order: {snapshot.order_name}</p>{p.deposit_id&&<><p>Settled Airwallex deposit: {p.deposit_reference}</p><p>Bank payer: {p.payer||'Not supplied by Airwallex'}</p><p className="text-xs text-muted-foreground">The deposit reference, amount and currency match the linked payment. Verify the bank payer and refund recipient against the customer.</p></>}</div>)}</div>
    {Number(snapshot.accounting_deduction)>0&&<p className="mt-3 text-sm">An additional {format(snapshot.accounting_deduction,snapshot.currency)} is held for Odoo credit notes or provider refunds.</p>}
    {snapshot.history.length>0&&<details className="mt-4" open={Number(snapshot.remaining)===0}><summary className="cursor-pointer text-sm font-medium">Previous refunds and reservations ({snapshot.history.length})</summary><div className="mt-2 divide-y">{snapshot.history.map(h=><div className="flex flex-wrap justify-between gap-2 py-2 text-sm" key={h.id}><span>{h.reference} · {h.source}</span><span>{format(h.amount,h.currency)} · {statusLabel(h.status)}</span></div>)}</div></details>}
    {Number(snapshot.remaining)===0&&<p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">No remaining amount is available to refund. Existing refunds and pending transfers are included.</p>}
   </section>
   {Number(snapshot.remaining)>0&&<section className="rounded-xl border bg-card p-5 space-y-5"><h3 className="font-semibold">2. Amount and recipient</h3>
    <div className="flex flex-wrap items-end gap-3"><label className="grid gap-1 text-sm">Refund amount<Input aria-label="Refund amount" inputMode="decimal" type="number" min={snapshot.rounding} max={snapshot.remaining} step={snapshot.rounding} value={amount} disabled={!editing||!!busy} onChange={e=>{setAmount(e.target.value);setReview(null)}} /></label><label className="grid gap-1 text-sm">Currency<Input value={snapshot.currency} readOnly className="w-24" /></label><Button variant="outline" disabled={!!busy} onClick={()=>setEditDialog(true)}>Edit cost</Button></div>
    <p className="text-sm">Maximum allowed: <strong>{format(snapshot.remaining,snapshot.currency)}</strong>. Amount and currency default to the remaining verified payment.</p>
    {Number(amount)>Number(snapshot.remaining)&&<p role="alert" className="text-sm text-red-700">Amount exceeds the maximum refundable value. Reduce it before continuing.</p>}
    <div className="flex flex-wrap items-end gap-3"><label className="grid gap-1 text-sm">Pay from currency<select aria-label="Pay from currency" className="rounded-md border bg-background p-2" value={sourceCurrency} disabled={!!busy} onChange={e=>{setSourceCurrency(e.target.value);setReview(null)}}><option value="">Select funding currency</option>{!funding.some(f=>f.currency===sourceCurrency)&&sourceCurrency&&<option value={sourceCurrency} disabled>{sourceCurrency} — unavailable</option>}{funding.map(f=><option key={f.currency} value={f.currency} disabled={f.status==='empty'}>{f.currency}{f.status==='empty'?' — 0 balance':''}</option>)}</select></label><Button variant="outline" disabled={!!busy} onClick={()=>void refreshFunding()}>Refresh currencies</Button></div>
    <p className="text-sm text-muted-foreground">Wallet amounts are private. A currency listed without “0 balance” has funds, but may not cover this refund. {sourceCurrency!==snapshot.currency?`Airwallex will convert ${sourceCurrency} to ${snapshot.currency}; the customer’s refund amount stays fixed. The final exchange rate and additional fees apply when submitted.`:`This refund will use your ${snapshot.currency} balance.`}</p>
    {editing&&<label className="grid gap-1 text-sm">Reason for changing the amount<Input value={editReason} onChange={e=>{setEditReason(e.target.value);setReview(null)}} placeholder="e.g. Partial refund for one unavailable item" /></label>}
    {snapshot.currency!==snapshot.order_currency&&<p className="rounded-lg bg-blue-50 p-3 text-sm">The customer paid in {snapshot.currency}. This refund uses the original locked payment conversion from {snapshot.order_currency}.</p>}
    <p className="text-sm text-muted-foreground">Choose the recipient’s bank country and a supported method. For Australian PayID, choose Local, then NPP, then the email, phone or business identifier type. Available options depend on the country, currency and account-holder type. Confirm the account details with the customer. The business pays Airwallex fees in addition to the refund amount.</p>
    {loadingSchema&&<p role="status" className="text-sm">Loading Airwallex’s supported methods and required fields…</p>}
    <fieldset disabled={loadingSchema||!!busy} className="grid gap-4 md:grid-cols-2">
     {renderedFields.map(f=>{
      const path=f.path==='transfer_methods'?'transfer_method':f.path, value=values[path]||'', fixed=path==='beneficiary.bank_details.account_currency'||(/account_routing_type[12]$/.test(path)&&!f.field.options?.length)
      const institution=/account_routing_value[12]$/.test(path)
      const options=institution?f.field.options?.slice().sort((a,b)=>a.value.localeCompare(b.value)):f.field.options, choice=options?.find(o=>o.value===value)
      if(institution&&options?.length)return <InstitutionSelect key={path} options={options} value={value} onChange={v=>changeField(path,v)} label={`${f.field.label}${f.required?' *':''}`} description={choice?.description||f.field.description}/>
      return <label className="grid content-start gap-1 text-sm" key={path}>{f.field.label}{f.required?' *':''}
       {options?.length?<select className="w-full rounded-md border bg-background p-2" value={value} disabled={fixed} onChange={e=>changeField(path,e.target.value)}><option value="">Select…</option>{options.map(o=><option key={o.value} value={o.value}>{institution?`${o.value} — ${o.label}`:o.label}</option>)}</select>:<Input value={value} readOnly={fixed} maxLength={500} placeholder={f.field.placeholder||f.field.example||undefined} type={path.endsWith('security_question_answer')?'password':'text'} autoComplete="off" onBlur={()=>{if(f.field.refresh)void loadSchema(values)}} onChange={e=>changeField(path,e.target.value)}/>}
       {(choice?.description||f.field.description)&&<span className="text-xs whitespace-pre-line text-muted-foreground">{choice?.description||f.field.description}</span>}
      </label>
     })}
    </fieldset>
    <div className="space-y-3 border-t pt-4 text-sm"><label className="flex items-start gap-2"><input className="mt-1" type="checkbox" checked={recipientConfirmed} onChange={e=>{setRecipientConfirmed(e.target.checked);setReview(null)}}/>I verified that these recipient details belong to this customer.</label><label className="flex items-start gap-2"><input className="mt-1" type="checkbox" checked={otherChecked} onChange={e=>{setOtherChecked(e.target.checked);setReview(null)}}/>I checked for refunds made through other payment providers or without an order reference. Any such refunds are recorded in Odoo before proceeding.</label></div>
    {!loadingSchema&&missingFields.length>0&&<p className="text-sm text-amber-800" role="status">Complete these required details before reviewing: {missingFields.join(', ')}.</p>}
    {!loadingSchema&&!missingFields.length&&(!recipientConfirmed||!otherChecked)&&<p className="text-sm text-muted-foreground">Confirm both checks above to review this refund.</p>}
    <Button disabled={!canReview} onClick={()=>void run('Validating refund with Airwallex',async()=>{const ticket=generation.current;const checked=await post<Review>('/review',{store_id:snapshot.store_id,order_id:snapshot.order_id,amount,source_currency:sourceCurrency,fields:values,edit_acknowledged:editing,edit_reason:editReason,recipient_confirmed:recipientConfirmed,other_refunds_checked:otherChecked});if(ticket===generation.current)setReview(checked)})}>Review refund</Button>
   </section>}
  </>}
  <section className="rounded-xl border bg-card p-5"><div className="flex justify-between gap-2"><h3 className="font-semibold">Refund history · app and manual Airwallex transfers</h3><Button variant="outline" disabled={!!busy} onClick={()=>void run('Syncing Airwallex refund history',async()=>{const result=await post<{total:number;mapped:number;unresolved:number}>('/history/sync',{});await refreshHistory();await refreshLimits();setNotice(`${result.total} refunds synced · ${result.mapped} matched to orders · ${result.unresolved} need reconciliation.`)})}>Sync Airwallex history</Button></div><p className="mt-2 text-sm text-muted-foreground">Submitting, processing and uncertain requests reserve the amount. A failed transfer requires finance reconciliation before sending a replacement.</p>
   {!history.length?<p className="py-5 text-sm text-muted-foreground">No refund history loaded. Sync Airwallex history to import earlier transfers.</p>:<div className="mt-4 overflow-x-auto"><table className="w-full text-sm"><thead><tr className="border-b text-left"><th className="p-2">Order / recipient</th><th className="p-2">Source / mapping</th><th className="p-2">Amount</th><th className="p-2">Status</th><th className="p-2">Action</th></tr></thead><tbody>{history.map(h=><tr className="border-b" key={h.transfer_id||h.request_id}><td className="p-2"><strong>{h.order_name}</strong><br/>{h.recipient}<small className="block text-muted-foreground">{h.store_name} · {h.created_at?new Date(h.created_at.replace(/([+-]\d{2})(\d{2})$/,'$1:$2')).toLocaleDateString():''}</small></td><td className="p-2">{h.source||'App'}<small className="block">{h.mapping_note||(h.mapping_status==='matched'?'Order matched':h.mapping_status==='ambiguous'?'Multiple matching orders':h.mapping_status==='unavailable'?'Order lookup unavailable':'Order not found')}</small></td><td className="p-2">{format(h.amount,h.currency)}{h.funding_currency&&<small className="block">Paid from: {h.funding_currency}</small>}{h.fee_amount&&<small className="block">Fee: {h.fee_amount} {h.fee_currency}</small>}</td><td className="p-2">{statusLabel(h.status)}{h.last_error&&<p className="max-w-xs whitespace-pre-line text-xs text-amber-800">{h.last_error}</p>}<small className="block text-muted-foreground">{h.transfer_id||h.request_id}</small>{h.email_status&&<small className="block">Customer email: {({queued:"Queued",sending:"Sending",sent:"Sent",retry:"Checking delivery",held:"Needs attention",failed:"Failed"} as Record<string,string>)[h.email_status]||"Needs attention"}</small>}{h.email_error&&<small className="block max-w-xs text-amber-800">{h.email_error}</small>}</td><td className="p-2">{h.store_id&&h.order_id&&<Button variant="outline" disabled={!!busy} onClick={()=>{setSelectedStore(String(h.store_id));setQuery(h.order_name);void choose({id:h.order_id} as Order,String(h.store_id))}}>Open order</Button>}{h.source!=='Manual Airwallex'&&<Button variant="outline" disabled={!!busy} onClick={()=>void run('Checking transfer status',async()=>{await post(`/${h.request_id}/refresh`,{});await refreshHistory()})}>Check status</Button>}</td></tr>)}</tbody></table></div>}
  </section>
  <Dialog open={editDialog} onOpenChange={setEditDialog}><DialogContent><DialogHeader><DialogTitle>Be careful when changing the refund amount</DialogTitle><DialogDescription>You are changing how much money will be returned to the customer. Check the order, currency and remaining refundable balance. You cannot exceed the remaining order value.</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={()=>setEditDialog(false)}>Cancel</Button><Button onClick={()=>{setEditing(true);setReview(null);setEditDialog(false)}}>I understand — edit amount</Button></DialogFooter></DialogContent></Dialog>
  <Dialog open={!!review} onOpenChange={open=>{if(!open&&!busy)setReview(null)}}><DialogContent><DialogHeader><DialogTitle>Confirm refund transfer</DialogTitle><DialogDescription>Check the recipient and amount carefully. Submitting sends a real payment from your Airwallex account.</DialogDescription></DialogHeader>{busy&&<p role="status" className="text-sm text-muted-foreground">{busy} · {busySeconds}s. Keep this window open while we confirm the result.</p>}{error&&<p role="alert" className="whitespace-pre-line text-sm text-red-700">{error}</p>}{review&&<div className="space-y-3"><p className="text-2xl font-semibold">{format(review.amount,review.currency)}</p><p className="text-sm">Pay from: <strong>{review.source_currency}</strong> · {review.conversion?'Estimated debit before fees':'Debit before fees'}: {format(review.estimated_source_amount,review.source_currency)}</p>{review.conversion&&<p className="text-xs text-muted-foreground">Airwallex converts the selected currency at submission. The estimate can change with the exchange rate; fees are additional.</p>}<p>{review.order_name} → {review.recipient}</p><p className="text-sm">{review.method} · {review.destination}</p><p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{review.fees}</p><p className="text-xs text-muted-foreground">This review expires after 10 minutes. The refund limit is checked again when you submit.</p></div>}<DialogFooter><Button variant="outline" disabled={!!busy} onClick={()=>setReview(null)}>Go back</Button><Button disabled={!!busy||!review||!daily||daily.remaining===0} onClick={()=>void run('Submitting refund',async()=>{if(!review)return;const result=await post<Payout>('/submit',{review_token:review.review_token,confirmed:true});setReview(null);setSnapshot(null);setSchema(null);setNotice(`${result.order_name}: ${statusLabel(result.status)}. ${result.last_error||'The transfer status will be tracked below.'}`);await refreshHistory();await refreshLimits();await refreshFunding()})}>{busy?'Submitting…':'Send refund'}</Button></DialogFooter></DialogContent></Dialog>
 </div>
}
