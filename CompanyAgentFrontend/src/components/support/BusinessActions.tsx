import { useState } from 'react'
import type { BusinessAction } from '../../api/business'

// Translate persisted server states into readable labels. Unknown states are displayed
// verbatim below rather than being incorrectly presented as a successful refund.
const labels: Record<string, string> = {
  pending: 'Pending review',
  executed: 'Completed',
  denied: 'Not allowed',
  rejected: 'Declined',
  expired: 'Expired',
  failed: 'Failed',
}

export function BusinessActions({
  actions,
  demo = false,
  busy = false,
  decide,
}: {
  actions: BusinessAction[]
  demo?: boolean
  busy?: boolean
  decide?: (id: string, decision: 'approve' | 'reject', note: string) => void
}) {
  // This reusable view receives facts and an optional decision callback from its parent.
  // notes maps action IDs to drafts so typing for one card does not overwrite another.
  // Updating via a function reads the latest map and {...values} preserves other drafts.
  const [notes, setNotes] = useState<Record<string, string>>({})
  // JSX map renders one card per action, keyed by its stable ID. Approval controls
  // require pending state AND a supplied callback. The demo label is explicit; neither
  // hidden controls nor disabled buttons replace backend authentication/validation.
  // Rejection needs a nonblank note; high-value approval notes are checked by the server.
  return (
    <div className="business-actions">
      {actions.map((action) => (
        <article className="business-action" key={action.id}>
          <div className="business-action-heading">
            <strong>{action.summary}</strong>
            <span className={`business-status ${action.status}`}>
              {labels[action.status] || action.status}
            </span>
          </div>
          <p>{action.reason_code.replaceAll('_', ' ').toLowerCase()}</p>
          {action.status === 'pending' && decide && (
            <div className="business-decision">
              {demo && <strong>Demo controls: simulate supervisor decision</strong>}
              <label htmlFor={`note-${action.id}`}>
                Decision note (required for rejection or high-value approval)
              </label>
              <textarea
                id={`note-${action.id}`}
                value={notes[action.id] || ''}
                maxLength={2000}
                disabled={busy}
                onChange={(event) => setNotes((values) => ({ ...values, [action.id]: event.target.value }))}
              />
              <div>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => decide(action.id, 'approve', notes[action.id] || '')}
                >
                  Approve{demo ? ' as supervisor' : ''}
                </button>{' '}
                <button
                  className="button secondary"
                  disabled={busy || !notes[action.id]?.trim()}
                  onClick={() => decide(action.id, 'reject', notes[action.id] || '')}
                >
                  Reject
                </button>
              </div>
            </div>
          )}
        </article>
      ))}
    </div>
  )
}
