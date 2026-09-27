import { useState } from 'react'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, Badge, DataTable, DateTime, PageHead } from '../components/ui'
import { ROLES, useAuth } from '../api/AuthContext'

const EMPTY = {
  full_name: '', email: '', employee_id: '', password: '',
  role: 'employee', designation: '', department_id: '', manager_id: '',
}

export default function AdminUsers() {
  const { can } = useAuth()
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [page, setPage] = useState(1)
  const [form, setForm] = useState(EMPTY)
  const [showForm, setShowForm] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const query = `?page=${page}&size=15${search ? `&search=${encodeURIComponent(search)}` : ''}${roleFilter ? `&role=${roleFilter}` : ''}`
  const users = useApi(() => http.get(`/users${query}`), [query])
  const departments = useApi(() => http.get('/admin/departments'), [])

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const create = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await http.post('/users', {
        ...form,
        department_id: form.department_id ? Number(form.department_id) : null,
        manager_id: form.manager_id ? Number(form.manager_id) : null,
      })
      setNotice(`${form.full_name} created with role ${ROLES[form.role]}.`)
      setForm(EMPTY)
      setShowForm(false)
      users.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not create the user'))
    } finally {
      setBusy(false)
    }
  }

  const toggleActive = async (row) => {
    setError(null)
    try {
      await http.patch(`/users/${row.id}`, { is_active: !row.is_active })
      setNotice(`${row.full_name} is now ${row.is_active ? 'deactivated' : 'active'}.`)
      users.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not update the user'))
    }
  }

  const changeRole = async (row, role) => {
    setError(null)
    try {
      await http.patch(`/users/${row.id}`, { role })
      setNotice(`${row.full_name} is now ${ROLES[role]}.`)
      users.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not change the role'))
    }
  }

  const remove = async (row) => {
    if (!window.confirm(`Soft-delete ${row.full_name}? Their history is preserved.`)) return
    setError(null)
    try {
      await http.delete(`/users/${row.id}`)
      setNotice(`${row.full_name} soft-deleted.`)
      users.refetch()
    } catch (err) {
      setError(errorMessage(err, 'Could not delete the user'))
    }
  }

  const columns = [
    { key: 'employee_id', label: 'Employee ID', width: 120, render: (row) => <span className="mono">{row.employee_id}</span> },
    { key: 'full_name', label: 'Name', render: (row) => <div><div style={{ fontWeight: 600 }}>{row.full_name}</div><div className="muted" style={{ fontSize: 11.5 }}>{row.designation || '—'}</div></div> },
    { key: 'email', label: 'Email' },
    { key: 'department', label: 'Department', width: 160, render: (row) => row.department?.name || <span className="muted">—</span> },
    { key: 'role', label: 'Role', width: 150, render: (row) => (
      can('grant-admin') ? (
        <select value={row.role} onChange={(e) => changeRole(row, e.target.value)} style={{ padding: '4px 6px' }}>
          {Object.entries(ROLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      ) : <Badge value={row.role} />
    ) },
    { key: 'is_active', label: 'State', width: 90, render: (row) => (row.is_active ? <span className="badge approved">active</span> : <span className="badge rejected">inactive</span>) },
    { key: 'last_login_at', label: 'Last login', width: 150, render: (row) => <DateTime value={row.last_login_at} /> },
    { key: 'actions', label: '', width: 150, render: (row) => (
      <div className="btn-row">
        <button className="ghost sm" onClick={() => toggleActive(row)}>{row.is_active ? 'Deactivate' : 'Activate'}</button>
        <button className="danger sm" onClick={() => remove(row)}>Delete</button>
      </div>
    ) },
  ]

  return (
    <>
      <PageHead title="Users & roles" subtitle="RBAC: employee → manager → admin → super admin.">
        <button className="ghost" onClick={users.refetch}>Refresh</button>
        <button onClick={() => setShowForm(!showForm)}>{showForm ? 'Close' : '+ Add user'}</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      {showForm && (
        <form className="card" onSubmit={create}>
          <h3>New user</h3>
          <div className="field-row">
            <label className="field"><span>Full name *</span><input type="text" value={form.full_name} onChange={set('full_name')} required minLength={2} /></label>
            <label className="field"><span>Work email *</span><input type="email" value={form.email} onChange={set('email')} required /></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Employee ID * (e.g. ERP-10011)</span>
              <input type="text" value={form.employee_id} onChange={(e) => setForm({ ...form, employee_id: e.target.value.toUpperCase() })} required pattern="[A-Z]{2,4}-\d{3,6}" /></label>
            <label className="field"><span>Temporary password *</span>
              <input type="text" value={form.password} onChange={set('password')} required minLength={8} />
              <div className="help">8+ characters with an uppercase letter, a lowercase letter and a digit.</div></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Role *</span>
              <select value={form.role} onChange={set('role')}>
                {Object.entries(ROLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
            <label className="field"><span>Designation</span><input type="text" value={form.designation} onChange={set('designation')} /></label>
          </div>
          <div className="field-row">
            <label className="field"><span>Department</span>
              <select value={form.department_id} onChange={set('department_id')}>
                <option value="">— none —</option>
                {(departments.data || []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select></label>
            <label className="field"><span>Reporting manager</span>
              <select value={form.manager_id} onChange={set('manager_id')}>
                <option value="">— none —</option>
                {(users.data?.items || []).filter((u) => u.role !== 'employee').map((u) => <option key={u.id} value={u.id}>{u.full_name} ({ROLES[u.role]})</option>)}
              </select></label>
          </div>
          <button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button>
        </form>
      )}

      <div className="card">
        <div className="filters">
          <label className="field" style={{ margin: 0, gridColumn: 'span 2' }}>
            <span>Search</span>
            <input type="text" placeholder="Name, email or employee ID…" value={search}
              onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && setPage(1)} />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Role</span>
            <select value={roleFilter} onChange={(e) => { setRoleFilter(e.target.value); setPage(1) }}>
              <option value="">All roles</option>
              {Object.entries(ROLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
      </div>

      <DataTable columns={columns} rows={users.data?.items || []} loading={users.loading}
        empty="No users match." page={users.data?.page || 1} total={users.data?.total || 0}
        pages={users.data?.pages || 0} onPage={setPage} />
    </>
  )
}
