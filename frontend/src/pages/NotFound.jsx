import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="card" style={{ textAlign: 'center', padding: 48 }}>
      <h1>404</h1>
      <p className="muted">That page does not exist.</p>
      <Link to="/dashboard" className="btn">Back to dashboard</Link>
    </div>
  )
}
