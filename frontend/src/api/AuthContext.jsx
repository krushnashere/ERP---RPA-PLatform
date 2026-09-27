/**
 * Auth context: who am I, what can I do.
 *
 * `can()` is the single place where the UI mirrors the backend RBAC rules, so a
 * button is hidden for a role that would get a 403 anyway.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { http, tokenStore } from './client'

const AuthContext = createContext(null)

export const ROLES = {
  employee: 'Employee',
  manager: 'Manager',
  admin: 'Admin',
  super_admin: 'Super admin',
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => tokenStore.user)
  const [loading, setLoading] = useState(Boolean(tokenStore.access))

  useEffect(() => {
    if (!tokenStore.access) {
      setLoading(false)
      return
    }
    http
      .get('/auth/me')
      .then(setUser)
      .catch(() => {
        tokenStore.clear()
        setUser(null)
      })
      .finally(() => setLoading(false))

    const onLogout = () => setUser(null)
    window.addEventListener('erp:logout', onLogout)
    return () => window.removeEventListener('erp:logout', onLogout)
  }, [])

  const login = useCallback(async (email, password) => {
    const data = await http.post('/auth/login', { email, password })
    tokenStore.save(data)
    setUser(data.user)
    return data.user
  }, [])

  const logout = useCallback(() => {
    tokenStore.clear()
    setUser(null)
  }, [])

  const value = useMemo(() => {
    const role = user?.role
    return {
      user,
      loading,
      isAuthenticated: Boolean(user),
      login,
      logout,
      role,
      isEmployee: role === 'employee',
      isManager: role === 'manager',
      isAdmin: role === 'admin' || role === 'super_admin',
      isSuperAdmin: role === 'super_admin',
      /** UI-level mirror of the backend RBAC matrix. */
      can: (action) => {
        switch (action) {
          case 'manage-users':
          case 'manage-workflows':
          case 'manage-rpa':
          case 'view-audit':
            return role === 'admin' || role === 'super_admin'
          case 'browse-queue':
            return role !== 'employee'
          case 'list-users':
            return role !== 'employee'
          case 'grant-admin':
          case 'soft-delete-open-request':
            return role === 'super_admin'
          default:
            return Boolean(role)
        }
      },
    }
  }, [user, loading, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
