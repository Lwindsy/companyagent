import { useEffect, useRef, useState } from 'react'
import {
  addKnowledge,
  ApiError,
  requestAdminOverview,
  requestHealth,
  requestKnowledgeStats,
  requestMonitor,
  requestSearch,
  uploadKnowledge,
} from '../api/backend'
import type { AdminSession, JsonRecord, KnowledgeDocument, SearchResult } from '../types'

export interface Dashboard {
  health: string
  chunks: number | null
  alerts: unknown[] | null
  monitor: JsonRecord | null
  errors: string[]
  checkedAt: number
}
const emptyDashboard = (): Dashboard => ({
  health: 'Not checked',
  chunks: null,
  alerts: null,
  monitor: null,
  errors: [],
  checkedAt: 0,
})
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : 'Could not complete this request.'

export function useAdminData(session: AdminSession | undefined, onExpired: () => void) {
  const [dashboard, setDashboard] = useState<Dashboard>(emptyDashboard)
  const [refreshing, setRefreshing] = useState(false)
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [results, setResults] = useState<SearchResult[] | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [operation, setOperation] = useState<JsonRecord | null>(null)
  const scope = `${session?.baseUrl || ''}:${session?.token || ''}`
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const expiredRef = useRef(onExpired)
  expiredRef.current = onExpired
  const controllers = useRef(new Map<string, AbortController>())

  function begin(key: string) {
    controllers.current.get(key)?.abort()
    const controller = new AbortController()
    controllers.current.set(key, controller)
    return controller
  }
  function current(controller: AbortController) {
    return !controller.signal.aborted && scopeRef.current === scope
  }
  function failure(reason: unknown) {
    if (reason instanceof ApiError && reason.status === 401) expiredRef.current()
    else setError(errorText(reason))
  }

  async function refresh() {
    if (!session) return
    const controller = begin('dashboard')
    setRefreshing(true)
    const outcomes = await Promise.allSettled([
      requestHealth(session.baseUrl, controller.signal),
      requestKnowledgeStats(session.baseUrl, session.token, controller.signal),
      requestMonitor(session.baseUrl, session.token, controller.signal),
      requestAdminOverview(session.baseUrl, session.token, controller.signal),
    ])
    if (!current(controller)) return
    const expired = outcomes.some(
      (item) => item.status === 'rejected' && item.reason instanceof ApiError && item.reason.status === 401,
    )
    if (expired) {
      setRefreshing(false)
      expiredRef.current()
      return
    }
    const [health, stats, monitor, overview] = outcomes
    const statsData = stats.status === 'fulfilled' ? stats.value : {}
    const overviewData = overview.status === 'fulfilled' ? overview.value : {}
    const chunks = statsData.total_chunks ?? statsData.totalChunks ?? overviewData.knowledge_chunks
    const alerts = overviewData.alerts
    const names = ['Health', 'Knowledge', 'Monitoring', 'Overview']
    setDashboard({
      health:
        health.status === 'fulfilled' && typeof health.value.status === 'string'
          ? health.value.status
          : 'Unavailable',
      chunks: typeof chunks === 'number' ? chunks : null,
      alerts: Array.isArray(alerts) ? alerts : null,
      monitor: monitor.status === 'fulfilled' ? monitor.value : null,
      errors: outcomes.flatMap((item, index) =>
        item.status === 'rejected' ? [`${names[index]}: ${errorText(item.reason)}`] : [],
      ),
      checkedAt: Date.now(),
    })
    setRefreshing(false)
  }

  useEffect(() => {
    setDashboard(emptyDashboard())
    setResults(null)
    setError('')
    setNotice('')
    setOperation(null)
    setSearching(false)
    setSaving(false)
    setRefreshing(false)
    void refresh()
    const requests = controllers.current
    return () => {
      requests.forEach((controller) => controller.abort())
      requests.clear()
    }
  }, [scope])

  async function search(query: string) {
    if (!session || !query.trim()) return
    const controller = begin('search')
    setSearching(true)
    setError('')
    setResults(null)
    try {
      const data = await requestSearch(session.baseUrl, session.token, query.trim(), controller.signal)
      if (current(controller)) setResults(data)
    } catch (reason) {
      if (current(controller)) failure(reason)
    } finally {
      if (current(controller)) setSearching(false)
    }
  }

  async function mutate(input: KnowledgeDocument | File): Promise<boolean> {
    if (!session || saving) return false
    const controller = begin('mutation')
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const data =
        input instanceof File
          ? await uploadKnowledge(session.baseUrl, session.token, input, controller.signal)
          : await addKnowledge(session.baseUrl, session.token, [input], controller.signal)
      if (!current(controller)) return false
      setOperation(data)
      setNotice(
        input instanceof File
          ? `“${input.name}” was uploaded successfully.`
          : `“${input.title}” was added to your knowledge base.`,
      )
      void refresh()
      return true
    } catch (reason) {
      if (current(controller)) failure(reason)
      return false
    } finally {
      if (current(controller)) setSaving(false)
    }
  }

  return {
    dashboard,
    refreshing,
    searching,
    saving,
    results,
    error,
    notice,
    operation,
    refresh,
    search,
    mutate,
  }
}
