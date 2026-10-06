import { useEffect, useState, type FormEvent } from 'react'
import { Check, Link2, Save } from 'lucide-react'
import { BACKENDS, normalizeBaseUrl, validEndpoint } from '../../api/backend'
import type { BackendSettings } from '../../types'

export function ConnectionSettings({
  settings,
  save,
  disabled,
}: {
  settings: BackendSettings
  save: (value: BackendSettings) => void
  disabled: boolean
}) {
  const [draft, setDraft] = useState(settings)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  useEffect(() => {
    setDraft(settings)
  }, [settings])
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!draft.userId.trim()) {
      setError('Enter a customer ID.')
      return
    }
    if (BACKENDS.some((item) => !validEndpoint(draft.endpoints[item.id]))) {
      setError(
        'Use a relative API path or an HTTP(S) URL without credentials, query parameters, or fragments.',
      )
      return
    }
    save({
      ...draft,
      userId: draft.userId.trim(),
      endpoints: {
        python: normalizeBaseUrl(draft.endpoints.python),
        java: normalizeBaseUrl(draft.endpoints.java),
        dotnet: normalizeBaseUrl(draft.endpoints.dotnet),
      },
    })
    setError('')
    setSaved(true)
  }
  return (
    <section className="admin-card">
      <div className="card-heading">
        <div className="card-icon">
          <Link2 size={19} />
        </div>
        <div>
          <span className="eyebrow">CONNECTIONS</span>
          <h2>Make yourself at home.</h2>
        </div>
      </div>
      <p className="card-description">
        Configure your services and customer identity. Existing conversations keep their original connection.
      </p>
      <form onSubmit={submit} onChange={() => setSaved(false)}>
        <div className="settings-grid">
          {BACKENDS.map((backend) => (
            <label className="field" key={backend.id}>
              <span>{backend.label} API</span>
              <input
                value={draft.endpoints[backend.id]}
                onChange={(event) =>
                  setDraft((value) => ({
                    ...value,
                    endpoints: { ...value.endpoints, [backend.id]: event.target.value },
                  }))
                }
                placeholder={backend.defaultUrl}
                required
                disabled={disabled}
                spellCheck={false}
              />
            </label>
          ))}
          <label className="field">
            <span>Customer ID</span>
            <input
              value={draft.userId}
              onChange={(event) => setDraft((value) => ({ ...value, userId: event.target.value }))}
              required
              disabled={disabled}
            />
          </label>
        </div>
        {error && (
          <div className="inline-error" role="alert">
            {error}
          </div>
        )}
        <div className="settings-footer">
          <p>Changing an API address requires signing in again to that service.</p>
          <button className="button secondary" type="submit" disabled={disabled}>
            {saved ? <Check size={15} /> : <Save size={15} />}
            {saved ? 'Saved' : 'Save settings'}
          </button>
        </div>
      </form>
    </section>
  )
}
