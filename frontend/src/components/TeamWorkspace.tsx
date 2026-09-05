import type { ReactNode } from "react"
import "./team-workspace.css"

type Queue = { value: string; label: string; hint: string }

export function TeamWorkspaceHeader({ title, description, current, children }: {
  title: string; description: string; current: string; children?: ReactNode
}) {
  return <header className="team-workspace-header">
    <div className="team-workspace-heading"><div><h2>{title}</h2><p>{description}</p></div>{children}</div>
    <nav className="team-workspace-links" aria-label="Related operations pages">
      {[["orders", "1 · Orders"], ["package-pickups", "2 · Pickup check"], ["package-tracker", "3 · Package tracker"], ["after-order-care", "4 · After-order care"], ["epost", "ePost exceptions"], ["email-log", "Email log"]].map(([path, label]) =>
        <a key={path} href={`/${path}`} aria-current={current === path ? "page" : undefined}>{label}</a>)}
    </nav>
  </header>
}

export function TeamQueues({ label = "Choose a work queue", value, onChange, queues }: {
  label?: string; value: string; onChange: (value: string) => void; queues: Queue[]
}) {
  return <section className="team-queue-panel" aria-label={label}>
    <div className="team-section-label">{label}</div>
    <div className="team-queues">{queues.map(queue => <button type="button" key={queue.value}
      className={value === queue.value ? "is-active" : ""} aria-pressed={value === queue.value}
      onClick={() => onChange(queue.value)}><strong>{queue.label}</strong><span>{queue.hint}</span></button>)}</div>
  </section>
}

export function TeamTools({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <details className="team-tools"><summary>{title}</summary><div className="team-tools-body">
    {description && <p className="team-help">{description}</p>}{children}
  </div></details>
}
