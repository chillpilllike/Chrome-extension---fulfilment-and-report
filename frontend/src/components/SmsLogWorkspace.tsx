import { SmsLog } from './SmsSettings'
import './epost-workspace.css'

type Props = {
  storeId: string
  api: <T>(path: string, options?: RequestInit) => Promise<T>
}

export function SmsLogWorkspace({ storeId, api }: Props) {
  return <div className="epost-workspace">
    <header className="epost-heading"><div>
      <span className="epost-eyebrow">Customer communications</span>
      <h2>SMS log</h2>
      <p>Review SMS messages, provider delivery reports and individual send attempts.</p>
    </div></header>
    <section className="epost-notice">
      <strong>SMS sending safeguards</strong>
      <p>Accepted or sent does not mean delivered. Failed messages require review before retrying. Resending a delivered message requires confirmation and may incur another charge. Pending and uncertain sends remain blocked to prevent duplicates.</p>
    </section>
    <SmsLog api={api} storeId={storeId} />
  </div>
}
