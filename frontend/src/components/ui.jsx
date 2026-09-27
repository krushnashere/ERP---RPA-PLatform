/** Small shared UI primitives: badges, table with sorting + pagination, alerts. */

export const STATUS_LABELS = {
  draft: 'Draft',
  submitted: 'Submitted',
  pending_approval: 'Pending approval',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  completed: 'Completed',
  queued: 'Queued',
  running: 'Running',
  retry_scheduled: 'Retry scheduled',
  succeeded: 'Succeeded',
  failed: 'Failed',
  dead_letter: 'Dead letter',
  pending: 'Pending',
  skipped: 'Skipped',
}

export function Badge({ value, prefix = '' }) {
  if (value === null || value === undefined || value === '') return <span className="muted">—</span>
  const key = String(value)
  return <span className={`badge ${prefix}${key}`}>{STATUS_LABELS[key] || key.replace(/_/g, ' ')}</span>
}

export function Money({ amount, currency = 'INR' }) {
  if (amount === null || amount === undefined) return <span className="muted">—</span>
  const formatted = new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(amount)
  return <span className="nowrap">{formatted}</span>
}

export function DateTime({ value, withTime = true }) {
  if (!value) return <span className="muted">—</span>
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return <span className="muted">{String(value)}</span>
  return (
    <span className="nowrap" title={date.toISOString()}>
      {date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}
      {withTime ? ` · ${date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}` : ''}
    </span>
  )
}

export function Alert({ kind = 'error', children, onDismiss }) {
  if (!children) return null
  return (
    <div className={`alert ${kind}`}>
      {children}
      {onDismiss && (
        <button type="button" className="ghost sm" style={{ marginLeft: 10 }} onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  )
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="stack" style={{ padding: 12 }}>
      <div className="skeleton" style={{ width: '45%' }} />
      <div className="skeleton" style={{ width: '80%' }} />
      <div className="skeleton" style={{ width: '65%' }} />
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
    </div>
  )
}

/**
 * Generic sortable, paginated table.
 * columns: [{ key, label, render?(row), sortable?, numeric?, width? }]
 */
export function DataTable({
  columns,
  rows,
  loading,
  empty = 'Nothing to show yet.',
  page = 1,
  size = 20,
  total = 0,
  pages = 0,
  onPage,
  sortBy,
  sortDir,
  onSort,
  onRowClick,
}) {
  if (loading) {
    return (
      <div className="table-wrap">
        <Spinner label="Fetching rows…" />
      </div>
    )
  }

  const handleClick = (column) => {
    if (!column.sortable || !onSort) return
    const nextDir = sortBy === column.key && sortDir === 'desc' ? 'asc' : 'desc'
    onSort(column.key, nextDir)
  }

  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  style={{ width: column.width, cursor: column.sortable ? 'pointer' : 'default' }}
                  onClick={() => handleClick(column)}
                  title={column.sortable ? 'Click to sort' : undefined}
                >
                  {column.label}
                  {column.sortable && sortBy === column.key ? (sortDir === 'desc' ? ' ▾' : ' ▴') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="muted" style={{ padding: 22, textAlign: 'center' }}>
                  {empty}
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr
                key={row.id ?? row.key}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                style={onRowClick ? { cursor: 'pointer' } : undefined}
              >
                {columns.map((column) => (
                  <td key={column.key} className={column.numeric ? 'num' : undefined}>
                    {column.render ? column.render(row) : row[column.key]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {total > 0 && (
        <div className="pager">
          <span className="muted">
            {total} row{total === 1 ? '' : 's'} · page {page} of {pages || 1}
          </span>
          <button className="ghost sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
            Previous
          </button>
          <button className="ghost sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
            Next
          </button>
        </div>
      )}
    </>
  )
}

export function PageHead({ title, subtitle, children }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      <div className="spacer" />
      {children && <div className="btn-row">{children}</div>}
    </div>
  )
}
