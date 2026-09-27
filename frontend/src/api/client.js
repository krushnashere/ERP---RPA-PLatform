/**
 * Axios instance + auth wiring.
 *
 *  - base URL comes from VITE_API_BASE_URL (defaults to the dev proxy path)
 *  - the JWT is attached to every request from localStorage
 *  - a 401 triggers a single silent refresh; if that fails we log out
 *  - API errors are normalised into a human-readable message for the UI
 */
import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const ACCESS_KEY = 'erp.access_token'
const REFRESH_KEY = 'erp.refresh_token'
const USER_KEY = 'erp.user'

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  get user() {
    const raw = localStorage.getItem(USER_KEY)
    try {
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  },
  save({ access_token, refresh_token, user }) {
    localStorage.setItem(ACCESS_KEY, access_token)
    localStorage.setItem(REFRESH_KEY, refresh_token)
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(USER_KEY)
  },
}

export const api = axios.create({ baseURL: BASE_URL, timeout: 20000 })

api.interceptors.request.use((config) => {
  const token = tokenStore.access
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

let refreshPromise = null

async function tryRefresh() {
  if (!tokenStore.refresh) return false
  if (!refreshPromise) {
    refreshPromise = axios
      .post(`${BASE_URL}/auth/refresh`, { refresh_token: tokenStore.refresh })
      .then((res) => {
        tokenStore.save(res.data)
        return true
      })
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshPromise = null), 0)
      })
  }
  return refreshPromise
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config || {}
    const status = error.response?.status

    if (status === 401 && !original._retried && tokenStore.refresh) {
      original._retried = true
      const ok = await tryRefresh()
      if (ok) {
        original.headers.Authorization = `Bearer ${tokenStore.access}`
        return api(original)
      }
      tokenStore.clear()
      window.dispatchEvent(new CustomEvent('erp:logout'))
    }
    return Promise.reject(error)
  },
)

/** Turn any axios error into one sentence the UI can show. */
export function errorMessage(error, fallback = 'Something went wrong') {
  const data = error?.response?.data
  if (!data) return error?.message || fallback
  if (Array.isArray(data.errors) && data.errors.length) {
    return data.errors.map((e) => `${e.field}: ${e.message}`).join(' | ')
  }
  return data.detail || fallback
}

export const http = {
  get: (url, config) => api.get(url, config).then((r) => r.data),
  post: (url, body, config) => api.post(url, body, config).then((r) => r.data),
  patch: (url, body, config) => api.patch(url, body, config).then((r) => r.data),
  delete: (url, config) => api.delete(url, config).then((r) => r.data),
  upload: (url, formData) =>
    api.post(url, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => r.data),
}

export default api
