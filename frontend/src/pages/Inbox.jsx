import { useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, Badge, DataTable, DateTime, Money, PageHead } from '../components/ui'

export default function Inbox() {
  const inbox = useApi(() => http.get('/requests/inbox'), [])
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [remarks, setRemarks] = useState('')
  const [reason, setReason] = useState('')

  const decide = async (id, verb, body) => {
    setBusy(`${verb}-${id}`)
    setError(null)
    setNotice(null)
    try {
      const updated = await http.post(`/requests/${id}/${verb}`, body)
      setNotice(`${updated.reference_no} → ${updated.status.replace(/_/g, ' ')}`)
      setRemarks('')
      setReason('')
      await inbox.refetch()
    } catch (err) {
      setError(errorMessage(err, `Could not ${verb} the request`))
    } finally {
      setBusy(null)
    }
  }

  const columns = [
    { key: 'reference_no', label: 'Reference', render: (row) => <Link to={`/requests/${row.id}`} className="ref">{row.reference_no}</Link> },
    { key: 'title', label: 'Title', render: (row) => <div><div style={{ fontWeight: 600 }}>{row.title}</div><div className="muted" style={{ fontSize: 11.5 }}>{row.request_type_name}</div></div> },
    { key: 'requester_name', label: 'Requester', width: 150 },
    { key: 'amount', label: 'Amount', numeric: true, width: 120, render: (row) => <Money amount={row.amount} currency={row.currency} /> },
    { key: 'priority', label: 'Priority', width: 100, render: (row) => <Badge value={row.priority} /> },
    { key: 'sla_due_at', label: 'SLA due', width: 150, render: (row) => <DateTime value={row.sla_due_at} /> },
    {
      key: 'actions',
      label: 'Decision',
      width: 260,
      render: (row) => (
        <div className="btn-row">
          <button className="success sm" disabled={busy} onClick={() => decide(row.id, 'approve', { remarks })}>
            {busy === `approve-${row.id}` ? '…' : 'Approve'}
          </button>
          <button className="danger sm" disabled={busy || reason.trim().length < 3}
            onClick={() => decide(row.id, 'reject', { reason })}>
            Reject
          </button>
          <Link to={`/requests/${row.id}`}>Review</Link>
        </div>
      ),
    },
  ]

  return (
    <>
      <PageHead title="My approvals" subtitle="Requests where you are the assigned approver for the current level.">
        <button className="ghost" onClick={inbox.refetch}>Refresh</button>
      </PageHead>

      <Alert kind="error">{error}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <div className="card">
        <div className="field-row">
          <label className="field" style={{ margin: 0 }}>
            <span>Remarks applied to every approval</span>
            <input type="text" value={remarks} onChange={(e) => setRemarks(e.target.value)} placeholder="e.g. Within budget" />
          </label>
          <label className="field" style={{ margin: 0 }}>
            <span>Reason applied to every rejection (min 3 chars)</span>
            <input type="text" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why is this being sent back?" />
          </label>
        </div>
      </div>

      <DataTable columns={columns} rows={inbox.data || []} loading={inbox.loading} empty="Your inbox is clear." />
    </>
  )
}
