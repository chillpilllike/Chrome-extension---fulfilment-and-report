import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

type Option = {key:string;label:string;amount:string;currency:string}
type Completion = {id:number;amount:string;currency:string;reference:string;completed_at:string;recorded_by:string}
type State = {enabled:boolean;test_mode:boolean;decision_version:number;options:Option[];completed:Completion[]}
type API = <T>(path:string,options?:RequestInit)=>Promise<T>

export function ManualRefundPanel({caseId,request,onComplete}:{caseId:number;request:API;onComplete:()=>void}) {
  const [data,setData]=useState<State|null>(null)
  const [scope,setScope]=useState('')
  const [amount,setAmount]=useState('')
  const [currency,setCurrency]=useState('')
  const [reference,setReference]=useState('')
  const [staff,setStaff]=useState('')
  const [notes,setNotes]=useState('')
  const [date,setDate]=useState(()=>new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16))
  const [confirmed,setConfirmed]=useState(false)
  const [checked,setChecked]=useState(false)
  const [busy,setBusy]=useState(false)
  const [message,setMessage]=useState('')
  useEffect(()=>{let active=true;request<State>(`/api/after-order/cases/${caseId}/manual-refund`).then(x=>{if(active)setData(x)}).catch(e=>{if(active)setMessage(String(e))});return()=>{active=false}},[caseId,request])
  function choose(key:string) {const o=data?.options.find(x=>x.key===key);setScope(key);setAmount(o?.amount||'');setCurrency(o?.currency||'');setConfirmed(false);setChecked(false)}
  if(!data)return message?<p role="alert" className="text-sm text-red-700">Manual refund panel: {message}</p>:null
  if(!data.options.length&&!data.completed.length)return null
  return <section className="space-y-3 rounded-lg border p-4">
    <h4 className="font-semibold">Manual refund completion</h4>
    <p className="text-sm text-muted-foreground">Refund the customer in your payment provider first. This form records that completed refund and sends a confirmation email. It does not transfer money or cancel the Odoo order.</p>
    {data.completed.map(x=><p key={x.id} className="rounded bg-green-50 p-3 text-sm text-green-900">Recorded: {x.currency} {x.amount} · {x.reference} · {x.recorded_by} · {new Date(x.completed_at).toLocaleString()}</p>)}
    {data.options.length>0&&<form className="grid gap-3" onSubmit={async e=>{
      e.preventDefault();if(busy||!confirmed||!checked)return
      if(!window.confirm(`Record ${currency} ${amount} as already refunded and send the customer a confirmation email now? This does not issue a refund.`))return
      setBusy(true);setMessage('')
      try {const result=await request<{message:string}>(`/api/after-order/cases/${caseId}/manual-refund`,{method:'POST',body:JSON.stringify({scope,decision_version:data.decision_version,amount,currency:currency.toUpperCase(),reference,completed_at:new Date(date).toISOString(),recorded_by:staff,notes,refunded_outside_app:confirmed,other_refunds_checked:checked})});setMessage(result.message);setData(await request<State>(`/api/after-order/cases/${caseId}/manual-refund`));onComplete()}catch(err){setMessage(String(err))}finally{setBusy(false)}
    }}>
      <label className="grid gap-1 text-sm">Refund request<select required value={scope} onChange={e=>choose(e.target.value)} className="rounded border p-2"><option value="">Select the refunded request</option>{data.options.map(o=><option key={o.key} value={o.key}>{o.label}</option>)}</select></label>
      <div className="grid grid-cols-2 gap-3"><label className="grid gap-1 text-sm">Amount refunded<Input required type="number" min="0.01" step="0.01" value={amount} onChange={e=>setAmount(e.target.value)}/></label><label className="grid gap-1 text-sm">Currency (e.g. USD)<Input required pattern="[A-Za-z]{3}" maxLength={3} value={currency} onChange={e=>setCurrency(e.target.value.toUpperCase())}/></label></div>
      <label className="grid gap-1 text-sm">Refund/payment reference<Input required minLength={3} maxLength={150} value={reference} onChange={e=>setReference(e.target.value)}/></label>
      <label className="grid gap-1 text-sm">Refund completed at (your local time)<Input required type="datetime-local" value={date} onChange={e=>setDate(e.target.value)}/></label>
      <label className="grid gap-1 text-sm">Recorded by<Input required minLength={2} maxLength={100} value={staff} onChange={e=>setStaff(e.target.value)}/></label>
      <label className="grid gap-1 text-sm">Internal notes<Input maxLength={2000} value={notes} onChange={e=>setNotes(e.target.value)}/></label>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>I confirm the money has already been refunded outside this app.</label>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={checked} onChange={e=>setChecked(e.target.checked)}/>I checked previous refunds in all payment providers and Odoo to avoid a duplicate refund.</label>
      <Button type="submit" disabled={busy||!data.enabled||data.test_mode||!scope||!confirmed||!checked}>{busy?'Recording…':'Mark manually refunded & send email'}</Button>
      {data.test_mode&&<p className="text-sm text-amber-800">Disabled in test mode. No refund or customer email will be recorded here.</p>}
      {!data.enabled&&<p className="text-sm text-amber-800">Manual-refund mode is not enabled.</p>}
    </form>}
    {message&&<p role="status" className="text-sm whitespace-pre-wrap">{message}</p>}
  </section>
}
