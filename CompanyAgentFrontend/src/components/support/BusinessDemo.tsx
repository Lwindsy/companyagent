import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../../api/backend'
import {
  actionsFrom,
  businessChat,
  createDemo,
  decideAction,
  getActions,
  personas,
  useBusinessCapabilities,
  type BusinessAction,
  type DemoSession,
  type Persona,
} from '../../api/business'
import { readStorage, record, removeStorage, writeStorage } from '../../lib/storage'
import type { RequestContext } from '../../types'
import { BusinessActions } from './BusinessActions'

interface DemoMessage {
  // The customer's message ID is also its stable HTTP retry ID. failed means the
  // browser lacks a confirmed reply; it does not prove the server performed no effect.
  id: string
  role: 'user' | 'assistant'
  content: string
  failed?: boolean
}
interface DemoState {
  // Keep account conversation state separate from the existing general-support hook.
  // actions are server facts; handoff is a recorded ticket ID, not a live staff connection.
  session?: DemoSession
  messages: DemoMessage[]
  actions: BusinessAction[]
  handoff: string
  expired: boolean
}
// A function creates fresh arrays each time; sharing one mutable object across
// resets could accidentally preserve another demo's messages or operation cards.
const empty = (): DemoState => ({ messages: [], actions: [], handoff: '', expired: false })

function restore(key: string): DemoState {
  // sessionStorage survives reloads in this browser tab. Stored JSON can be stale or
  // malformed, so check primitive fields before restoring credentials and messages.
  // Restored token expiry is a UI hint only: the server rechecks every authenticated request.
  const saved = record(readStorage('sessionStorage', key))
  const session = record(saved.session)
  if (
    typeof session.token !== 'string' ||
    typeof session.convId !== 'string' ||
    typeof session.expiresAt !== 'number'
  )
    return empty()
  const messages: DemoMessage[] = Array.isArray(saved.messages)
    ? saved.messages.flatMap((value) => {
        const row = record(value)
        if (
          typeof row.id !== 'string' ||
          typeof row.content !== 'string' ||
          !['user', 'assistant'].includes(String(row.role))
        )
          return []
        return [
          {
            id: row.id,
            role: row.role as DemoMessage['role'],
            content: row.content,
            failed: row.failed === true,
          },
        ]
      })
    : []
  // A customer message without a subsequent assistant reply was interrupted: retry the same ID.
  if (messages.at(-1)?.role === 'user') messages[messages.length - 1].failed = true
  return {
    session: {
      token: session.token,
      convId: session.convId,
      expiresAt: session.expiresAt,
      name: String(session.name || ''),
    },
    messages,
    actions: actionsFrom(saved.actions),
    handoff: typeof saved.handoff === 'string' ? saved.handoff : '',
    expired: saved.expired === true || session.expiresAt <= Date.now(),
  }
}

export function BusinessDemo({ context, children }: { context: RequestContext; children: ReactNode }) {
  // children is the existing support workspace. Wrap it with an optional demo rather
  // than rewriting its conversation lifecycle. The parent remounts us on backend changes.
  const key = `support.business.demo:${context.backend}:${context.baseUrl}`
  const capabilities = useBusinessCapabilities(context.baseUrl, context.backend === 'python')
  const [open, setOpen] = useState(false)
  const [accounts, setAccounts] = useState<Persona[]>([])
  const [state, setState] = useState(() => restore(key))
  // useState causes a render when changed; useRef holds a current value without
  // waiting for another render. Async callbacks read current.current to avoid stale state.
  const current = useRef(state)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [storageError, setStorageError] = useState(false)
  const controller = useRef<AbortController | undefined>(undefined)
  // Incrementing generation invalidates old async work after reset/unmount. Abort alone
  // is insufficient because a response may already be resolving when cancellation happens.
  const generation = useRef(0)
  function commit(next: DemoState) {
    // Update the ref, rendered state, and tab storage together. Show a storage error
    // if persistence fails rather than promise that a reload can always recover the message.
    current.current = next
    setState(next)
    setStorageError(!writeStorage('sessionStorage', key, next))
  }
  function failed(cause: unknown) {
    // A 401 means the account credential expired/failed. Keep the conversation visible
    // for reference but stop new writes instead of falling back to anonymous support.
    setError(cause instanceof Error ? cause.message : 'The service is unavailable.')
    if (cause instanceof ApiError && cause.status === 401) commit({ ...current.current, expired: true })
  }
  useEffect(
    // Cleanup runs when this component unmounts; React may also exercise cleanup during
    // development. Invalidate outstanding responses and cancel browser waiting.
    () => () => {
      generation.current++
      controller.current?.abort()
    },
    [],
  )
  useEffect(() => {
    // Fetch persona choices only while the chooser is visible and no account is active.
    if (!open || state.session) return
    const abort = new AbortController()
    void personas(context.baseUrl, abort.signal)
      .then((rows) => {
        if (!abort.signal.aborted) setAccounts(rows)
      })
      .catch((cause) => {
        if (!abort.signal.aborted) failed(cause)
      })
    return () => abort.abort()
  }, [open, state.session, context.baseUrl])
  const hasPending = state.actions.some((action) => action.status === 'pending')
  useEffect(() => {
    // Poll only pending, visible account work while no explicit operation is busy.
    // This observes staff decisions made elsewhere; it does not request another approval.
    if (!open || !state.session || state.expired || !hasPending || busy) return
    const abort = new AbortController()
    const version = generation.current
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      // Schedule the next poll after this one finishes, preventing overlapping requests.
      // Eight thousand milliseconds is eight seconds; hidden tabs resume on visibilitychange.
      if (document.hidden || abort.signal.aborted) return
      try {
        const actions = await getActions(
          context.baseUrl,
          state.session!.token,
          state.session!.convId,
          abort.signal,
        )
        if (!abort.signal.aborted && version === generation.current) commit({ ...current.current, actions })
      } catch (cause) {
        if (!abort.signal.aborted && version === generation.current) failed(cause)
      }
      if (!abort.signal.aborted) timer = setTimeout(() => void poll(), 8000)
    }
    function visible() {
      clearTimeout(timer)
      if (!document.hidden) void poll()
    }
    document.addEventListener('visibilitychange', visible)
    void poll()
    return () => {
      // Clear all resources owned by this effect before starting a replacement effect.
      abort.abort()
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [open, state.session?.token, state.expired, hasPending, busy, context.baseUrl])

  async function start(account: Persona) {
    // Reserve one in-flight controller synchronously so rapid clicks cannot create
    // multiple accounts before React has had a chance to render busy=true.
    if (controller.current) return
    const abort = new AbortController()
    controller.current = abort
    const version = generation.current
    setBusy(true)
    setError('')
    try {
      const session = await createDemo(context.baseUrl, account.id, abort.signal)
      if (!abort.signal.aborted && version === generation.current) {
        commit({ ...empty(), session })
        setDraft(account.starter)
      }
    } catch (cause) {
      if (version === generation.current) failed(cause)
    } finally {
      if (controller.current === abort) {
        controller.current = undefined
        setBusy(false)
      }
    }
  }
  async function send(content: string, retryId?: string) {
    // Retry an unresolved message before accepting a different one, matching server
    // ordering. A retry replaces the same UI row; a new message appends a fresh row.
    const before = current.current
    if (controller.current || !before.session || before.expired || !content.trim()) return
    if (!retryId && before.messages.some((message) => message.failed)) return
    const id = retryId || crypto.randomUUID()
    // Store this stable ID before HTTP. If a reload interrupts the reply, restore()
    // exposes a retry using this same identity instead of risking a duplicate effect.
    const message: DemoMessage = { id, role: 'user', content: content.trim() }
    const messages = retryId
      ? before.messages.map((row) => (row.id === id ? message : row))
      : [...before.messages, message]
    commit({ ...before, messages }) // Persist the stable request ID before sending.
    const abort = new AbortController()
    controller.current = abort
    const version = generation.current
    setBusy(true)
    setError('')
    setDraft('')
    try {
      const result = await businessChat(context.baseUrl, before.session, message.content, id, abort.signal)
      if (!abort.signal.aborted && version === generation.current)
        commit({
          ...current.current,
          messages: [...messages, { id: crypto.randomUUID(), role: 'assistant', content: result.response }],
          actions: result.actions,
          handoff: result.handoff || current.current.handoff,
        })
    } catch (cause) {
      if (version === generation.current) {
        // Mark uncertainty, not rollback: the server could already have committed.
        // Only the matching generation may mutate the currently selected demo.
        commit({
          ...current.current,
          messages: messages.map((row) => (row.id === id ? { ...row, failed: true } : row)),
        })
        failed(cause)
      }
    } finally {
      if (controller.current === abort) {
        controller.current = undefined
        setBusy(false)
      }
    }
  }
  async function decide(id: string, decision: 'approve' | 'reject', note: string) {
    // Customer demo decisions use the customer token and the simulation endpoint.
    // The action card is updated only after the server returns a persisted result.
    const session = current.current.session
    if (!session || controller.current || state.expired) return
    const abort = new AbortController()
    controller.current = abort
    const version = generation.current
    setBusy(true)
    setError('')
    try {
      const action = await decideAction(
        context.baseUrl,
        session.token,
        id,
        decision,
        note,
        true,
        abort.signal,
      )
      if (!abort.signal.aborted && version === generation.current)
        commit({
          ...current.current,
          actions: current.current.actions.map((row) => (row.id === id ? action : row)),
        })
    } catch (cause) {
      if (version === generation.current) failed(cause)
    } finally {
      if (controller.current === abort) {
        controller.current = undefined
        setBusy(false)
      }
    }
  }
  function reset() {
    // Invalidate late callbacks before clearing local state. Starting a new demo does
    // not delete server audit data or undo a previously committed simulated operation.
    generation.current++
    controller.current?.abort()
    controller.current = undefined
    removeStorage('sessionStorage', key)
    current.current = empty()
    setState(empty())
    setBusy(false)
    setError('')
    setDraft('')
  }
  // JSX describes rendered elements. Conditional expressions below choose account
  // selection, conversation, or the unchanged general workspace; they do not authorize
  // actions. Disabled buttons help users, but server-side rules remain authoritative.
  if (!capabilities.includes('business_workflows')) return children
  return (
    <>
      <div className="business-entry">
        <span>Explore a simulated customer account.</span>
        <button className="button secondary" onClick={() => setOpen(!open)}>
          {open ? 'Back to general support' : 'Try demo accounts'}
        </button>
      </div>
      {!open ? (
        children
      ) : (
        <section className="business-workspace" aria-label="Demo account support">
          <header>
            <div>
              <h1>{state.session ? `Support for ${state.session.name}` : 'Choose a demo account'}</h1>
              <p>Simulated payments and memberships. Changes affect only your demo account.</p>
            </div>
            {state.session && (
              <button className="button secondary" onClick={reset}>
                New demo account
              </button>
            )}
          </header>
          {error && (
            <div className="inline-error" role="alert">
              {error}
            </div>
          )}
          {storageError && (
            <p role="status">
              This browser cannot save the demo. Keep this page open while a request is processing.
            </p>
          )}
          {!state.session ? (
            <div className="business-personas">
              {accounts.map((account) => (
                <article key={account.id}>
                  <h2>{account.name}</h2>
                  <p>{account.tagline}</p>
                  <button className="button primary" disabled={busy} onClick={() => void start(account)}>
                    Use {account.name}'s account
                  </button>
                </article>
              ))}
            </div>
          ) : (
            <>
              {state.expired && (
                <p className="inline-notice" role="status">
                  This demo session has expired. Start a new demo account to continue.
                </p>
              )}
              <div className="business-messages" role="log" aria-label="Demo conversation">
                {state.messages.map((message) => (
                  <article className={`business-message ${message.role}`} key={message.id}>
                    <strong>{message.role === 'user' ? 'You' : 'Support'}</strong>
                    <p>{message.content}</p>
                    {message.failed && !state.expired && (
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() => void send(message.content, message.id)}
                      >
                        Retry this message
                      </button>
                    )}
                  </article>
                ))}
              </div>
              {state.handoff && (
                <p className="inline-notice">
                  Human review recorded · Ticket {state.handoff}. A live agent is not connected.
                </p>
              )}
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  void send(draft)
                }}
              >
                <label htmlFor="business-message">Message your demo account support</label>
                <textarea
                  id="business-message"
                  maxLength={4000}
                  value={draft}
                  disabled={busy || state.expired}
                  onChange={(event) => setDraft(event.target.value)}
                />
                <div className="business-form-actions">
                  <button
                    className="button primary"
                    disabled={
                      busy ||
                      state.expired ||
                      !draft.trim() ||
                      state.messages.some((message) => message.failed)
                    }
                  >
                    {busy ? 'Processing…' : 'Send to account support'}
                  </button>
                  {busy && (
                    <button
                      type="button"
                      className="button secondary"
                      onClick={() => controller.current?.abort()}
                    >
                      Stop waiting
                    </button>
                  )}
                </div>
              </form>
              {state.actions.length > 0 && (
                <section aria-label="Account operations">
                  <h2>Operations</h2>
                  <BusinessActions
                    actions={state.actions}
                    demo
                    busy={busy || state.expired}
                    decide={
                      capabilities.includes('demo_self_approval')
                        ? (id, decision, note) => void decide(id, decision, note)
                        : undefined
                    }
                  />
                </section>
              )}
            </>
          )}
        </section>
      )}
    </>
  )
}
