import { Link } from 'react-router-dom'
import { http } from '../api/client'
import { useApi } from '../components/useApi'
import { Badge, DateTime, Money, PageHead, Alert } from '../components/ui'
import { useAuth } from '../api/AuthContext'

function Kpi({ label, value, hint }) {
  return (
    <div className="kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  )
}

export default function Dashboard() {
  const { user, isManager } = useAuth()
  const stats = useApi(() => http.get('/requests/stats/summary'), [])
  const queue = useApi(() => (isManager ? http.get('/rpa/queue/stats') : Promise.resolve(null)), [isManager])
  const inbox = useApi(() => (isManager ? http.get('/requests/inbox') : Promise.resolve([])), [isManager])

  const s = stats.data || {}
  const byStatus = s.by_status || {}
  const q = queue.data || {}
  const trend = s.trend_30d || []
  const maxTrend = Math.max(1, ...trend.map((t) => t.count))

  return (
    <>
      <PageHead
        title={`Good day, ${user?.full_name?.split(' ')[0] || 'there'}`}
        subtitle="Live view of requests, approvals and automation health."
      >
        <Link to="/requests/new" className="btn">+ New request</Link>
        <Link to="/requests" className="btn ghost">All requests</Link>
      </PageHead>

      <Alert kind="error">{stats.error}</Alert>

      <div className="grid cols-4">
        <Kpi label="Open requests" value={s.open ?? '—'} hint={`${s.total ?? 0} total in scope`} />
        <Kpi label="Awaiting my approval" value={s.pending_for_me ?? 0} hint="Assigned approval steps" />
        <Kpi label="SLA breached" value={s.sla_breached ?? 0} hint="Past due date, still open" />
        <Kpi label="Open value" value={<Money amount={s.open_value} />} hint="Submitted + pending" />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="card-head">
            <h3>Requests by status</h3>
            <div className="spacer" />
            <Link to="/requests">Open list →</Link>
          </div>
          <div className="table-wrap">
            <table className="data">
              <tbody>
                {Object.entries(byStatus).map(([status, count]) => (
                  <tr key={status}>
                    <td><Badge value={status} /></td>
                    <td className="num">{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>New requests — last 30 days</h3>
          </div>
          {trend.length === 0 ? (
            <p className="muted">No activity recorded yet.</p>
          ) : (
            <div className="bar-chart">
              {trend.slice(-20).map((point) => (
                <div
                  key={point.date}
                  className="bar"
                  style={{ height: `${Math.max(4, (point.count / maxTrend) * 100)}%` }}
                  title={`${point.date}: ${point.count}`}
                >
                  <span>{point.date.slice(8)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <div className="card-head">
            <h3>By department</h3>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Department</th><th className="right">Requests</th><th className="right">Value</th></tr>
              </thead>
              <tbody>
                {(s.by_department || []).map((row) => (
                  <tr key={row.department}>
                    <td>{row.department}</td>
                    <td className="num">{row.count}</td>
                    <td className="num"><Money amount={row.amount} /></td>
                  </tr>
                ))}
                {!(s.by_department || []).length && (
                  <tr><td colSpan="3" className="muted">No data</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Automation health</h3>
            <div className="spacer" />
            <Link to="/rpa/jobs">Job queue →</Link>
          </div>
          {!isManager ? (
            <p className="muted">Queue metrics are visible to managers and admins.</p>
          ) : (
            <>
              <div className="grid cols-3">
                <Kpi label="Queued" value={q.queued ?? 0} />
                <Kpi label="Running" value={q.running ?? 0} hint={`${q.retry_scheduled ?? 0} awaiting retry`} />
                <Kpi label="Dead letter" value={q.dead_letter ?? 0} hint="Needs a human" />
              </div>
              <p className="muted" style={{ marginTop: 12 }}>
                Success rate <strong>{q.success_rate != null ? `${(q.success_rate * 100).toFixed(1)}%` : '—'}</strong>
                {' · '}avg run <strong>{q.avg_duration_seconds != null ? `${q.avg_duration_seconds}s` : '—'}</strong>
                {' · '}oldest queued <strong>{q.oldest_queued_seconds != null ? `${q.oldest_queued_seconds}s` : '—'}</strong>
              </p>
            </>
          )}
        </div>
      </div>

      {isManager && (
        <div className="card">
          <div className="card-head">
            <h3>Waiting on you</h3>
            <div className="spacer" />
            <Link to="/inbox">Open inbox →</Link>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Reference</th><th>Title</th><th>Requester</th>
                  <th className="right">Amount</th><th>Priority</th><th>Submitted</th>
                </tr>
              </thead>
              <tbody>
                {(inbox.data || []).slice(0, 6).map((row) => (
                  <tr key={row.id}>
                    <td className="ref"><Link to={`/requests/${row.id}`}>{row.reference_no}</Link></td>
                    <td>{row.title}</td>
                    <td>{row.requester_name}</td>
                    <td className="num"><Money amount={row.amount} currency={row.currency} /></td>
                    <td><Badge value={row.priority} /></td>
                    <td><DateTime value={row.submitted_at} /></td>
                  </tr>
                ))}
                {!(inbox.data || []).length && (
                  <tr><td colSpan="6" className="muted" style={{ textAlign: 'center', padding: 18 }}>Inbox clear 🎉</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
