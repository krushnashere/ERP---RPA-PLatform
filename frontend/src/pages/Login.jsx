import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../api/AuthContext'
import { errorMessage } from '../api/client'
import { Alert } from '../components/ui'

const DEMO_USERS = [
  { label: 'Super admin — Aditi Sharma', email: 'admin@acme-erp.com', password: 'Admin@123' },
  { label: 'Admin — Priya Deshmukh (Finance)', email: 'priya.admin@acme-erp.com', password: 'Passw0rd!' },
  { label: 'Manager — Rahul Kulkarni (IT)', email: 'rahul.manager@acme-erp.com', password: 'Passw0rd!' },
  { label: 'Employee — Anita Fernandes (IT)', email: 'anita.emp@acme-erp.com', password: 'Passw0rd!' },
]

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('anita.emp@acme-erp.com')
  const [password, setPassword] = useState('Passw0rd!')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const submit = async (event) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email.trim().toLowerCase(), password)
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError(errorMessage(err, 'Login failed'))
    } finally {
      setBusy(false)
    }
  }

  const useDemo = (demo) => {
    setEmail(demo.email)
    setPassword(demo.password)
    setError(null)
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>Enterprise ERP &amp; RPA Platform</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Business requests, multi-level approvals and automated fulfilment.
        </p>

        <Alert kind="error">{error}</Alert>

        <form onSubmit={submit}>
          <label className="field">
            <span>Work email</span>
            <input
              type="email"
              value={email}
              autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={6}
            />
          </label>
          <button type="submit" disabled={busy} style={{ width: '100%' }}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <div className="demo-users">
          <div className="muted" style={{ marginBottom: 6 }}>Demo accounts (click to fill):</div>
          {DEMO_USERS.map((demo) => (
            <button key={demo.email} type="button" className="ghost sm" onClick={() => useDemo(demo)}>
              {demo.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
