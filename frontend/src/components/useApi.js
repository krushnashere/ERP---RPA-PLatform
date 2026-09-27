import { useCallback, useEffect, useRef, useState } from 'react'
import { errorMessage } from '../api/client'

/**
 * Tiny data-fetching hook: loading / error / refetch, with a request-id guard so
 * an out-of-order response can never overwrite newer data.
 */
export function useApi(fetcher, deps = [], { immediate = true } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(immediate)
  const [error, setError] = useState(null)
  const requestId = useRef(0)

  const run = useCallback(async () => {
    const id = ++requestId.current
    setLoading(true)
    setError(null)
    try {
      const result = await fetcher()
      if (id === requestId.current) setData(result)
      return result
    } catch (err) {
      if (id === requestId.current) setError(errorMessage(err))
      return null
    } finally {
      if (id === requestId.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    if (immediate) run()
  }, [run, immediate])

  return { data, loading, error, refetch: run, setData, setError }
}

/** Auto-refresh (used by the job queue board). */
export function usePolling(callback, intervalMs = 0) {
  const saved = useRef(callback)
  useEffect(() => {
    saved.current = callback
  }, [callback])
  useEffect(() => {
    if (!intervalMs) return undefined
    const timer = setInterval(() => saved.current(), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
}
