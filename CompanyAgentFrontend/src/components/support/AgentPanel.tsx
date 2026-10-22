import { useEffect, useRef, useState } from 'react'
import { agentRequest, parseRun, type AgentEvent, type AgentRun, type WriteGrant } from '../../api/agent'
import { actionsFrom, type DemoSession } from '../../api/business'
import { readStorage, record, writeStorage } from '../../lib/storage'
import { BusinessActions } from './BusinessActions'

// This optional panel owns a separate server run. The existing fixed workflow stays
// available; its state and retry IDs are not reused as agent execution state.
export function AgentPanel({ baseUrl, session }: { baseUrl: string; session: DemoSession }) {
  const key = `support.agent:${baseUrl}:${session.convId}`
  const [open, setOpen] = useState(false)
  const [run, setRun] = useState<AgentRun>()
  const [events, setEvents] = useState<AgentEvent[]>([])
  const [draft, setDraft] = useState('Investigate the service login problem. Do not change my account.')
  const [payment, setPayment] = useState('ch_2')
  const [permission, setPermission] = useState('read')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [savedId, setSavedId] = useState(() => String(record(readStorage('sessionStorage', key)).run || ''))
  // A pending request envelope is persisted BEFORE transmission. A lost creation
  // reply can then be retried using the same body/ID after reload, not re-executed.
  const pending = useRef<Record<string, unknown> | undefined>(undefined)
  const [retryPending, setRetryPending] = useState(false)
  const current = useRef(run)
  current.current = run
  const terminal = new Set(['completed', 'failed', 'cancelled', 'budget_exhausted'])

  useEffect(() => {
    const restored = record(readStorage('sessionStorage', key))
    if (restored.pending && typeof restored.pending === 'object') {
      pending.current = restored.pending as Record<string, unknown>
      setRetryPending(true)
    }
  }, [key])

  useEffect(() => {
    if (!open || !savedId) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let cursor = 0
    setEvents([])
    async function poll() {
      try {
        const value = parseRun(await agentRequest(baseUrl, session, `/${savedId}`, controller.signal))
        const response = await agentRequest(
          baseUrl,
          session,
          `/${savedId}/events?after=${cursor}`,
          controller.signal,
        )
        if (controller.signal.aborted) return
        setRun(value)
        if (Array.isArray(response.items)) {
          const batch = response.items.filter(
            (e): e is AgentEvent => !!e && typeof e.seq === 'number' && typeof e.kind === 'string',
          )
          if (batch.length) {
            cursor = batch.at(-1)!.seq
            setEvents((before) => [...before, ...batch].slice(-100))
          }
        }
        setError('')
        // Sequence polling uses the same durable event cursor as SSE, and supports
        // bearer headers without putting credentials in EventSource query strings.
        if (!terminal.has(value.status)) timer = setTimeout(poll, 700)
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Could not refresh agent status')
          timer = setTimeout(poll, 1500)
        }
      }
    }
    void poll()
    return () => {
      controller.abort()
      if (timer) clearTimeout(timer)
    }
    // Changes of identity/base URL unmount this panel in BusinessDemo. The effect
    // also lists them explicitly to invalidate late results during a session change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, savedId, baseUrl, session.token, session.convId])

  async function send() {
    const controller = new AbortController()
    setBusy(true)
    setError('')
    try {
      const grant: WriteGrant | undefined =
        permission === 'refund'
          ? { kind: 'refund', charge_id: payment }
          : permission === 'cancel' || permission === 'restore'
            ? { kind: 'renewal', cancel_at_period_end: permission === 'cancel' }
            : undefined
      const waiting = current.current?.status === 'waiting_user'
      const envelope = pending.current || {
        path: waiting ? `/${current.current!.id}/input` : '',
        body: {
          request_id: crypto.randomUUID(),
          message: draft.trim(),
          ...(grant ? { write_grant: grant } : {}),
          ...(waiting ? { version: current.current!.version } : { conv_id: session.convId }),
        },
      }
      pending.current = envelope
      if (!writeStorage('sessionStorage', key, { run: savedId, pending: envelope }))
        throw new Error('Cannot save a safe retry. Enable tab storage before starting an agent run.')
      const value = parseRun(
        await agentRequest(baseUrl, session, String(envelope.path), controller.signal, envelope.body),
      )
      pending.current = undefined
      setRetryPending(false)
      writeStorage('sessionStorage', key, { run: value.id })
      setSavedId(value.id)
      setRun(value)
      setDraft('')
      setPermission('read')
    } catch (cause) {
      setRetryPending(!!pending.current)
      setError(cause instanceof Error ? cause.message : 'Run could not be started')
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (!run) return
    try {
      setRun(
        parseRun(await agentRequest(baseUrl, session, `/${run.id}/cancel`, new AbortController().signal, {})),
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Cancellation could not be confirmed')
    }
  }

  const canSend = !run || terminal.has(run.status) || run.status === 'waiting_user'
  return (
    <section className="business-message" aria-label="Recoverable account agent">
      <button className="button secondary" type="button" onClick={() => setOpen(!open)}>
        {open ? 'Hide account agent' : 'Open account agent'}
      </button>
      {open && (
        <>
          <h2>Account agent</h2>
          <p>Investigate with tools. Payments and diagnostics are simulated.</p>
          {error && <p role="alert">{error}</p>}
          {run && (
            <>
              <p role="status">Agent status: {run.status}</p>
              <p>Estimated used or reserved: ${run.used_or_reserved_usd.toFixed(6)}</p>
              <ol aria-label="Agent steps">
                {events.map((event) => (
                  <li key={event.seq}>
                    {event.kind.replaceAll('_', ' ')} {event.data.name || ''}
                    {event.data.elapsed_ms !== undefined && ` · ${event.data.elapsed_ms} ms`}
                    {event.data.input_tokens !== undefined &&
                      ` · ${event.data.input_tokens} input / ${event.data.output_tokens || 0} output tokens`}
                    {event.data.status && ` · ${event.data.status}`}
                  </li>
                ))}
              </ol>
              {run.result.response && <p>{run.result.response}</p>}
              {/* Evidence identifiers are plain text, not model-supplied URLs. The
                  backend accepts only IDs present in this run's observations. */}
              {run.result.evidence_ids && run.result.evidence_ids.length > 0 && (
                <p>Evidence: {run.result.evidence_ids.join(', ')}</p>
              )}
              {run.result.actions && (
                <BusinessActions actions={actionsFrom(run.result.actions)} busy={false} demo />
              )}
              {!terminal.has(run.status) && (
                <button className="button secondary" type="button" onClick={() => void cancel()}>
                  Cancel agent run
                </button>
              )}
            </>
          )}
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void send()
            }}
          >
            <label htmlFor="agent-goal">Agent goal or requested clarification</label>
            <textarea
              id="agent-goal"
              value={draft}
              maxLength={4000}
              disabled={busy || !canSend || retryPending}
              onChange={(event) => setDraft(event.target.value)}
            />
            <label>
              Agent action permission
              <select
                value={permission}
                disabled={busy || !canSend || retryPending}
                onChange={(event) => setPermission(event.target.value)}
              >
                <option value="read">Research only — no account changes</option>
                <option value="refund">Allow one full-payment refund request</option>
                <option value="cancel">Allow stopping renewal</option>
                <option value="restore">Allow restoring renewal</option>
              </select>
            </label>
            {permission === 'refund' && (
              <label>
                Payment ID
                <input value={payment} maxLength={80} onChange={(event) => setPayment(event.target.value)} />
              </label>
            )}
            <button
              className="button primary"
              disabled={busy || (!retryPending && (!canSend || !draft.trim()))}
            >
              {retryPending
                ? 'Retry the same agent request'
                : run?.status === 'waiting_user'
                  ? 'Continue agent'
                  : 'Start agent investigation'}
            </button>
          </form>
        </>
      )}
    </section>
  )
}
