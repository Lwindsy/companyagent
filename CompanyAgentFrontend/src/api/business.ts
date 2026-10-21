import { useEffect, useState } from 'react'
import { ApiError, normalizeBaseUrl, requestJson } from './backend'
import { record, textValue } from '../lib/storage'

// Interfaces describe the data our TypeScript code expects; they do not validate
// network JSON at runtime. actionsFrom below checks the minimum usable shape.
// status/reason_code come from the server, and amount_cents is an integer euro amount
// measured in cents (or null when this action does not have a monetary amount).
export interface BusinessAction {
  id: string
  tool: string
  status: string
  reason_code: string
  summary: string
  amount_cents: number | null
}
export interface DemoSession {
  // This is a customer bearer credential, not an administrator token. convId is
  // server-assigned; expiresAt uses browser milliseconds, unlike server epoch seconds.
  token: string
  convId: string
  name: string
  expiresAt: number
}
export interface Persona {
  // Fictional account metadata plus a suggested first message; choosing it creates
  // an isolated demo sandbox, not real-world proof of the customer's identity.
  id: string
  name: string
  tagline: string
  starter: string
}

// Serialize a plain value as JSON and tell the HTTP server which body format to parse.
// unknown means a caller must inspect a value before treating it as a specific type.
const post = (body: unknown) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})
const auth = (token: string, options: RequestInit = {}) => {
  // Copy existing headers so adding credentials preserves Content-Type and signals.
  // Authorization travels in a header rather than a URL or a caller-chosen user_id.
  const headers = new Headers(options.headers)
  headers.set('Authorization', `Bearer ${token}`)
  return { ...options, headers }
}

export function actionsFrom(raw: unknown): BusinessAction[] {
  // A server response might be missing or malformed. flatMap can return [] to drop
  // unusable entries, or [action] to keep one. Never invent a completed action as fallback.
  if (!Array.isArray(raw)) return []
  return raw.flatMap((value) => {
    const row = record(value)
    if (typeof row.id !== 'string' || typeof row.status !== 'string') return []
    return [
      {
        id: row.id,
        tool: textValue(row.tool),
        status: row.status,
        reason_code: textValue(row.reason_code),
        summary: textValue(row.summary),
        amount_cents: typeof row.amount_cents === 'number' ? row.amount_cents : null,
      },
    ]
  })
}

export function useBusinessCapabilities(baseUrl: string, python: boolean) {
  // A React hook keeps component state and reruns its effect when dependencies change.
  // Missing capabilities mean unavailable; Java/.NET are not probed for Python-only APIs.
  const [capabilities, setCapabilities] = useState<string[]>([])
  useEffect(() => {
    // Abort and clear the old probe when the endpoint changes or the component leaves
    // the screen, so a late response cannot expose controls for the wrong backend.
    const controller = new AbortController()
    setCapabilities([])
    if (python) {
      void requestJson(baseUrl, '/health', { signal: controller.signal })
        .then((raw) => {
          if (!controller.signal.aborted)
            setCapabilities(
              Array.isArray(raw.capabilities)
                ? raw.capabilities.filter((x): x is string => typeof x === 'string')
                : [],
            )
        })
        .catch(() => {}) // General support remains usable if capability probing fails.
    }
    return () => controller.abort()
  }, [baseUrl, python])
  return capabilities
}

export async function personas(baseUrl: string, signal: AbortSignal): Promise<Persona[]> {
  // await suspends this function until HTTP completes without blocking the UI thread.
  // Treat items as untrusted JSON and keep only entries with an ID and a name.
  const raw = await requestJson(baseUrl, '/customer/personas', { signal })
  return Array.isArray(raw.items)
    ? raw.items.flatMap((value) => {
        const row = record(value)
        return typeof row.id === 'string' && typeof row.name === 'string'
          ? [{ id: row.id, name: row.name, tagline: textValue(row.tagline), starter: textValue(row.starter) }]
          : []
      })
    : []
}

export async function createDemo(
  baseUrl: string,
  personaId: string,
  signal: AbortSignal,
): Promise<DemoSession> {
  // The server creates identity and conversation binding. The browser stores the
  // returned token, never manufactures it. Convert the TTL from seconds to milliseconds.
  const raw = await requestJson(baseUrl, '/customer/sessions', { ...post({ persona_id: personaId }), signal })
  if (typeof raw.token !== 'string' || typeof raw.conv_id !== 'string')
    throw new ApiError('The service did not return a demo session.')
  return {
    token: raw.token,
    convId: raw.conv_id,
    name: textValue(record(raw.customer).name),
    expiresAt: Date.now() + Number(raw.expires_in) * 1000,
  }
}

export async function businessChat(
  baseUrl: string,
  session: DemoSession,
  message: string,
  requestId: string,
  signal: AbortSignal,
) {
  // A new customer message gets a new requestId; a retry MUST keep the same one.
  // signal stops browser waiting, not a database write already running on the server.
  // The 35-second request budget allows time beyond the server's model extraction deadline.
  const raw = await requestJson(
    baseUrl,
    '/customer/chat',
    auth(session.token, {
      ...post({ message, conv_id: session.convId, request_id: requestId }),
      signal,
    }),
    35_000,
  )
  if (typeof raw.response !== 'string' || !raw.response.trim())
    throw new ApiError('The service returned an empty reply.')
  return {
    response: raw.response,
    actions: actionsFrom(raw.actions),
    handoff: textValue(record(raw.handoff).id),
  }
}

export async function getActions(
  baseUrl: string,
  token: string,
  convId: string | undefined,
  signal: AbortSignal,
) {
  // A conversation selects the customer URL; absence selects the staff URL. The
  // server must still authenticate token appropriately; this URL choice grants no rights.
  // encodeURIComponent keeps characters such as & inside the value, not query syntax.
  const path = convId ? `/customer/actions?conv_id=${encodeURIComponent(convId)}` : '/admin/actions'
  const raw = await requestJson(normalizeBaseUrl(baseUrl), path, auth(token, { signal }))
  return actionsFrom(raw.items)
}

export async function decideAction(
  baseUrl: string,
  token: string,
  id: string,
  decision: 'approve' | 'reject',
  note: string,
  demo: boolean,
  signal: AbortSignal,
) {
  // demo chooses an explicitly simulated customer endpoint; false chooses staff.
  // Authorization remains server-enforced. Use the returned action as the new state
  // rather than optimistically labelling a click as a completed refund.
  const path = `${demo ? '/customer/demo' : '/admin'}/actions/${encodeURIComponent(id)}/decide`
  const raw = await requestJson(baseUrl, path, auth(token, { ...post({ decision, note }), signal }))
  const action = actionsFrom([raw.action])[0]
  if (!action) throw new ApiError('The service did not return the updated operation.')
  return action
}
