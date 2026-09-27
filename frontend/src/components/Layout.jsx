import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { ROLES, useAuth } from '../api/AuthContext'

const NAV = [
  { group: 'Workspace' },
  { to: '/dashboard', label: 'Dashboard', icon: '▦' },
  { to: '/inbox', label: 'My approvals', icon: '✓', needs: 'browse-queue' },
  { to: '/requests', label: 'Requests', icon: '≡' },
  { to: '/requests/new', label: 'New request', icon: '+' },
  { group: 'Automation' },
  { to: '/rpa/jobs', label: 'Job queue', icon: '⚙', needs: 'browse-queue' },
  { to: '/rpa/processes', label: 'RPA processes', icon: '⛓', needs: 'manage-rpa' },
  { group: 'Administration' },
  { to: '/admin/users', label: 'Users & roles', icon: '☰', needs: 'manage-users' },
  { to: '/admin/request-types', label: 'Workflows', icon: '⌘', needs: 'manage-workflows' },
  { to: '/audit', label: 'Audit trail', icon: '⧗', needs: 'view-audit' },
]

export default function Layout() {
  const { user, logout, can } = useAuth()
  const navigate = useNavigate()

  const signOut = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <strong>Acme ERP</strong>
          <span>Requests · Approvals · RPA</span>
        </div>
        <nav>
          {NAV.map((item, index) => {
            if (item.group) return <div className="group-label" key={`g${index}`}>{item.group}</div>
            if (item.needs && !can(item.needs)) return null
            return (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'active' : '')}>
                <span aria-hidden="true">{item.icon}</span>
                {item.label}
              </NavLink>
            )
          })}
        </nav>
        <div className="footer">
          <div style={{ color: '#e2e8f0', fontWeight: 600 }}>{user?.full_name}</div>
          <div style={{ color: '#94a3b8' }}>{user?.email}</div>
          <div style={{ marginTop: 6 }}>
            <span className={`badge ${user?.role}`}>{ROLES[user?.role] || user?.role}</span>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <strong style={{ fontSize: 14 }}>Enterprise ERP &amp; RPA Automation Platform</strong>
          <div className="spacer" />
          <div className="who">
            <strong>{user?.full_name}</strong>
            <span>{user?.employee_id} · {ROLES[user?.role] || user?.role}</span>
          </div>
          <button className="ghost sm" onClick={signOut}>Sign out</button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
