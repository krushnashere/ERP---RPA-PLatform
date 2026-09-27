import { useCallback, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { errorMessage, http } from '../api/client'
import { useApi, usePolling } from '../components/useApi'
import { Alert, Badge, DataTable, DateTime, PageHead } from '../components/ui'
import { useAuth } from '../api/AuthContext'

const STATUSES = ['queued', 'running', 'retry_scheduled', 'succeeded', 'failed', 'cancelled', 'dead_letter']

export default function RpaJobs() {
  const [params, setParams] = useSearchParams()
  const { can } = useAuth()
  const [selected, setSelected] = useState(Number(params.get('job')) || null)
  const [logs, setLogs] = useState(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [autoRefresh, setAutoRefresh] = useState(true)

  const state = {
    status: params.getAll('status'),
    search: params.get('search') || '',
    page: Number(params.get('page') || 1),
    sort_by: params.get('sort_by') || 'created_at',
    sort_dir: params.get('sort_dir') || 'desc',
  }
  const [searchDraft, setSearchDraft] = useState(state.search)

  const query = useCallback(() => {
    const q = new URLSearchParams()
    state.status.forEach((s) => q.append('status', s))
    if (state.search) q.set('search', state.search)
    if (params.get('request_id')) q.set('request_id', params.get('request_id'))
    q.set('page', String(state.page))
    q.set('size', '20')
    q.set('sort_by', state.sort_by)
    q.set('sort_dir', state.sort_dir)
    return q
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(state), params.get('request_id')])

  const jobs = useApi(() => http.get(`/rpa/jobs?${query().toString()}`), [query])
  const stats = useApi(() => http.get('/rpa/queue/stats'), [])

  const refreshAll = useCallback(() => {
    jobs.refetch()
    stats.refetch()
    if (selected) loadLogs(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.refetch, stats.refetch, selected])

  usePolling(refreshAll, autoRefresh ? 4000 : 0)

  const update = (patch) => {
    const next = new URLSearchParams(query())
    Object.entries(patch).forEach(([key, value]) => {
      if (key === 'status') {
        next.delete('status')
        value.forEach((v) => next.append('status', v))
      } else if (!value || value === false) next.delete(key)
      else next.set(key, String(value))
    })
    if (!('page' in patch)) next.set('page', '1')
    setParams(next, { replace: true })
  }

  const loadLogs = async (jobId) => {
    setLogsLoading(true)
    try {
      setLogs(await http.get(`/rpa/jobs/${jobId}/logs?limit=300`))
    } catch (err) {
      setError(errorMessage(err, 'Could not load logs'))
    } finally {
      setLogsLoading(false)
    }
  }

  const openJob = (job) => {
    setSelected(job.id)
    loadLogs(job.id)
  }

  const act = async (jobId, verb, extra = '') => {
    setError(null)
    setNotice(null)
    try {
      await http.post(`/rpa/jobs/${jobId}/${verb}${extra}`, {})
      setNotice(`Job ${jobId}: ${verb} accepted.`)
      refreshAll()
    } catch (err) {
      setError(errorMessage(err, `Could not ${verb} the job`))
    }
  }

  const s = stats.data || {}

  const columns = [
    { key: 'job_ref', label: 'Job', width: 170, render: (row) => <span className="ref mono">{row.job_ref}</span> },
    { key: 'process_name', label: 'Process', render: (row) => <div><div style={{ fontWeight: 600 }}>{row.process_name}</div><div className="muted mono" style={{ fontSize: 11 }}>{row.process_code}</div></div> },
    { key: 'request_reference', label: 'Request', width: 150, render: (row) => row.request_reference || <span className="muted">standalone</span> },
    { key: 'status', label: 'Status', width: 140, render: (row) => <Badge value={row.status} /> },
    { key: 'priority', label: 'Prio', width: 60, numeric: true },
    { key: 'attempts', label: 'Attempts', width: 90, numeric: true, render: (row) => `${row.attempts}/${row.max_attempts}` },
    { key: 'worker_id', label: 'Worker', width: 150, render: (row) => <span className="muted mono">{row.worker_id || '—'}</span> },
    { key: 'next_run_at', label: 'Next run', width: 150, render: (row) => <DateTime value={row.next_run_at} /> },
    {
      key: 'actions', label: 'Actions', width: 210,
      render: (row) => (
        <div className="btn-row">
          <button className="ghost sm" onClick={() => openJob(row)}>Logs</button>
          {['queued', 'running', 'retry_scheduled'].includes(row.status) && (
            <button className="danger sm" onClick={() => act(row.id, 'cancel', '?reason=cancelled+from+UI')}>Cancel</button>
          )}
          {['failed', 'dead_letter', 'cancelled'].includes(row.status) && can('manage-rpa') && (
            <button className="sm" onClick={() => act(row.id, 'retry')}>Retry</button>
          )}
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHead title="RPA job queue" subtitle="Queue-based execution with retries, cancellation and a dead-letter bucket.">
        <label className="row" style={{ fontSize: 12 }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          Auto-refresh (4s)
        </label>
        <button className="ghost" onClick={refreshAll}>Refresh now</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <div className="grid cols-4">
        <div className="kpi"><div className="label">Queued</div><div className="value">{s.queued ?? 0}</div><div className="hint">{s.retry_scheduled ?? 0} awaiting retry</div></div>
        <div className="kpi"><div className="label">Running</div><div className="value">{s.running ?? 0}</div><div className="hint">oldest queued {s.oldest_queued_seconds ?? '—'}s</div></div>
        <div className="kpi"><div className="label">Success rate</div><div className="value">{s.success_rate != null ? `${(s.success_rate * 100).toFixed(1)}%` : '—'}</div><div className="hint">avg {s.avg_duration_seconds ?? '—'}s per run</div></div>
        <div className="kpi"><div className="label">Dead letter</div><div className="value">{s.dead_letter ?? 0}</div><div className="hint">retries exhausted → manual fix</div></div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div className="filters">
          <label className="field" style={{ margin: 0, gridColumn: 'span 2' }}>
            <span>Search</span>
            <input type="text" placeholder="Job ref or process…" value={searchDraft}
              onChange={(e) => setSearchDraft(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && update({ search: searchDraft })} />
          </label>
          <div className="btn-row">
            <button className="sm" onClick={() => update({ search: searchDraft })}>Apply</button>
            <button className="sm ghost" onClick={() => { setSearchDraft(''); update({ search: '', status: [] }) }}>Clear</button>
            {can('manage-rpa') && <button className="sm ghost" onClick={() => sweep()}>Sweep stale leases</button>}
          </div>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <span className="muted" style={{ fontSize: 12 }}>Status:</span>
          {STATUSES.map((status) => (
            <button key={status} type="button" className={`sm ${state.status.includes(status) ? '' : 'ghost'}`}
              onClick={() => update({ status: state.status.includes(status) ? state.status.filter((x) => x !== status) : [...state.status, status] })}>
              {status.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={jobs.data?.items || []}
        loading={jobs.loading}
        empty="No jobs match these filters."
        page={jobs.data?.page || 1}
        total={jobs.data?.total || 0}
        pages={jobs.data?.pages || 0}
        onPage={(page) => update({ page })}
        sortBy={state.sort_by}
        sortDir={state.sort_dir}
        onSort={(sort_by, sort_dir) => update({ sort_by, sort_dir })}
      />

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head">
            <h3>Execution log — job #{selected}</h3>
            <div className="spacer" />
            <button className="ghost sm" onClick={() => loadLogs(selected)}>Reload</button>
            <button className="ghost sm" onClick={() => { setSelected(null); setLogs(null) }}>Close</button>
          </div>
          {logsLoading ? <div className="muted">Loading logs…</div> : (
            <div className="log-viewer">
              {(logs || []).length === 0 && <div className="line muted">No log lines yet.</div>}
              {(logs || []).map((line) => (
                <div key={line.id} className={`line ${line.level}`}>
                  <span className="muted">{new Date(line.created_at).toLocaleTimeString('en-IN')}</span>{' '}
                  <span className={line.level}>[{line.level}]</span>{' '}
                  <span className="step">{line.step || '—'}</span> · attempt {line.attempt} · {line.message}
                  {line.worker_id && <span className="muted"> ({line.worker_id})</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )

  async function sweep() {
    setError(null)
    try {
      const result = await http.post('/rpa/queue/sweep', {})
      setNotice(`Swept ${result.requeued} stale lease(s).`)
      refreshAll()
    } catch (err) {
      setError(errorMessage(err, 'Sweep failed'))
    }
  }
}
