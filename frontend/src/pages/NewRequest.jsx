import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, Badge, PageHead } from '../components/ui'

const EMPTY_ITEM = { sku: '', qty: 1, unit_price: 0 }

export default function NewRequest() {
  const navigate = useNavigate()
  const types = useApi(() => http.get('/admin/request-types'), [])
  const departments = useApi(() => http.get('/admin/departments'), [])

  const [form, setForm] = useState({
    request_type_id: '',
    title: '',
    description: '',
    amount: 0,
    currency: 'INR',
    priority: 'normal',
    department_id: '',
    submit_immediately: true,
  })
  const [items, setItems] = useState([EMPTY_ITEM])
  const [vendor, setVendor] = useState({ name: '', gstin: '' })
  const [attachment, setAttachment] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const selectedType = useMemo(
    () => (types.data || []).find((t) => String(t.id) === String(form.request_type_id)),
    [types.data, form.request_type_id],
  )

  const itemsTotal = items.reduce((sum, item) => sum + (Number(item.qty) || 0) * (Number(item.unit_price) || 0), 0)
  const exceedsLimit = selectedType?.amount_limit > 0 && Number(form.amount) > selectedType.amount_limit
  const autoApproves = selectedType?.auto_approve_below != null && Number(form.amount) <= selectedType.auto_approve_below
  const needsAttachment = selectedType?.requires_attachment && !autoApproves && !attachment

  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value })

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const payload = {
        line_items: items.filter((item) => item.sku),
        vendor: vendor.name ? vendor : undefined,
        items_total: itemsTotal,
      }
      const created = await http.post('/requests', {
        ...form,
        request_type_id: Number(form.request_type_id),
        department_id: form.department_id ? Number(form.department_id) : null,
        amount: Number(form.amount),
        payload,
        // hold it as a draft when a document is still required but not uploaded yet
        submit_immediately: needsAttachment ? false : form.submit_immediately,
      })

      if (attachment) {
        const data = new FormData()
        data.append('file', attachment)
        await http.upload(`/requests/${created.id}/attachments`, data)
        if (needsAttachment) await http.post(`/requests/${created.id}/submit`, {})
      }
      navigate(`/requests/${created.id}`)
    } catch (err) {
      setError(errorMessage(err, 'Could not create the request'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHead title="New request" subtitle="The approval chain is built from the workflow definition when you submit." />
      <Alert kind="error">{error}</Alert>

      <form onSubmit={submit} className="grid cols-2">
        <div className="card">
          <h3>Request</h3>
          <label className="field">
            <span>Workflow / request type *</span>
            <select value={form.request_type_id} onChange={set('request_type_id')} required>
              <option value="">Select…</option>
              {(types.data || []).map((type) => (
                <option key={type.id} value={type.id}>{type.code} — {type.name}</option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Title *</span>
            <input type="text" value={form.title} onChange={set('title')} required minLength={3} maxLength={180}
              placeholder="e.g. Dell PowerEdge servers for the Pune DC" />
          </label>

          <label className="field">
            <span>Description</span>
            <textarea value={form.description} onChange={set('description')} placeholder="Business justification…" />
          </label>

          <div className="field-row">
            <label className="field">
              <span>Amount *</span>
              <input type="number" min="0" step="0.01" value={form.amount} onChange={set('amount')} required />
              {exceedsLimit && <div className="help" style={{ color: '#b91c1c' }}>Above the {selectedType.code} limit.</div>}
              {autoApproves && <div className="help">Under {selectedType.auto_approve_below} → auto-approved on submit.</div>}
            </label>
            <label className="field">
              <span>Currency</span>
              <select value={form.currency} onChange={set('currency')}>
                {['INR', 'USD', 'EUR', 'GBP'].map((c) => <option key={c}>{c}</option>)}
              </select>
            </label>
          </div>

          <div className="field-row">
            <label className="field">
              <span>Priority</span>
              <select value={form.priority} onChange={set('priority')}>
                {['low', 'normal', 'high', 'critical'].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Department</span>
              <select value={form.department_id} onChange={set('department_id')}>
                <option value="">My department</option>
                {(departments.data || []).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
            </label>
          </div>

          {selectedType && (
            <div className="alert info">
              <strong>{selectedType.name}</strong> · SLA {selectedType.sla_hours}h
              {selectedType.approval_chain?.length > 0 && (
                <div style={{ marginTop: 6 }} className="row">
                  {selectedType.approval_chain.map((level) => (
                    <span key={level.level} className="row" style={{ gap: 4 }}>
                      <Badge value={level.role} />
                      <span className="muted" style={{ fontSize: 11 }}>
                        L{level.level}{level.min_amount ? ` ≥ ${level.min_amount.toLocaleString('en-IN')}` : ''}
                      </span>
                    </span>
                  ))}
                </div>
              )}
              {selectedType.rpa_process_id && <div style={{ marginTop: 6 }}>On approval this triggers an automated (RPA) job.</div>}
            </div>
          )}

          <label className="field row" style={{ alignItems: 'center', gap: 8 }}>
            <input type="checkbox" style={{ width: 'auto' }} checked={form.submit_immediately}
              onChange={(e) => setForm({ ...form, submit_immediately: e.target.checked })} />
            <span style={{ margin: 0 }}>Submit for approval immediately (uncheck to save as draft)</span>
          </label>
        </div>

        <div className="card">
          <h3>Business data</h3>
          <p className="muted" style={{ marginTop: 0 }}>Stored as a JSON payload on the request and handed to the automation.</p>

          <div className="field-row">
            <label className="field">
              <span>Vendor name</span>
              <input type="text" value={vendor.name} onChange={(e) => setVendor({ ...vendor, name: e.target.value })} />
            </label>
            <label className="field">
              <span>GSTIN (15 chars)</span>
              <input type="text" value={vendor.gstin} maxLength={15}
                onChange={(e) => setVendor({ ...vendor, gstin: e.target.value.toUpperCase() })} />
            </label>
          </div>

          <h3 style={{ marginTop: 14 }}>Line items</h3>
          <div className="stack">
            {items.map((item, index) => (
              <div className="filters" key={index} style={{ gridTemplateColumns: '2fr 1fr 1fr auto' }}>
                <input type="text" placeholder="SKU / description" value={item.sku}
                  onChange={(e) => { const next = [...items]; next[index] = { ...item, sku: e.target.value }; setItems(next) }} />
                <input type="number" min="0" placeholder="Qty" value={item.qty}
                  onChange={(e) => { const next = [...items]; next[index] = { ...item, qty: e.target.value }; setItems(next) }} />
                <input type="number" min="0" step="0.01" placeholder="Unit price" value={item.unit_price}
                  onChange={(e) => { const next = [...items]; next[index] = { ...item, unit_price: e.target.value }; setItems(next) }} />
                <button type="button" className="ghost sm" disabled={items.length === 1}
                  onClick={() => setItems(items.filter((_, i) => i !== index))}>✕</button>
              </div>
            ))}
            <div className="row">
              <button type="button" className="ghost sm" onClick={() => setItems([...items, EMPTY_ITEM])}>+ Add line</button>
              <span className="muted">Items total: <strong>{itemsTotal.toLocaleString('en-IN')}</strong></span>
              {itemsTotal > 0 && Number(form.amount) !== itemsTotal && (
                <button type="button" className="ghost sm" onClick={() => setForm({ ...form, amount: itemsTotal })}>
                  Use items total as amount
                </button>
              )}
            </div>
          </div>

          <h3 style={{ marginTop: 14 }}>Supporting document</h3>
          <label className="field">
            <input type="file" accept=".pdf,.png,.jpg,.csv,.xlsx,.docx" onChange={(e) => setAttachment(e.target.files?.[0] || null)} />
            {selectedType?.requires_attachment && (
              <div className="help" style={{ color: needsAttachment ? '#b45309' : undefined }}>
                {needsAttachment ? 'Required by this workflow — it will be uploaded before submission.' : 'Attached ✓'}
              </div>
            )}
          </label>
        </div>

        <div style={{ gridColumn: '1 / -1' }} className="btn-row">
          <button type="submit" disabled={busy || !form.request_type_id || exceedsLimit}>
            {busy ? 'Saving…' : form.submit_immediately ? 'Create & submit' : 'Save as draft'}
          </button>
          <button type="button" className="ghost" onClick={() => navigate('/requests')}>Cancel</button>
        </div>
      </form>
    </>
  )
}
