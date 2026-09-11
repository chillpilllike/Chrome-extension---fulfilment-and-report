import { IconAlertTriangle, IconCheck, IconClock, IconMail, IconPackage, IconUserCheck } from '@tabler/icons-react'
import './care-activity-timeline.css'

export type CareEvent = { id: number; event_type: string; created_at: string; actor_label?: string; actor_type?: string; decision?: string; details?: Record<string, unknown> }
const words = (value: string) => value.replaceAll('_', ' ')
const hiddenFields = new Set(['signature', 'request_fingerprint', 'allowed_actions'])

function appearance(type: string) {
  if (/fail|error|block|bounce|complaint/.test(type)) return { Icon: IconAlertTriangle, tone: 'red' }
  if (/awaiting|needs_approval|review|pending/.test(type)) return { Icon: IconClock, tone: 'yellow' }
  if (/customer|selection|decision/.test(type)) return { Icon: IconUserCheck, tone: 'purple' }
  if (/email|notification/.test(type)) return { Icon: IconMail, tone: 'blue' }
  if (/approved|confirmed|completed|delivered/.test(type)) return { Icon: IconCheck, tone: 'green' }
  return { Icon: IconPackage, tone: 'secondary' }
}

export function CareActivityTimeline({ events, filter, loading }: { events: CareEvent[]; filter: string; loading: boolean }) {
  const visible = events.filter(event => JSON.stringify(event).toLowerCase().includes(filter.toLowerCase()))
  if (!visible.length) return loading ? null : <p className="care-timeline-empty" role="status">{events.length ? 'No activity matches this filter.' : 'No recorded activity yet.'}</p>
  return <ul className="timeline care-activity-timeline" aria-label="Order activity timeline" aria-busy={loading}>
    {visible.map(event => {
      const { Icon, tone } = appearance(event.event_type)
      const timestamp = new Date(event.created_at)
      const validDate = Boolean(event.created_at) && !Number.isNaN(timestamp.getTime())
      const details = Object.entries(event.details || {}).filter(([key, value]) => !hiddenFields.has(key) && value != null)
      return <li key={event.id} className="timeline-event">
        <div className={`timeline-event-icon bg-${tone}-lt care-event-${tone}`} aria-hidden="true"><Icon size={18} stroke={1.7}/></div>
        <div className="card timeline-event-card"><div className="card-body">
          <div className="care-event-heading"><h4>{words(event.event_type)}</h4>
            <time dateTime={validDate ? event.created_at : undefined} title={validDate ? timestamp.toLocaleString() : undefined}>{validDate ? timestamp.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : 'Date unavailable'}{validDate && <span>{timestamp.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' })}</span>}</time>
          </div>
          <div className="care-event-meta"><span>{event.actor_label || words(event.actor_type || 'System')}</span>{event.decision && <span className="care-event-decision">{words(event.decision)}</span>}</div>
          {details.length > 0 && <details className="care-event-details"><summary>Event details <span>{details.length}</span></summary><dl>{details.map(([key, value]) => <div key={key}><dt>{words(key)}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></details>}
        </div></div>
      </li>
    })}
  </ul>
}
