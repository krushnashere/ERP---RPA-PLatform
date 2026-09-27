import { useState } from 'react'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, DataTable, PageHead } from '../components/ui'

const ROLES_IN_CHAIN = ['manager', 'admin', 'super_admin']

export default function AdminRequestTypes() {
  const types = useApi(() => http.get('/admin/request-types?include_inactive=true'), [])
  const processes = useApi(() => http.get('/rpa/processes'), [])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    code: '', name: '', description: '', amount_limit: 0, sla_hours: 48,
    requires_attachment: false, auto_approve_below: '', rpa_process_id: '',
    approval_chain: [{ level: 1, role: 'manager', min_amount: 0 }],
  })

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const setLevel = (index, key, value) => {
    const chain = [...form.approval_chain]
    chain[index] = { ...chain[index], [key]: key === 'level' || key === 'min_amount' ? Number(value) : value }
    setForm({ ...form, approval_chain: chain })
  }

  const create = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await http.post('/admin/request-types', {
        ...form,
        amount_limit: Number(form.amount_limit),
        sla_hours: Number(form.sla_hours),
        auto_approve_below: form.auto_approve_below === '' ? null : Number(form.auto_approve_below),
        rpa_process_id: form.rpa_process_id ? Number(form.rpa_process_id) : null,
      })
      setNotice(`Workflow '${form.code}' created.`)
      setShowForm(false)
      types.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not create the workflow'))
    } finally {
      setBusy(false)
    }
  }

  const deactivate = async (row) => {
    if (!window.confirm(`Deactivate ${row.code}? Existing requests are unaffected.`)) return
    try {
      await http.delete(`/admin/request-types/${row.id}`)
      setNotice(`${row.code} deactivated.`)
      types.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not deactivate the workflow'))
    }
  }

  const columns = [
    { key: 'code', label: 'Code', width: 150, render: (row) => <span className="mono">{row.code}</span> },
    { key: 'name', label: 'Name', render: (row) => <div><div style={{ fontWeight: 600 }}>{row.name}</div><div className="muted" style={{ fontSize: 11.5 }}>{row.description}</div></div> },
    { key: 'approval_chain', label: 'Approval chain', width: 280, render: (row) => (
      <div className="row" style={{ gap: 4 }}>
        {(row.approval_chain || []).length === 0 && <span className="muted">none (auto-approve)</span>}
        {(row.approval_chain || []).map((level) => (
          <span key={level.level} className="badge manager" title={`min amount ${level.min_amount}`}>
            L{level.level}: {level.role}{level.min_amount ? ` ≥ ${Number(level.min_amount).toLocaleString('en-IN')}` : ''}
          </span>
        ))}
      </div>
    ) },
    { key: 'amount_limit', label: 'Limit', numeric: true, width: 110, render: (row) => (row.amount_limit ? row.amount_limit.toLocaleString('en-IN') : '—') },
    { key: 'auto_approve_below', label: 'Auto <', numeric: true, width: 90, render: (row) => (row.auto_approve_below ?? '—') },
    { key: 'sla_hours', label: 'SLA (h)', numeric: true, width: 80 },
    { key: 'requires_attachment', label: 'Doc', width: 60, render: (row) => (row.requires_attachment ? '✓' : '—') },
    {
      key: 'rpa', label: 'Automation', width: 170,
      render: (row) => {
        const process = (processes.data || []).find((p) => p.id === row.rpa_process_id)
        return process ? <span className="mono" style={{ fontSize: 11 }}>{process.code}</span> : <span className="muted">manual</span>
      },
    },
    { key: 'is_active', label: 'State', width: 90, render: (row) => (row.is_active ? <span className="badge approved">active</span> : <span className="badge cancelled">off</span>) },
    { key: 'actions', label: '', width: 110, render: (row) => row.is_active && <button className="danger sm" onClick={() => deactivate(row)}>Deactivate</button> },
  ]

  return (
    <>
      <PageHead title="Workflow definitions" subtitle="Approval chains are data, not code — add a level without a deploy.">
        <button className="ghost" onClick={types.refetch}>Refresh</button>
        <button onClick={() => setShowForm(!showForm)}>{showForm ? 'Close' : '+ New workflow'}</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      {showForm && (
        <form className="card" onSubmit={create}>
          <h3>New request type</h3>
          <div className="field-row">
            <label className="field"><span>Code *</span>
              <input type="text" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })} required pattern="[A-Z0-9_]+" placeholder="PURCHASE_ORDER" /></label>
            <label className="field"><span>Name *</span><input type="text" value={form.name} onChange={set('name')} required /></label>
          </div>
          <label className="field"><span>Description</span><input type="text" value={form.description} onChange={set('description')} /></label>
          <div className="field-row">
            <label className="field"><span>Amount limit</span><input type="number" min="0" value={form.amount_limit} onChange={set('amount_limit')} /></label>
            <label className="field"><span>SLA (hours)</span><input type="number" min="0" value={form.sla_hours} onChange={set('sla_hours')} /></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Auto-approve below</span><input type="number" min="0" value={form.auto_approve_below} onChange={set('auto_approve_below')} placeholder="leave blank to disable" /></label>
            <label className="field"><span>Trigger automation on approval</span>
              <select value={form.rpa_process_id} onChange={set('rpa_process_id')}>
                <option value="">— none —</option>
                {(processes.data || []).map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
              </select></label>
          </div>
          <label className="field row" style={{ alignItems: 'center', gap: 8 }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={form.requires_attachment}
              onChange={(e) => setForm({ ...form, requires_attachment: e.target.checked })} />
            <span style={{ margin: 0 }}>A supporting document is mandatory</span>
          </label>

          <h3 style={{ marginTop: 12 }}>Approval chain</h3>
          {form.approval_chain.map((level, index) => (
            <div className="filters" key={index} style={{ gridTemplateColumns: '80px 1fr 1fr auto' }}>
              <input type="number" min="1" value={level.level} onChange={(e) => setLevel(index, 'level', e.target.value)} placeholder="Level" />
              <select value={level.role} onChange={(e) => setLevel(index, 'role', e.target.value)}>
                {ROLES_IN_CHAIN.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              <input type="number" min="0" value={level.min_amount} onChange={(e) => setLevel(index, 'min_amount', e.target.value)} placeholder="min amount" />
              <button type="button" className="ghost sm" disabled={form.approval_chain.length === 1}
                onClick={() => setForm({ ...form, approval_chain: form.approval_chain.filter((_, i) => i !== index) })}>✕</button>
            </div>
          ))}
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button type="button" className="ghost sm"
              onClick={() => setForm({ ...form, approval_chain: [...form.approval_chain, { level: form.approval_chain.length + 1, role: 'admin', min_amount: 0 }] })}>
              + Add level
            </button>
            <span className="muted">A level with a min amount only activates above that value (escalation).</span>
          </div>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Create workflow'}</button>
          </div>
        </form>
      )}

      <DataTable columns={columns} rows={types.data || []} loading={types.loading} empty="No workflows defined." />
    </>
  )
}
