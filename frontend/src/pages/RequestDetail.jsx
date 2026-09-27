import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { errorMessage, http } from '../api/client'
import { useApi } from '../components/useApi'
import { Alert, Badge, DateTime, Money, Spinner } from '../components/ui'
import { useAuth } from '../api/AuthContext'

const ACTIONABLE = ['submitted', 'pending_approval']

export default function RequestDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user, can } = useAuth()

  const detail = useApi(() => http.get(`/requests/${id}`), [id])
  const timeline = useApi(() => http.get(`/requests/${id}/timeline`), [id], { immediate: false })
  const auditTrail = useApi(() => http.get(`/requests/${id}/audit`), [id], { immediate: false })
  const jobs = useApi(
    () => (can('browse-queue') ? http.get(`/rpa/jobs?request_id=${id}&size=20`) : Promise.resolve({ items: [] })),
    [id, can],
  )

  const [tab, setTab] = useState('chain')
  const [remarks, setRemarks] = useState('')
  const [reason, setReason] = useState('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [notice, setNotice] = useState(null)

  const req = detail.data

  useEffect(() => {
    if (tab === 'timeline') timeline.refetch()
    if (tab === 'audit' && can('view-audit')) auditTrail.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  if (detail.loading && !req) return <Spinner label="Loading request…" />
  if (detail.error) return <Alert kind="error">{detail.error}</Alert>
  if (!req) return null

  const isRequester = req.requester_id === user?.id
  const myStep = req.steps.find((step) => step.status === 'pending' && step.approver_id === user?.id)
  const canDecide = Boolean(myStep) || user?.role === 'super_admin'

  const run = async (label, fn) => {
    setBusy(label)
    setActionError(null)
    setNotice(null)
    try {
      await fn()
      await detail.refetch()
      setNotice(`${label} completed.`)
    } catch (err) {
      setActionError(errorMessage(err, `${label} failed`))
    } finally {
      setBusy(null)
    }
  }

  const act = (verb, body) => run(verb, async () => {
    await http.post(`/requests/${id}/${verb.toLowerCase()}`, body || {})
    setRemarks('')
    setReason('')
  })

  const upload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    await run('Upload', () => http.upload(`/requests/${id}/attachments`, form))
    event.target.value = ''
  }

  return (
    <>
      <div className="page-head">
        <div>
          <div className="muted mono" style={{ fontSize: 12 }}>{req.reference_no}</div>
          <h1 style={{ marginTop: 2 }}>{req.title}</h1>
          <p className="muted" style={{ margin: '4px 0 0' }}>
            {req.request_type_name} · raised by {req.requester_name} · {req.department_name || 'No department'}
          </p>
        </div>
        <div className="spacer" />
        <div className="btn-row">
          <Badge value={req.status} />
          <Badge value={req.priority} />
          {req.is_sla_breached && <span className="badge sla">SLA breached</span>}
          <button className="ghost sm" onClick={() => navigate('/requests')}>← Back</button>
        </div>
      </div>

      <Alert kind="error">{actionError}</Alert>
      <Alert kind="ok">{notice}</Alert>

      <div className="grid cols-4">
        <div className="kpi"><div className="label">Amount</div><div className="value"><Money amount={req.amount} currency={req.currency} /></div></div>
        <div className="kpi"><div className="label">Current step</div><div className="value">{req.current_step_level ? `Level ${req.current_step_level}` : '—'}</div>
          <div className="hint">{req.steps.length} level(s) in chain</div></div>
        <div className="kpi"><div className="label">Submitted</div><div className="value" style={{ fontSize: 15 }}><DateTime value={req.submitted_at} /></div>
          <div className="hint">SLA due <DateTime value={req.sla_due_at} /></div></div>
        <div className="kpi"><div className="label">Record version</div><div className="value">v{req.version}</div>
          <div className="hint">Optimistic locking</div></div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-head">
          <h3>Actions</h3>
          <div className="spacer" />
          <button className="ghost sm" onClick={() => detail.refetch()}>Refresh</button>
        </div>

        {req.status === 'draft' && (
          <div className="btn-row">
            {isRequester && <button disabled={busy} onClick={() => act('Submit')}>{busy === 'Submit' ? 'Submitting…' : 'Submit for approval'}</button>}
            <span className="muted">Drafts are only visible to you until submitted.</span>
          </div>
        )}

        {ACTIONABLE.includes(req.status) && canDecide && (
          <div className="stack">
            <label className="field" style={{ margin: 0 }}>
              <span>Approval remarks (optional)</span>
              <input type="text" value={remarks} onChange={(e) => setRemarks(e.target.value)} placeholder="e.g. Within Q3 budget" />
            </label>
            <div className="btn-row">
              <button className="success" disabled={busy} onClick={() => act('Approve', { remarks, expected_version: req.version })}>
                {busy === 'Approve' ? 'Approving…' : 'Approve this level'}
              </button>
              <input
                type="text" value={reason} placeholder="Rejection reason (mandatory)"
                onChange={(e) => setReason(e.target.value)} style={{ maxWidth: 340 }}
              />
              <button className="danger" disabled={busy || reason.trim().length < 3}
                onClick={() => act('Reject', { reason, expected_version: req.version })}>
                Reject
              </button>
            </div>
          </div>
        )}

        {['draft', 'submitted', 'pending_approval', 'approved'].includes(req.status) && (isRequester || can('manage-users')) && (
          <div className="btn-row" style={{ marginTop: 10 }}>
            <button className="ghost" disabled={busy} onClick={() => act('Cancel', { remarks: remarks || 'Withdrawn by requester' })}>
              Withdraw request
            </button>
            <span className="muted" style={{ fontSize: 12 }}>
              Cancelling also stops any queued automation for this request.
            </span>
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 10 }}>
          <input type="text" value={comment} placeholder="Add a comment…" onChange={(e) => setComment(e.target.value)} style={{ maxWidth: 420 }} />
          <button className="ghost" disabled={busy || !comment.trim()}
            onClick={() => run('Comment', async () => { await http.post(`/requests/${id}/comments`, { body: comment }); setComment('') })}>
            Comment
          </button>
          <label className="btn ghost" style={{ cursor: 'pointer' }}>
            Attach file
            <input type="file" hidden onChange={upload} accept=".pdf,.png,.jpg,.jpeg,.csv,.xlsx,.docx" />
          </label>
        </div>
      </div>

      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          {['chain', 'payload', 'jobs', 'timeline', 'audit'].map((name) => (
            <button key={name} type="button" className={`sm ${tab === name ? '' : 'ghost'}`} onClick={() => setTab(name)}>
              {name === 'chain' ? 'Approval chain' : name === 'payload' ? 'Business data' : name === 'jobs' ? 'Automation' : name === 'timeline' ? 'Timeline' : 'Audit trail'}
            </button>
          ))}
        </div>

        {tab === 'chain' && (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Level</th><th>Approver</th><th>Role</th><th>Status</th><th>Remarks</th><th>Decided</th></tr></thead>
              <tbody>
                {req.steps.length === 0 && <tr><td colSpan="6" className="muted">No approval steps — this request was auto-approved or is still a draft.</td></tr>}
                {req.steps.map((step) => (
                  <tr key={step.id}>
                    <td>{step.level}</td>
                    <td>{step.approver_name || <span className="muted">unassigned</span>}</td>
                    <td><Badge value={step.approver_role} /></td>
                    <td><Badge value={step.status} /></td>
                    <td>{step.remarks || <span className="muted">—</span>}</td>
                    <td><DateTime value={step.decided_at} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'payload' && (
          <div className="stack">
            {req.description && <p>{req.description}</p>}
            {req.rejection_reason && <Alert kind="error">Rejection reason: {req.rejection_reason}</Alert>}
            <pre className="json">{JSON.stringify(req.payload, null, 2)}</pre>
            {req.attachments?.length > 0 && (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>File</th><th>Type</th><th className="right">Size</th><th>Uploaded</th></tr></thead>
                  <tbody>
                    {req.attachments.map((file) => (
                      <tr key={file.id}>
                        <td className="mono">{file.filename}</td>
                        <td className="muted">{file.content_type}</td>
                        <td className="num">{(file.size_bytes / 1024).toFixed(1)} KB</td>
                        <td><DateTime value={file.created_at} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {req.comments?.length > 0 && (
              <div className="stack">
                <h3>Comments</h3>
                {req.comments.map((entry) => (
                  <div key={entry.id} className="card" style={{ margin: 0, background: '#f8fafc' }}>
                    <strong>{entry.user_name || 'Unknown'}</strong> <span className="muted" style={{ fontSize: 11 }}><DateTime value={entry.created_at} /></span>
                    <div>{entry.body}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {tab === 'jobs' && (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Job</th><th>Process</th><th>Status</th><th className="right">Attempts</th><th>Worker</th><th>Finished</th><th /></tr></thead>
              <tbody>
                {(jobs.data?.items || []).length === 0 && (
                  <tr><td colSpan="7" className="muted">No automation was triggered for this request.</td></tr>
                )}
                {(jobs.data?.items || []).map((job) => (
                  <tr key={job.id}>
                    <td className="ref">{job.job_ref}</td>
                    <td>{job.process_name}</td>
                    <td><Badge value={job.status} /></td>
                    <td className="num">{job.attempts}/{job.max_attempts}</td>
                    <td className="muted mono">{job.worker_id || '—'}</td>
                    <td><DateTime value={job.finished_at} /></td>
                    <td>{can('browse-queue') && <Link to={`/rpa/jobs?job=${job.id}`}>Open →</Link>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'timeline' && (
          timeline.loading ? <Spinner /> : (
            <ul className="timeline">
              {(timeline.data?.events || []).map((event, index) => (
                <li key={index} className={event.type}>
                  <div className="when"><DateTime value={event.at} /> · {event.actor}</div>
                  <div className="what">
                    {event.type === 'comment' ? `Commented: “${event.body}”`
                      : event.type === 'step' ? `Approval level ${event.level} → ${event.status}`
                      : event.action}
                  </div>
                  {event.remarks && <div className="muted">{event.remarks}</div>}
                  {event.notes && <div className="muted">{event.notes}</div>}
                </li>
              ))}
            </ul>
          )
        )}

        {tab === 'audit' && (
          !can('view-audit') ? <p className="muted">The audit trail is restricted to admins.</p>
            : auditTrail.loading ? <Spinner /> : (
              <div className="table-wrap">
                <table className="data">
                  <thead><tr><th>When</th><th>Action</th><th>Actor</th><th>IP</th><th>Change</th></tr></thead>
                  <tbody>
                    {(auditTrail.data || []).map((entry) => (
                      <tr key={entry.id}>
                        <td><DateTime value={entry.created_at} /></td>
                        <td className="mono">{entry.action}</td>
                        <td>{entry.actor_role || 'system'}</td>
                        <td className="muted mono">{entry.ip_address || '—'}</td>
                        <td className="muted" style={{ maxWidth: 420 }}>
                          {entry.new_values ? JSON.stringify(entry.new_values).slice(0, 160) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
        )}
      </div>
    </>
  )
}
