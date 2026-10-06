import { useState } from 'react'
import { BACKENDS, normalizeBaseUrl, requestAdminLogin } from '../api/backend'
import { readStorage, record, removeStorage, textValue, writeStorage } from '../lib/storage'
import type { AdminSession, BackendSettings } from '../types'

const SESSION_KEY = 'support.admin.sessions.v2'
export const sessionScope = (backend: string, baseUrl: string) => `${backend}:${normalizeBaseUrl(baseUrl)}`

export function readAdminSessions(settings: BackendSettings): Record<string, AdminSession> {
  const sessions: Record<string, AdminSession> = {}
  const saved = record(readStorage('sessionStorage', SESSION_KEY))
  for (const [key, value] of Object.entries(saved)) {
    const item = record(value)
    if (typeof item.token === 'string' && typeof item.baseUrl === 'string')
      sessions[key] = {
        token: item.token,
        username: textValue(item.username, 'Administrator'),
        baseUrl: item.baseUrl,
      }
  }
  // Bind old backend-only tokens to the exact configured endpoint once during migration.
  try {
    for (const backend of BACKENDS) {
      const token =
        sessionStorage.getItem(`support.admin.${backend.id}.token`) ||
        (backend.id === settings.backend ? sessionStorage.getItem('support.admin.token') : '')
      if (token) {
        const baseUrl = normalizeBaseUrl(settings.endpoints[backend.id])
        const key = sessionScope(backend.id, baseUrl)
        sessions[key] ??= {
          token,
          baseUrl,
          username:
            sessionStorage.getItem(`support.admin.${backend.id}.username`) ||
            sessionStorage.getItem('support.admin.username') ||
            'Administrator',
        }
      }
    }
  } catch {
    /* In-memory sessions still work when browser storage is unavailable. */
  }
  return sessions
}

export function useAdminSession(settings: BackendSettings) {
  const [sessions, setSessions] = useState(() => readAdminSessions(settings))
  const scope = sessionScope(settings.backend, settings.endpoints[settings.backend])
  function persist(next: Record<string, AdminSession>) {
    setSessions(next)
    writeStorage('sessionStorage', SESSION_KEY, next)
    for (const backend of BACKENDS) {
      removeStorage('sessionStorage', `support.admin.${backend.id}.token`)
      removeStorage('sessionStorage', `support.admin.${backend.id}.username`)
    }
    removeStorage('sessionStorage', 'support.admin.token')
    removeStorage('sessionStorage', 'support.admin.username')
  }
  async function login(username: string, password: string, signal: AbortSignal) {
    const session = await requestAdminLogin(
      settings.endpoints[settings.backend],
      username.trim(),
      password,
      signal,
    )
    if (!signal.aborted) persist({ ...sessions, [scope]: session })
  }
  function expire() {
    const next = { ...sessions }
    delete next[scope]
    persist(next)
  }
  return { session: sessions[scope], login, expire, logout: () => persist({}) }
}
