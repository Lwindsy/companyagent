import type {
  AdminSession,
  BackendId,
  BackendSettings,
  ChatResponse,
  JsonRecord,
  KnowledgeDocument,
  RequestContext,
  SearchResult,
} from '../types'
import { readStorage, record, textValue, writeStorage } from '../lib/storage'

export const BACKENDS = [
  {
    id: 'python',
    label: 'Python',
    description: 'FastAPI',
    defaultUrl: import.meta.env.VITE_PYTHON_API_URL || '/api/python',
  },
  {
    id: 'java',
    label: 'Java',
    description: 'Spring Boot',
    defaultUrl: import.meta.env.VITE_JAVA_API_URL || '/api/java',
  },
  {
    id: 'dotnet',
    label: '.NET',
    description: 'ASP.NET Core',
    defaultUrl: import.meta.env.VITE_DOTNET_API_URL || '/api/dotnet',
  },
] as const

export const isBackendId = (value: unknown): value is BackendId => BACKENDS.some((item) => item.id === value)
export const normalizeBaseUrl = (value: string) => value.trim().replace(/\/+$/, '')

export function validEndpoint(value: string): boolean {
  const url = normalizeBaseUrl(value)
  if (/^\/(?!\/)[^?#\\\s]+$/.test(url)) return true
  try {
    const parsed = new URL(url)
    return (
      ['http:', 'https:'].includes(parsed.protocol) &&
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash
    )
  } catch {
    return false
  }
}

export function createInitialSettings(): BackendSettings {
  const saved = record(readStorage('localStorage', 'console.frontend.settings'))
  const endpoints = record(saved.endpoints)
  const endpoint = (id: BackendId) => {
    const fallback = BACKENDS.find((item) => item.id === id)!.defaultUrl
    return typeof endpoints[id] === 'string' && validEndpoint(endpoints[id])
      ? normalizeBaseUrl(endpoints[id])
      : fallback
  }
  return {
    backend: isBackendId(saved.backend) ? saved.backend : 'python',
    userId:
      typeof saved.userId === 'string' && saved.userId.trim() && saved.userId !== 'u1001'
        ? saved.userId
        : `web-${crypto.randomUUID().slice(0, 8)}`,
    endpoints: { python: endpoint('python'), java: endpoint('java'), dotnet: endpoint('dotnet') },
  }
}

export function saveSettings(settings: BackendSettings) {
  return writeStorage('localStorage', 'console.frontend.settings', settings)
}

export function requestContext(settings: BackendSettings): RequestContext {
  return {
    backend: settings.backend,
    baseUrl: normalizeBaseUrl(settings.endpoints[settings.backend]),
    userId: settings.userId,
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status = 0,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function requestJson(
  baseUrl: string,
  path: string,
  options: RequestInit = {},
  timeout = 30_000,
): Promise<JsonRecord> {
  const timer = new AbortController()
  const timeoutId = window.setTimeout(() => timer.abort(), timeout)
  const signal = options.signal ? AbortSignal.any([options.signal, timer.signal]) : timer.signal
  try {
    const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, { ...options, signal })
    const body = await response.text()
    let data: unknown
    try {
      data = body ? JSON.parse(body) : {}
    } catch {
      throw new ApiError(`The service returned an invalid response (${response.status}).`, response.status)
    }
    if (!response.ok) {
      const detail = record(data).detail
      throw new ApiError(
        typeof detail === 'string' ? detail : `Request failed (${response.status}).`,
        response.status,
      )
    }
    if (data === null || typeof data !== 'object' || Array.isArray(data))
      throw new ApiError('The service returned an unexpected response.')
    return record(data)
  } catch (error) {
    if (timer.signal.aborted && !options.signal?.aborted)
      throw new ApiError('The service took too long to respond. Please try again.')
    throw error
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function authenticated(token: string, options: RequestInit = {}): RequestInit {
  const headers = new Headers(options.headers)
  headers.set('Authorization', `Bearer ${token}`)
  return { ...options, headers }
}

const jsonBody = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export async function requestChat(
  context: RequestContext,
  message: string,
  conversationId: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const payload = {
    message,
    user_id: context.userId || 'anonymous',
    [context.backend === 'python' ? 'conv_id' : 'conversation_id']: conversationId || undefined,
  }
  const raw = await requestJson(context.baseUrl, '/chat', { ...jsonBody(payload), signal }, 120_000)
  const response = textValue(raw.response)
  if (!response.trim()) throw new ApiError('The service returned an empty reply. Please try again.')
  return {
    conversationId: textValue(raw.conversation_id || raw.conversationId || raw.conv_id),
    response,
    agentType: textValue(raw.agent_type || raw.agentType),
    escalated: raw.escalated === true,
    knowledgeUsed: (raw.knowledge_used ?? raw.knowledgeUsed) === true,
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs) || 0,
  }
}

export async function requestAdminLogin(
  baseUrl: string,
  username: string,
  password: string,
  signal?: AbortSignal,
): Promise<AdminSession> {
  const result = await requestJson(baseUrl, '/admin/login', { ...jsonBody({ username, password }), signal })
  if (typeof result.access_token !== 'string' || !result.access_token)
    throw new ApiError('The service did not return a valid administrator session.')
  return {
    token: result.access_token,
    username: textValue(result.username, username),
    baseUrl: normalizeBaseUrl(baseUrl),
  }
}

export const requestHealth = (baseUrl: string, signal?: AbortSignal) =>
  requestJson(baseUrl, '/health', { signal })
export const requestAdminOverview = (baseUrl: string, token: string, signal?: AbortSignal) =>
  requestJson(baseUrl, '/admin/overview', authenticated(token, { signal }))
export const requestKnowledgeStats = (baseUrl: string, token: string, signal?: AbortSignal) =>
  requestJson(baseUrl, '/knowledge/stats', authenticated(token, { signal }))
export const requestMonitor = (baseUrl: string, token: string, signal?: AbortSignal) =>
  requestJson(baseUrl, '/monitor', authenticated(token, { signal }))

export async function requestSearch(
  baseUrl: string,
  token: string,
  query: string,
  signal?: AbortSignal,
): Promise<SearchResult[]> {
  const params = new URLSearchParams({ query, top_k: '5' })
  const raw = await requestJson(
    baseUrl,
    `/search?${params}`,
    authenticated(token, { method: 'POST', signal }),
  )
  return (Array.isArray(raw.results) ? raw.results : []).map((value, index) => {
    const item = record(value)
    return {
      id: textValue(item.id, String(index)),
      title: textValue(item.title, 'Untitled document'),
      content: textValue(item.content),
      score: typeof item.score === 'number' ? item.score : null,
    }
  })
}

export const addKnowledge = (
  baseUrl: string,
  token: string,
  documents: KnowledgeDocument[],
  signal?: AbortSignal,
) => requestJson(baseUrl, '/knowledge/add', authenticated(token, { ...jsonBody({ documents }), signal }))
export function uploadKnowledge(baseUrl: string, token: string, file: File, signal?: AbortSignal) {
  const body = new FormData()
  body.append('file', file)
  return requestJson(
    baseUrl,
    '/knowledge/upload',
    authenticated(token, { method: 'POST', body, signal }),
    120_000,
  )
}
