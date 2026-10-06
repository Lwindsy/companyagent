import { lazy, Suspense, useEffect, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { createInitialSettings, requestContext, saveSettings as persistSettings } from './api/backend'
import { AppShell } from './components/layout/AppShell'
import { SupportWorkspace } from './components/support/SupportWorkspace'
import { LoginDialog } from './components/admin/LoginDialog'
import { useAdminSession } from './hooks/useAdminSession'
import { useConversations } from './hooks/useConversations'
import { useWorkspaceRoute } from './hooks/useWorkspaceRoute'
import { readStorage, writeStorage } from './lib/storage'
import type { BackendId, BackendSettings, View } from './types'

const AdminWorkspace = lazy(() =>
  import('./components/admin/AdminWorkspace').then((module) => ({ default: module.AdminWorkspace })),
)
const GuideWorkspace = lazy(() =>
  import('./components/guides/GuideWorkspace').then((module) => ({ default: module.GuideWorkspace })),
)

export default function App() {
  const [settings, setSettings] = useState(createInitialSettings)
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    readStorage('localStorage', 'support.theme') === 'dark' ? 'dark' : 'light',
  )
  const [loginOpen, setLoginOpen] = useState(false)
  const [loginNotice, setLoginNotice] = useState('')
  const [settingsNotice, setSettingsNotice] = useState('')
  const auth = useAdminSession(settings)
  const chat = useConversations(requestContext(settings))
  const route = useWorkspaceRoute()
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    writeStorage('localStorage', 'support.theme', theme)
  }, [theme])
  useEffect(() => {
    persistSettings(settings)
  }, [])
  useEffect(() => {
    document.title = `${route.view === 'support' ? 'Your next step' : route.view === 'guides' ? 'Knowledge & guides' : 'Service operations'} · Support`
  }, [route.view])
  function saveSettings(value: BackendSettings) {
    setSettings(value)
    setSettingsNotice(
      persistSettings(value)
        ? ''
        : 'Settings are active, but this browser could not save them. They will reset on refresh.',
    )
  }
  function selectBackend(backend: BackendId) {
    saveSettings({ ...settings, backend })
  }
  function navigate(view: View) {
    route.navigate(view)
    if (view === 'admin' && !auth.session) {
      setLoginNotice('')
      setLoginOpen(true)
    }
  }
  function expire() {
    auth.expire()
    setLoginNotice('Your administrator session has expired. Please sign in again.')
    setLoginOpen(true)
  }
  return (
    <AppShell
      view={route.view}
      navigate={navigate}
      conversations={chat.conversations}
      activeId={chat.activeId}
      select={(id) => {
        chat.select(id)
        route.navigate('support')
      }}
      remove={chat.remove}
      newConversation={() => {
        chat.newConversation()
        route.navigate('support')
      }}
      theme={theme}
      toggleTheme={() => setTheme((value) => (value === 'light' ? 'dark' : 'light'))}
      username={auth.session?.username}
      logout={() => {
        auth.logout()
        route.navigate('support')
      }}
    >
      {settingsNotice && (
        <div className="inline-notice" role="status">
          {settingsNotice}
        </div>
      )}
      <Suspense
        fallback={
          <div className="loading-workspace" role="status">
            <LoaderCircle size={20} className="spin" /> Opening your workspace…
          </div>
        }
      >
        {route.view === 'support' && (
          <SupportWorkspace chat={chat} openGuides={(id) => route.navigate('guides', id)} />
        )}
        {route.view === 'guides' && (
          <GuideWorkspace
            selectedId={route.guide}
            select={(id) => route.navigate('guides', id)}
            back={() => route.navigate('support')}
          />
        )}
        {route.view === 'admin' && (
          <AdminWorkspace
            settings={settings}
            session={auth.session}
            onExpired={expire}
            onLogin={() => {
              setLoginNotice('')
              setLoginOpen(true)
            }}
            selectBackend={selectBackend}
            saveSettings={saveSettings}
          />
        )}
      </Suspense>
      {loginOpen && (
        <LoginDialog
          backend={settings.backend}
          onBackendChange={selectBackend}
          login={auth.login}
          onClose={() => setLoginOpen(false)}
          onSuccess={() => {
            setLoginOpen(false)
            route.navigate('admin')
          }}
          notice={loginNotice}
        />
      )}
    </AppShell>
  )
}
