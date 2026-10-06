import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight, Eye, EyeOff, LoaderCircle, LockKeyhole } from 'lucide-react'
import { BACKENDS } from '../../api/backend'
import type { BackendId } from '../../types'
import { Dialog } from '../ui/Dialog'

interface Props {
  backend: BackendId
  onBackendChange: (backend: BackendId) => void
  login: (username: string, password: string, signal: AbortSignal) => Promise<void>
  onClose: () => void
  onSuccess: () => void
  notice?: string
}

export function LoginDialog({ backend, onBackendChange, login, onClose, onSuccess, notice }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || !username.trim() || !password) return
    controller.current = new AbortController()
    const signal = controller.current.signal
    setBusy(true)
    setError('')
    try {
      await login(username, password, signal)
      if (!signal.aborted) onSuccess()
    } catch {
      if (!signal.aborted)
        setError('Sign-in failed. Check your credentials and that the selected service is reachable.')
    } finally {
      if (!signal.aborted) {
        setBusy(false)
        setPassword('')
      }
    }
  }
  return (
    <Dialog title="Staff sign in" onClose={onClose}>
      <div className="login-icon">
        <LockKeyhole size={23} />
      </div>
      <span className="dialog-eyebrow">A SPACE FOR YOUR TEAM</span>
      <h2>
        Behind great support,
        <br />
        there’s you.
      </h2>
      <p>Sign in to manage knowledge and keep your service running smoothly.</p>
      {notice && <div className="inline-notice">{notice}</div>}
      <form className="login-form" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span>Service</span>
          <select
            value={backend}
            disabled={busy}
            onChange={(event) => {
              onBackendChange(event.target.value as BackendId)
              setError('')
            }}
          >
            {BACKENDS.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} · {item.description}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Username</span>
          <input
            autoFocus
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Password</span>
          <div className="password-field">
            <input
              type={visible ? 'text' : 'password'}
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <button
              type="button"
              className="icon-button"
              aria-label={visible ? 'Hide password' : 'Show password'}
              onClick={() => setVisible((value) => !value)}
            >
              {visible ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </label>
        {error && (
          <div className="inline-error" role="alert">
            {error}
          </div>
        )}
        <button
          className="button primary login-submit"
          type="submit"
          disabled={busy || !username.trim() || !password}
        >
          {busy ? (
            <>
              <LoaderCircle size={16} className="spin" /> Signing in…
            </>
          ) : (
            <>
              Enter workspace <ArrowRight size={16} />
            </>
          )}
        </button>
      </form>
      <p className="login-footnote">
        Credentials are sent only to the selected service. Your session stays in this browser tab.
      </p>
    </Dialog>
  )
}
