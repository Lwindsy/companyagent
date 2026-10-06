import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Bell,
  CheckCircle2,
  CircleHelp,
  Database,
  Fingerprint,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  RefreshCw,
  Server,
  SlidersHorizontal,
} from 'lucide-react'
import { useState } from 'react'
import { BACKENDS } from '../../api/backend'
import { useAdminData } from '../../hooks/useAdminData'
import { record, textValue } from '../../lib/storage'
import type { AdminSession, BackendId, BackendSettings } from '../../types'
import { ConnectionSettings } from './ConnectionSettings'
import { KnowledgeTools } from './KnowledgeTools'

interface Props {
  settings: BackendSettings
  session?: AdminSession
  onExpired: () => void
  onLogin: () => void
  selectBackend: (id: BackendId) => void
  saveSettings: (settings: BackendSettings) => void
}

export function AdminWorkspace({
  settings,
  session,
  onExpired,
  onLogin,
  selectBackend,
  saveSettings,
}: Props) {
  const data = useAdminData(session, onExpired)
  const [tab, setTab] = useState<'overview' | 'settings'>('overview')
  const backend = BACKENDS.find((item) => item.id === settings.backend)!
  const healthy = ['ok', 'healthy', 'up'].includes(data.dashboard.health.toLowerCase())
  return (
    <div className="admin-workspace">
      <header className="admin-page-heading">
        <div>
          <span className="welcome-eyebrow">
            <Activity size={14} /> THE BIG PICTURE, IN FOCUS
          </span>
          <h1>
            Good support.
            <br className="mobile-break" /> Great oversight<span>.</span>
          </h1>
          <p>Your services, knowledge, and next moves. All in one place.</p>
        </div>
        <button
          className="button secondary"
          disabled={!session || data.refreshing}
          onClick={() => void data.refresh()}
        >
          <RefreshCw size={15} className={data.refreshing ? 'spin' : ''} />
          {data.refreshing ? 'Refreshing' : 'Refresh data'}
        </button>
      </header>
      <div className="service-switcher">
        <div className="service-segments" aria-label="Active backend">
          {BACKENDS.map((item) => (
            <button
              key={item.id}
              onClick={() => selectBackend(item.id)}
              disabled={data.saving}
              aria-pressed={settings.backend === item.id}
              className={settings.backend === item.id ? 'active' : ''}
            >
              <span className={`service-icon ${item.id}`}>
                {item.id === 'python' ? 'Py' : item.id === 'java' ? 'Jv' : '.N'}
              </span>
              <span>
                {item.label}
                <small>{item.description}</small>
              </span>
              {settings.backend === item.id && <CheckCircle2 size={14} />}
            </button>
          ))}
        </div>
        <span className="last-checked">
          {data.dashboard.checkedAt
            ? `Updated ${new Date(data.dashboard.checkedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
            : 'Waiting for service data'}
        </span>
      </div>
      {!session ? (
        <>
          <div className="admin-locked">
            <span className="login-icon">
              <LockKeyhole size={25} />
            </span>
            <h2>A fresh connection.</h2>
            <p>
              Sign in to {backend.label} to view metrics and manage its knowledge base.
              <br />
              Each service has its own secure session.
            </p>
            <button className="button primary" onClick={onLogin}>
              Sign in to {backend.label} <ArrowRight size={16} />
            </button>
          </div>
          <details className="connection-recovery">
            <summary>Need to update the service address?</summary>
            <ConnectionSettings settings={settings} save={saveSettings} disabled={false} />
          </details>
        </>
      ) : (
        <>
          <section className="metric-grid" aria-label="Service overview">
            <article className="metric-card">
              <div>
                <span>Service health</span>
                <Activity size={16} />
              </div>
              <strong className={healthy ? 'healthy-value' : ''}>
                {data.refreshing && !data.dashboard.checkedAt
                  ? 'Checking…'
                  : healthy
                    ? 'Healthy'
                    : data.dashboard.health}
              </strong>
              <small>
                <span className={`metric-dot ${healthy ? 'healthy' : ''}`} />
                {healthy ? 'Service is reachable' : 'Health endpoint status'}
              </small>
            </article>
            <article className="metric-card">
              <div>
                <span>Knowledge chunks</span>
                <Database size={16} />
              </div>
              <strong>{data.dashboard.chunks?.toLocaleString() ?? '—'}</strong>
              <small>Context your agents can use</small>
            </article>
            <article className="metric-card">
              <div>
                <span>Active alerts</span>
                <Bell size={16} />
              </div>
              <strong>{data.dashboard.alerts?.length ?? '—'}</strong>
              <small>
                {data.dashboard.alerts === null
                  ? 'Awaiting alert data'
                  : data.dashboard.alerts.length
                    ? 'Needs a closer look'
                    : 'Nothing needs attention'}
              </small>
            </article>
            <article className="metric-card active-service">
              <div>
                <span>Connected service</span>
                <Server size={16} />
              </div>
              <strong>{backend.label}</strong>
              <small title={settings.endpoints[settings.backend]}>
                {settings.endpoints[settings.backend]}
              </small>
            </article>
          </section>
          <div className="admin-tabs">
            <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>
              <LayoutDashboard size={15} /> Knowledge & activity
            </button>
            <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>
              <SlidersHorizontal size={15} /> Connection settings
            </button>
          </div>
          {data.dashboard.errors.length > 0 && (
            <div className="inline-error" role="alert">
              <strong>Some service data is unavailable.</strong>
              {data.dashboard.errors.map((error) => (
                <p key={error}>{error}</p>
              ))}
            </div>
          )}
          {data.error && (
            <div className="inline-error" role="alert">
              {data.error}
            </div>
          )}
          {data.notice && (
            <div className="inline-notice" role="status">
              <CheckCircle2 size={14} /> {data.notice}
            </div>
          )}
          {tab === 'settings' ? (
            <ConnectionSettings settings={settings} save={saveSettings} disabled={data.saving} />
          ) : (
            <KnowledgeTools
              results={data.results}
              searching={data.searching}
              saving={data.saving}
              search={data.search}
              mutate={data.mutate}
            />
          )}
          {data.dashboard.alerts !== null && data.dashboard.alerts.length > 0 && (
            <section className="admin-card alerts-card">
              <div className="card-heading">
                <Bell size={18} />
                <h2>Worth your attention.</h2>
              </div>
              {data.dashboard.alerts.map((alert, index) => (
                <p key={index}>
                  {typeof alert === 'string'
                    ? alert
                    : textValue(record(alert).message, JSON.stringify(alert))}
                </p>
              ))}
            </section>
          )}
          <div className="admin-bottom-grid">
            <section className="admin-card diagnostics">
              <div className="card-heading">
                <div className="card-icon">
                  <Fingerprint size={19} />
                </div>
                <div>
                  <span className="eyebrow">UNDER THE SURFACE</span>
                  <h2>Service diagnostics.</h2>
                </div>
                {data.refreshing && <LoaderCircle size={16} className="spin" />}
              </div>
              <p className="card-description">Inspect the latest response from your selected service.</p>
              <details>
                <summary>
                  Monitoring response <span>{data.dashboard.monitor ? 'JSON' : 'Unavailable'}</span>
                </summary>
                <pre>
                  {data.dashboard.monitor
                    ? JSON.stringify(data.dashboard.monitor, null, 2)
                    : 'No monitoring response available. Refresh to try again.'}
                </pre>
              </details>
              {data.operation && (
                <details open>
                  <summary>
                    Latest knowledge operation <span>JSON</span>
                  </summary>
                  <pre>{JSON.stringify(data.operation, null, 2)}</pre>
                </details>
              )}
            </section>
            <section className="api-card">
              <CircleHelp size={24} strokeWidth={1.5} />
              <span className="eyebrow">BUILT TO BE EXPLORED</span>
              <h2>Go a layer deeper.</h2>
              <p>Explore endpoints and contracts in the interactive API references.</p>
              <div>
                {BACKENDS.map((item) => (
                  <a
                    key={item.id}
                    href={`${settings.endpoints[item.id]}/docs`}
                    target={`api-docs-${item.id}`}
                    rel="noopener noreferrer"
                  >
                    {item.label} API <ArrowUpRight size={14} />
                  </a>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  )
}
