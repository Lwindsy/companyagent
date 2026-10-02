const DEFAULT_BACKENDS = {
  python: {
    id: 'python',
    label: 'Python',
    baseUrl: import.meta.env.VITE_PYTHON_API_URL || '/api/python',
    port: '8000'
  },
  java: {
    id: 'java',
    label: 'Java',
    baseUrl: import.meta.env.VITE_JAVA_API_URL || '/api/java',
    port: '8080'
  },
  dotnet: {
    id: 'dotnet',
    label: '.NET',
    baseUrl: import.meta.env.VITE_DOTNET_API_URL || '/api/dotnet',
    port: '8090'
  }
}

export const BACKEND_IDS = Object.keys(DEFAULT_BACKENDS)

export const BACKEND_OPTIONS = Object.values(DEFAULT_BACKENDS).map(({ id, label }) => ({ id, label }))

export function createInitialSettings() {
  const saved = readSettings()
  return {
    // Python/FastAPI is the first-run default; Java and .NET are optional
    // runtimes selectable from the admin workspace.
    backend: DEFAULT_BACKENDS[saved.backend] ? saved.backend : 'python',
    userId: saved.userId || 'u1001',
    conversationId: '',
    endpoints: Object.fromEntries(BACKEND_IDS.map((id) => [id, saved.endpoints?.[id] || DEFAULT_BACKENDS[id].baseUrl]))
  }
}

export function saveSettings(settings) {
  localStorage.setItem('console.frontend.settings', JSON.stringify({
    ...settings,
    conversationId: ''
  }))
}

export function backendMeta(type, settings) {
  const meta = DEFAULT_BACKENDS[type] || DEFAULT_BACKENDS.java
  return {
    ...meta,
    baseUrl: normalizeBaseUrl(settings.endpoints[type] || meta.baseUrl)
  }
}

export async function requestHealth(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/health')
}

export async function requestMonitor(type, settings, token) {
  return requestJson(backendMeta(type, settings).baseUrl, '/monitor', withAdminAuth(token))
}

export async function requestKnowledgeStats(type, settings, token) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/stats', withAdminAuth(token))
}

export async function requestSearch(type, settings, query, topK = 5, token) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(backendMeta(type, settings).baseUrl, `/search?${params}`, withAdminAuth(token, { method: 'POST' }))
}

export async function requestChat(type, settings, message) {
  const meta = backendMeta(type, settings)
  const payload = buildChatPayload(type, settings, message)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return normalizeChatResponse(type, raw)
}

export async function addKnowledge(type, settings, documents, token) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/add', withAdminAuth(token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  }))
}

export async function uploadKnowledge(type, settings, file, token) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/upload', withAdminAuth(token, {
    method: 'POST',
    body: form
  }))
}

export async function requestAdminLogin(type, settings, username, password) {
  return requestJson(backendMeta(type, settings).baseUrl, '/admin/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  })
}

export async function requestAdminOverview(type, settings, token) {
  return requestJson(backendMeta(type, settings).baseUrl, '/admin/overview', withAdminAuth(token))
}

function buildChatPayload(type, settings, message) {
  if (type === 'python') {
    return {
      message,
      user_id: settings.userId || 'anonymous',
      conv_id: settings.conversationId || undefined
    }
  }
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conversation_id: settings.conversationId || undefined
  }
}

function normalizeChatResponse(type, raw) {
  return {
    backend: type,
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    response: raw.response || '',
    intent: raw.intent || 'other',
    agentType: raw.agent_type || raw.agentType || '',
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used ?? raw.knowledgeUsed),
    verified: raw.verified,
    grounded: raw.grounded,
    raw
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  const response = await fetch(url, options)
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data)
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function withAdminAuth(token, options = {}) {
  return {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {})
    }
  }
}

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem('console.frontend.settings') || '{}')
  } catch {
    return {}
  }
}
