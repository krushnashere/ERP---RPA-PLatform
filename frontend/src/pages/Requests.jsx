import { useCallback, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, Badge, DataTable, DateTime, Money, PageHead } from '../components/ui'

const STATUSES = ['draft', 'submitted', 'pending_approval', 'approved', 'rejected', 'cancelled', 'completed']
const PRIORITIES = ['low', 'normal', 'high', 'critical']

/**
 * The list view: multi-value status filters, free-text search, amount range,
 * date range, SLA-only toggle, column sorting and server-side pagination.
 * Every filter is reflected in the URL so a view can be shared/bookmarked.
 */
export default function Requests() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()

  const state = {
    search: params.get('search') || '',
    status: params.getAll('status'),
    priority: params.getAll('priority'),
    mine: params.get('mine') || 'all',
    min_amount: params.get('min_amount') || '',
    max_amount: params.get('max_amount') || '',
    sla: params.get('sla') === '1',
    page: Number(params.get('page') || 1),
    size: Number(params.get('size') || 20),
    sort_by: params.get('sort_by') || 'created_at',
    sort_dir: params.get('sort_dir') || 'desc',
  }
  const [draftFilters, setDraftFilters] = useState({ search: state.search, min_amount: state.min_amount, max_amount: state.max_amount })

  const buildQuery = useCallback(() => {
    const q = new URLSearchParams()
    state.status.forEach((s) => q.append('status', s))
    state.priority.forEach((p) => q.append('priority', p))
    if (state.mine !== 'all') q.set('mine', state.mine)
    if (state.search) q.set('search', state.search)
    if (state.min_amount) q.set('min_amount', state.min_amount)
    if (state.max_amount) q.set('max_amount', state.max_amount)
    if (state.sla) q.set('sla_breached_only', 'true')
    q.set('page', String(state.page))
    q.set('size', String(state.size))
    q.set('sort_by', state.sort_by)
    q.set('sort_dir', state.sort_dir)
    return q
  }, [JSON.stringify(state)])

  const { data, loading, error, refetch } = useApi(
    () => http.get(`/requests?${buildQuery().toString()}`),
    [buildQuery],
  )

  const update = (patch, { resetPage = true } = {}) => {
    const next = new URLSearchParams(buildQuery())
    Object.entries(patch).forEach(([key, value]) => {
      if (key === 'status' || key === 'priority') {
        next.delete(key)
        value.forEach((v) => next.append(key, v))
      } else if (value === '' || value === null || value === undefined || value === false || value === 'all') {
        next.delete(key)
      } else {
        next.set(key, String(value === true ? '1' : value))
      }
    })
    if (resetPage && !('page' in patch)) next.set('page', '1')
    setParams(next, { replace: true })
  }

  const toggle = (key, value) => {
    const list = state[key]
    update({ [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] })
  }

  const columns = [
    {
      key: 'reference_no',
      label: 'Reference',
      sortable: true,
      width: 150,
      render: (row) => <Link to={`/requests/${row.id}`} className="ref">{row.reference_no}</Link>,
    },
    {
      key: 'title',
      label: 'Title',
      render: (row) => (
        <div>
          <div style={{ fontWeight: 600 }}>{row.title}</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            {row.request_type_name} · {row.department_name || 'No department'}
          </div>
        </div>
      ),
    },
    { key: 'requester_name', label: 'Requester', width: 150 },
    {
      key: 'amount',
      label: 'Amount',
      sortable: true,
      numeric: true,
      width: 130,
      render: (row) => <Money amount={row.amount} currency={row.currency} />,
    },
    { key: 'priority', label: 'Priority', width: 100, render: (row) => <Badge value={row.priority} /> },
    {
      key: 'status',
      label: 'Status',
      sortable: true,
      width: 160,
      render: (row) => (
        <span className="row" style={{ gap: 6 }}>
          <Badge value={row.status} />
          {row.current_step_level > 0 && <span className="muted" style={{ fontSize: 11 }}>L{row.current_step_level}</span>}
        </span>
      ),
    },
    {
      key: 'sla_due_at',
      label: 'SLA due',
      sortable: true,
      width: 160,
      render: (row) => {
        if (!row.sla_due_at) return <span className="muted">—</span>
        const breached = new Date(row.sla_due_at) < new Date() && ['submitted', 'pending_approval'].includes(row.status)
        return <span className={breached ? 'badge sla' : ''}><DateTime value={row.sla_due_at} /></span>
      },
    },
    { key: 'created_at', label: 'Created', sortable: true, width: 150, render: (row) => <DateTime value={row.created_at} /> },
  ]

  return (
    <>
      <PageHead title="Business requests" subtitle="Filter, search and drill into any request and its approval chain.">
        <Link to="/requests/new" className="btn">+ New request</Link>
        <button className="ghost" onClick={refetch}>Refresh</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>

      <div className="card">
        <div className="filters">
          <label className="field" style={{ margin: 0, gridColumn: 'span 2' }}>
            <span>Search</span>
            <input
              type="text"
              placeholder="Reference, title or requester…"
              value={draftFilters.search}
              onChange={(e) => setDraftFilters({ ...draftFilters, search: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && update({ search: draftFilters.search })}
            />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Scope</span>
            <select value={state.mine} onChange={(e) => update({ mine: e.target.value })}>
              <option value="all">Everything I can see</option>
              <option value="created">Raised by me</option>
              <option value="approvals">Waiting on my approval</option>
            </select>
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Min amount</span>
            <input
              type="number" min="0" value={draftFilters.min_amount}
              onChange={(e) => setDraftFilters({ ...draftFilters, min_amount: e.target.value })}
              onBlur={() => update({ min_amount: draftFilters.min_amount })}
            />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Max amount</span>
            <input
              type="number" min="0" value={draftFilters.max_amount}
              onChange={(e) => setDraftFilters({ ...draftFilters, max_amount: e.target.value })}
              onBlur={() => update({ max_amount: draftFilters.max_amount })}
            />
          </label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
            <button className="sm" onClick={() => update({ search: draftFilters.search, min_amount: draftFilters.min_amount, max_amount: draftFilters.max_amount })}>
              Apply
            </button>
            <button
              className={`sm ${state.sla ? 'danger' : 'ghost'}`}
              onClick={() => update({ sla: !state.sla })}
            >
              SLA breached
            </button>
            <button className="sm ghost" onClick={() => { setDraftFilters({ search: '', min_amount: '', max_amount: '' }); setParams(new URLSearchParams(), { replace: true }) }}>
              Clear
            </button>
          </div>
        </div>

        <div style={{ marginTop: 12 }} className="row">
          <span className="muted" style={{ fontSize: 12 }}>Status:</span>
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={`sm ${state.status.includes(status) ? '' : 'ghost'}`}
              onClick={() => toggle('status', status)}
            >
              {status.replace(/_/g, ' ')}
            </button>
          ))}
        </div>
        <div style={{ marginTop: 8 }} className="row">
          <span className="muted" style={{ fontSize: 12 }}>Priority:</span>
          {PRIORITIES.map((priority) => (
            <button
              key={priority}
              type="button"
              className={`sm ${state.priority.includes(priority) ? '' : 'ghost'}`}
              onClick={() => toggle('priority', priority)}
            >
              {priority}
            </button>
          ))}
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={data?.items || []}
        loading={loading}
        empty="No requests match these filters."
        page={data?.page || 1}
        size={data?.size || 20}
        total={data?.total || 0}
        pages={data?.pages || 0}
        onPage={(page) => update({ page }, { resetPage: false })}
        sortBy={state.sort_by}
        sortDir={state.sort_dir}
        onSort={(sort_by, sort_dir) => update({ sort_by, sort_dir })}
        onRowClick={(row) => navigate(`/requests/${row.id}`)}
      />
    </>
  )
}
