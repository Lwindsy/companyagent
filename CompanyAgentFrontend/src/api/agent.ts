import { requestJson } from './backend'
import type { DemoSession } from './business'

// These transport types describe public data, never native provider continuations.
// A version is a server fencing value; a price is estimated USD, not an invoice.
export interface AgentRun {
  id: string
  status: string
  version: number
  used_or_reserved_usd: number
  result: { response?: string; error?: string; actions?: unknown[]; evidence_ids?: string[] }
}
export interface AgentEvent {
  seq: number
  kind: string
  data: {
    name?: string
    call_id?: string
    cost_micro?: number
    elapsed_ms?: number
    input_tokens?: number
    output_tokens?: number
    status?: string
  }
}
export type WriteGrant =
  { kind: 'refund'; charge_id: string } | { kind: 'renewal'; cancel_at_period_end: boolean }

export async function agentRequest(
  baseUrl: string,
  session: DemoSession,
  path: string,
  signal: AbortSignal,
  body?: unknown,
): Promise<Record<string, unknown>> {
  // Authentication is sent in a header, never in a URL/event stream query string.
  // Each caller owns cancellation; stopping a fetch does not cancel server work.
  return requestJson(baseUrl, `/customer/agent/runs${path}`, {
    signal,
    headers: {
      Authorization: `Bearer ${session.token}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
  })
}

export function parseRun(value: Record<string, unknown>): AgentRun {
  // Reject incomplete status data instead of fabricating a successful operation.
  if (typeof value.id !== 'string' || typeof value.status !== 'string' || typeof value.version !== 'number')
    throw new Error('Invalid agent status response')
  return {
    id: value.id,
    status: value.status,
    version: value.version,
    used_or_reserved_usd: Number(value.used_or_reserved_usd || 0),
    result: value.result && typeof value.result === 'object' ? (value.result as AgentRun['result']) : {},
  }
}
