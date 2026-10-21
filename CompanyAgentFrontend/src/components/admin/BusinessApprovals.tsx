import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api/backend'
import { decideAction, getActions, type BusinessAction } from '../../api/business'
import type { AdminSession } from '../../types'
import { BusinessActions } from '../support/BusinessActions'

export function BusinessApprovals({ session, expired }: { session: AdminSession; expired: () => void }) {
  // session is an administrator credential, separate from a customer demo token.
  // State drives the cards and error UI; controller identifies the active HTTP attempt.
  const [actions, setActions] = useState<BusinessAction[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | undefined>(undefined)
  function failure(cause: unknown) {
    // Tell the host to end an expired staff session rather than quietly retrying with
    // customer credentials. Non-authentication errors remain visible in this panel.
    setError(cause instanceof Error ? cause.message : 'Could not load operations.')
    if (cause instanceof ApiError && cause.status === 401) expired()
  }
  async function refresh() {
    // Cancel any previous list request. Its finally block checks controller identity
    // so a late old response cannot clear the newer request's busy indicator.
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError('')
    try {
      const rows = await getActions(session.baseUrl, session.token, undefined, abort.signal)
      if (!abort.signal.aborted) setActions(rows)
    } catch (cause) {
      if (!abort.signal.aborted) failure(cause)
    } finally {
      if (controller.current === abort) {
        controller.current = undefined
        setBusy(false)
      }
    }
  }
  useEffect(() => {
    // Load on mount and credential/endpoint changes; abort requests when leaving.
    void refresh()
    return () => controller.current?.abort()
  }, [session.baseUrl, session.token])
  async function decide(id: string, decision: 'approve' | 'reject', note: string) {
    // Serialize explicit decisions. false selects the administrator URL in the API
    // helper; the server validates that token before permitting cross-customer approval.
    if (controller.current) return
    const abort = new AbortController()
    controller.current = abort
    setBusy(true)
    setError('')
    try {
      const row = await decideAction(session.baseUrl, session.token, id, decision, note, false, abort.signal)
      if (!abort.signal.aborted) setActions((rows) => rows.map((action) => (action.id === id ? row : action)))
    } catch (cause) {
      if (!abort.signal.aborted) {
        failure(cause)
        if (cause instanceof ApiError && cause.status === 409) {
          // Another staff member or expiry may have changed the row. Reload facts
          // instead of claiming our failed click completed the operation.
          const rows = await getActions(session.baseUrl, session.token, undefined, abort.signal).catch(
            () => undefined,
          )
          if (rows && !abort.signal.aborted) setActions(rows)
        }
      }
    } finally {
      if (controller.current === abort) {
        controller.current = undefined
        setBusy(false)
      }
    }
  }
  // The view renders server results and exposes a manual refresh. It shares the card
  // component with demo support while keeping administrator credentials in this parent.
  return (
    <section className="business-workspace" aria-label="Business approvals">
      <header>
        <div>
          <h2>Account operations & approvals</h2>
          <p>Review the requested amount and reason before deciding.</p>
        </div>
        <button className="button secondary" disabled={busy} onClick={() => void refresh()}>
          Refresh operations
        </button>
      </header>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {!actions.length && <p>No account operations to review.</p>}
      <BusinessActions
        actions={actions}
        busy={busy}
        decide={(id, decision, note) => void decide(id, decision, note)}
      />
    </section>
  )
}
