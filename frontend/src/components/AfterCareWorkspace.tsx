import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import "./epost-workspace.css"
import "./after-care-workspace.css"

const queues = [
  ["open", "All open cases", "Active cases", "Review customer requests, sourcing issues and shipment follow-up."],
  ["needs_confirmation", "Confirm decisions", "Customer requests", "Check the latest customer choice before confirming. Approval is not proof of a completed refund or replacement."],
  ["needs_attention", "Needs attention", "Sourcing & exceptions", "Check fulfilment options and resolve blockers before contacting the customer."],
  ["approved_pending_execution", "Execution pending", "Approved follow-up", "Review execution progress and errors for decisions already confirmed by your team."],
  ["tracking", "Tracking follow-up", "Shipment monitoring", "Review carrier evidence. Missing first scans alone do not mean a package is lost."],
  ["approved", "Approved", "Decision history", "Review confirmed decisions and their recorded execution status."],
  ["resolved", "Resolved", "Case history", "Review completed cases and their customer activity."],
  ["all", "All cases", "Full history", "Search across all eligible cases for the selected store."],
] as const

export function AfterCareWorkspace({ status, summary, loading, total, query, onQuery, onSearch, onQueue, onRefresh, testMode, automationEnabled, cutoffDate, tools, children }: {
  status: string; summary: Record<string, number>; loading: boolean; total: number;
  query: string; onQuery: (value: string) => void; onSearch: () => void;
  onQueue: (value: string) => void; onRefresh: () => void; testMode: boolean;
  automationEnabled: boolean; cutoffDate: string; tools: ReactNode; children: ReactNode;
}) {
  const current = queues.find(queue => queue[0] === status) || queues[0]
  const count = (key: string) => key === "all" ? Object.values(summary).reduce((sum, n) => sum + n, 0)
    : key === "open" ? Object.entries(summary).reduce((sum, [state, n]) => sum + (["approved", "resolved"].includes(state) ? 0 : n), 0)
    : summary[key] || 0
  return <div className="epost-workspace care-workspace">
    <header className="epost-heading">
      <div><span className="epost-eyebrow">Customer operations</span><h2>After-order care</h2><p>Customer requests, sourcing reviews and delivery follow-up.</p></div>
      <div className="epost-heading-actions"><span className={`care-mode ${testMode ? "" : "is-live"}`} role="status">{testMode ? "Test mode · no live actions" : "Live mode · customer delivery permitted"}</span><Button variant="outline" disabled={loading} onClick={onRefresh}>Reload cases</Button></div>
    </header>
    <div className="care-admin-tools">{tools}</div>
    <div className="epost-layout">
      <aside className="epost-queues" aria-label="After-order work queues">
        <div className="epost-queue-title">Work queues <span>Store totals</span></div>
        {queues.map(([key, label], index) => <div key={key}>
          {index === 4 && <div className="epost-queue-divider">Monitor & history</div>}
          <button type="button" className={`epost-queue ${status === key ? "is-active" : ""}`} aria-pressed={status === key} disabled={loading} onClick={() => onQueue(key)}><span>{label}</span><strong>{loading ? "—" : count(key).toLocaleString()}</strong></button>
        </div>)}
        <div className="epost-related"><h3>Related work</h3><a href="/epost">ePost Global tracking ↗</a><a href="/email-log">Email log & retries ↗</a><a href="/package-tracker">Track all packages ↗</a></div>
        <div className="care-queue-note"><small>Orders from {cutoffDate}. Queue totals cover the selected store, regardless of search.</small><small>Background checks: {automationEnabled ? "enabled" : "paused"}.</small></div>
      </aside>
      <section className="epost-main" aria-label="After-order case results" aria-busy={loading}>
        <section className="epost-queue-context"><div><span className="epost-eyebrow">{current[2]}</span><h3>{current[1]}</h3><p>{current[3]}</p></div><span className="epost-result-count">{loading ? "Loading…" : `${total.toLocaleString()} matching`}</span></section>
        <form className="epost-filters" onSubmit={event => { event.preventDefault(); onSearch() }}>
          <div className="epost-search"><label htmlFor="care-search">Find an order or case</label><Input id="care-search" value={query} onChange={event => onQuery(event.target.value)} placeholder="Order, tracking number or case title…" /></div>
          <Button type="submit" variant="outline" disabled={loading}>Search</Button>
        </form>
        {children}
      </section>
    </div>
  </div>
}
