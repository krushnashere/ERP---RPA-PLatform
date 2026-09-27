import { useState } from 'react'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, DataTable, PageHead } from '../components/ui'

const HANDLERS = [
  'app.worker.handlers.create_vendor_in_sap',
  'app.worker.handlers.generate_monthly_report',
  'app.worker.handlers.sync_leave_balance',
  'app.worker.handlers.archive_request_documents',
]

export default function RpaProcesses() {
  const processes = useApi(() => http.get('/rpa/processes'), [])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    code: '', name: '', description: '', handler: HANDLERS[0],
    max_attempts: 3, backoff_base_seconds: 2, timeout_seconds: 300, is_active: true,
  })

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const create = async (event) => {
    event.preventDefault()
    setError(null)
    try {
      await http.post('/rpa/processes', {
        ...form,
        max_attempts: Number(form.max_attempts),
        backoff_base_seconds: Number(form.backoff_base_seconds),
        timeout_seconds: Number(form.timeout_seconds),
      })
      setNotice(`Process '${form.code}' registered.`)
      setShowForm(false)
      setForm({ ...form, code: '', name: '', description: '' })
      processes.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not register the process'))
    }
  }

  const toggleActive = async (row) => {
    setError(null)
    try {
      await http.patch(`/rpa/processes/${row.id}`, { is_active: !row.is_active })
      processes.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not update the process'))
    }
  }

  const columns = [
    { key: 'code', label: 'Code', render: (row) => <span className="mono">{row.code}</span> },
    { key: 'name', label: 'Name', render: (row) => <div><div style={{ fontWeight: 600 }}>{row.name}</div><div className="muted mono" style={{ fontSize: 11 }}>{row.handler}</div></div> },
    { key: 'max_attempts', label: 'Retries', numeric: true, width: 80 },
    { key: 'backoff_base_seconds', label: 'Backoff base', numeric: true, width: 110 },
    { key: 'timeout_seconds', label: 'Timeout (s)', numeric: true, width: 100 },
    { key: 'jobs_total', label: 'Jobs', numeric: true, width: 80 },
    {
      key: 'jobs_failed', label: 'Failed', numeric: true, width: 80,
      render: (row) => (row.jobs_failed > 0 ? <span className="badge failed">{row.jobs_failed}</span> : 0),
    },
    { key: 'is_active', label: 'State', width: 100, render: (row) => (row.is_active ? <span className="badge approved">active</span> : <span className="badge cancelled">paused</span>) },
    { key: 'actions', label: '', width: 100, render: (row) => <button className="ghost sm" onClick={() => toggleActive(row)}>{row.is_active ? 'Pause' : 'Resume'}</button> },
  ]

  return (
    <>
      <PageHead title="RPA processes" subtitle="Each process maps to a Python handler executed by a worker.">
        <button className="ghost" onClick={processes.refetch}>Refresh</button>
        <button onClick={() => setShowForm(!showForm)}>{showForm ? 'Close' : '+ Register process'}</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      {showForm && (
        <form className="card" onSubmit={create}>
          <h3>Register an automation</h3>
          <div className="field-row">
            <label className="field"><span>Code *</span>
              <input type="text" value={form.code} onChange={set('code')} required pattern="[a-z0-9_]+" placeholder="create_vendor_in_sap" />
            </label>
            <label className="field"><span>Name *</span>
              <input type="text" value={form.name} onChange={set('name')} required placeholder="Create vendor in SAP" />
            </label>
          </div>
          <label className="field"><span>Handler (python dotted path) *</span>
            <select value={form.handler} onChange={set('handler')}>
              {HANDLERS.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
            <div className="help">The worker imports this function and calls it with the job payload.</div>
          </label>
          <div className="field-row">
            <label className="field"><span>Max attempts</span>
              <input type="number" min="1" max="10" value={form.max_attempts} onChange={set('max_attempts')} /></label>
            <label className="field"><span>Backoff base (seconds)</span>
              <input type="number" min="0.1" step="0.1" value={form.backoff_base_seconds} onChange={set('backoff_base_seconds')} /></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Timeout (seconds)</span>
              <input type="number" min="1" value={form.timeout_seconds} onChange={set('timeout_seconds')} /></label>
            <label className="field"><span>Description</span>
              <input type="text" value={form.description} onChange={set('description')} /></label>
          </div>
          <button type="submit">Register process</button>
        </form>
      )}

      <DataTable columns={columns} rows={processes.data || []} loading={processes.loading} empty="No processes registered." />
    </>
  )
}
