import { useState } from 'react'
import { http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, DataTable, DateTime, PageHead } from '../components/ui'

export default function AuditLog() {
  const [filters, setFilters] = useState({ entity_type: '', action: '', search: '', actor_id: '', page: 1 })
  const [expanded, setExpanded] = useState(null)

  const query = `?page=${filters.page}&size=25` +
    (filters.entity_type ? `&entity_type=${filters.entity_type}` : '') +
    (filters.action ? `&action=${encodeURIComponent(filters.action)}` : '') +
    (filters.search ? `&search=${encodeURIComponent(filters.search)}` : '') +
    (filters.actor_id ? `&actor_id=${filters.actor_id}` : '')

  const logs = useApi(() => http.get(`/audit${query}`), [query])
  const actions = useApi(() => http.get('/audit/actions'), [])
  const actors = useApi(() => http.get('/audit/actors'), [])

  const columns = [
    { key: 'created_at', label: 'When', width: 170, render: (row) => <DateTime value={row.created_at} /> },
    { key: 'action', label: 'Action', width: 200, render: (row) => <span className="mono">{row.action}</span> },
    { key: 'entity_type', label: 'Entity', width: 150, render: (row) => <span>{row.entity_type}{row.entity_id ? ` #${row.entity_id}` : ''}</span> },
    {
      key: 'actor', label: 'Actor', width: 160,
      render: (row) => (
        <div>
          <div>{(actors.data || []).find((a) => a.id === row.actor_id)?.full_name || <span className="muted">system</span>}</div>
          <div className="muted" style={{ fontSize: 11 }}>{row.actor_role}</div>
        </div>
      ),
    },
    { key: 'ip_address', label: 'IP', width: 130, render: (row) => <span className="muted mono">{row.ip_address || '—'}</span> },
    {
      key: 'change', label: 'Change',
      render: (row) => (
        <div>
          <div className="muted" style={{ fontSize: 12 }}>{JSON.stringify(row.new_values || row.old_values || {}).slice(0, 140)}</div>
          {(row.old_values || row.new_values) && (
            <button type="button" className="ghost sm" style={{ marginTop: 4 }}
              onClick={() => setExpanded(expanded === row.id ? null : row.id)}>
              {expanded === row.id ? 'Hide diff' : 'Show diff'}
            </button>
          )}
          {expanded === row.id && (
            <div className="grid cols-2" style={{ marginTop: 8 }}>
              <div><div className="muted" style={{ fontSize: 11 }}>Before</div><pre className="json">{JSON.stringify(row.old_values || {}, null, 2)}</pre></div>
              <div><div className="muted" style={{ fontSize: 11 }}>After</div><pre className="json">{JSON.stringify(row.new_values || {}, null, 2)}</pre></div>
            </div>
          )}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHead title="Audit trail" subtitle="Append-only: who did what, when, from which IP, and what changed.">
        <button className="ghost" onClick={logs.refetch}>Refresh</button>
      </PageHead>

      <Alert kind="error">{logs.error}</Alert>

      <div className="card">
        <div className="filters">
          <label className="field" style={{ margin: 0, gridColumn: 'span 2' }}>
            <span>Search action / notes</span>
            <input type="text" value={filters.search} placeholder="e.g. rejected, cancelled, job…"
              onChange={(e) => setFilters({ ...filters, search: e.target.value, page: 1 })} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Action</span>
            <select value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value, page: 1 })}>
              <option value="">All actions</option>
              {(actions.data || []).map((action) => <option key={action} value={action}>{action}</option>)}
            </select>
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Entity type</span>
            <select value={filters.entity_type} onChange={(e) => setFilters({ ...filters, entity_type: e.target.value, page: 1 })}>
              <option value="">All entities</option>
              {['approval_request', 'rpa_job', 'rpa_process', 'user', 'department', 'request_type'].map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Actor</span>
            <select value={filters.actor_id} onChange={(e) => setFilters({ ...filters, actor_id: e.target.value, page: 1 })}>
              <option value="">Anyone</option>
              {(actors.data || []).map((a) => <option key={a.id} value={a.id}>{a.full_name}</option>)}
            </select>
          </label>
        </div>
      </div>

      <DataTable columns={columns} rows={logs.data?.items || []} loading={logs.loading}
        empty="No audit entries match." page={logs.data?.page || 1} total={logs.data?.total || 0}
        pages={logs.data?.pages || 0} onPage={(page) => setFilters({ ...filters, page })} />
    </>
  )
}
