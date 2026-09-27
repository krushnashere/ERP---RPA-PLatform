import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './api/AuthContext'
import Layout from './components/Layout'
import AdminRequestTypes from './pages/AdminRequestTypes'
import AdminUsers from './pages/AdminUsers'
import AuditLog from './pages/AuditLog'
import Dashboard from './pages/Dashboard'
import Inbox from './pages/Inbox'
import Login from './pages/Login'
import NewRequest from './pages/NewRequest'
import NotFound from './pages/NotFound'
import RequestDetail from './pages/RequestDetail'
import Requests from './pages/Requests'
import RpaJobs from './pages/RpaJobs'
import RpaProcesses from './pages/RpaProcesses'

/** Route guard: no token -> /login. Missing permission -> dashboard. */
function Protected({ children, needs }) {
  const { isAuthenticated, loading, can } = useAuth()
  if (loading) return <div className="content"><div className="skeleton" style={{ height: 24, width: 200 }} /></div>
  if (!isAuthenticated) return <Navigate to="/login" replace />
  if (needs && !can(needs)) return <Navigate to="/dashboard" replace />
  return children
}

export default function App() {
  const { isAuthenticated } = useAuth()

  return (
    <Routes>
      <Route path="/login" element={isAuthenticated ? <Navigate to="/dashboard" replace /> : <Login />} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/requests" element={<Requests />} />
        <Route path="/requests/new" element={<NewRequest />} />
        <Route path="/requests/:id" element={<RequestDetail />} />
        <Route path="/inbox" element={<Protected needs="list-users"><Inbox /></Protected>} />
        <Route path="/rpa/processes" element={<Protected needs="manage-rpa"><RpaProcesses /></Protected>} />
        <Route path="/rpa/jobs" element={<Protected needs="browse-queue"><RpaJobs /></Protected>} />
        <Route path="/admin/users" element={<Protected needs="manage-users"><AdminUsers /></Protected>} />
        <Route path="/admin/request-types" element={<Protected needs="manage-workflows"><AdminRequestTypes /></Protected>} />
        <Route path="/audit" element={<Protected needs="view-audit"><AuditLog /></Protected>} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
