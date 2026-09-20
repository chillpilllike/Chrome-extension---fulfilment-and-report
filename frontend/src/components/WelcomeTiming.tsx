import { useEffect, useState } from 'react'

type Timing = { target_minutes:number; pending_overdue:number; sent_late:number; last_check_at:string|null; import_interval_minutes?:string; import_backlog_minutes?:number; orders:{store_id:number;order_id:number;order_name:string;minutes:number;status:string}[] }
type API = <T>(path:string,init?:RequestInit)=>Promise<T>

export function WelcomeTiming({request,storeId}:{request:API;storeId:string}) {
  const [data,setData]=useState<Timing|null>(null)
  const [error,setError]=useState('')
  useEffect(()=>{
    let active=true
    async function load(){try{const result=await request<Timing>(`/api/after-order/welcome-timing${storeId?`?store_id=${encodeURIComponent(storeId)}`:''}`);if(active){setData(result);setError('')}}catch{if(active)setError('New-order email timing could not be checked.')}}
    void load();const timer=window.setInterval(()=>void load(),60000)
    return()=>{active=false;window.clearInterval(timer)}
  },[request,storeId])
  if(error)return <p role="alert" className="m-3 border border-amber-300 bg-amber-50 p-3 text-sm">{error}</p>
  if(!data)return null
  const stale=!data.last_check_at||Date.now()-new Date(data.last_check_at).getTime()>120000
  const slowImport=Number(data.import_interval_minutes||0)>5||Number(data.import_interval_minutes||0)<=0
  const stalledImport=(data.import_backlog_minutes||0)>10
  const warning=data.pending_overdue>0||stale||slowImport||stalledImport
  return <section aria-label="New-order email timing" className={`m-3 border p-3 text-sm ${warning?'border-amber-300 bg-amber-50':'border-slate-200 bg-white'}`}>
    <div className="flex flex-wrap items-center justify-between gap-2"><strong>New-order emails · 15-minute target</strong><a href="/email-log" className="underline">Open email log</a></div>
    <p className="mt-1">{data.pending_overdue} overdue without a recorded send · {data.sent_late} sent late in the last 24 hours (includes earlier delays).</p>
    {stale&&<p className="mt-1 text-amber-900">The welcome worker has not completed a check in the last two minutes.</p>}
    {slowImport&&<p className="mt-1 text-amber-900">Order import interval is {data.import_interval_minutes||'not configured'} minutes; five minutes or less is needed for this target.</p>}
    {stalledImport&&<p className="mt-1 text-amber-900">An order import has been queued or running for {data.import_backlog_minutes} minutes. New orders may not yet be visible to the email worker.</p>}
    {data.orders.length>0&&<details className="mt-2"><summary className="cursor-pointer">View overdue orders</summary><ul className="mt-2 space-y-1">{data.orders.map(o=><li key={`${o.store_id}:${o.order_id}`}>{o.order_name} · {o.minutes} minutes · {o.status.replaceAll('_',' ')}</li>)}</ul></details>}
    <p className="mt-2 text-xs text-muted-foreground">Confirmed orders imported in the last 24-hour order-date window. Delivery depends on Odoo and the email provider; this panel does not resend messages.</p>
  </section>
}
