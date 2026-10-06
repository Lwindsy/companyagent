import { useEffect, useState, type ReactNode } from 'react'
import {
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  Command,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageCircle,
  Moon,
  Plus,
  Search,
  ShieldCheck,
  Sun,
  Trash2,
  X,
} from 'lucide-react'
import type { Conversation, View } from '../../types'
import { Brand } from '../ui/Brand'
import { Dialog } from '../ui/Dialog'

interface Props {
  children: ReactNode
  view: View
  navigate: (view: View) => void
  conversations: Conversation[]
  activeId: string
  select: (id: string) => void
  remove: (id: string) => void
  newConversation: () => void
  theme: 'light' | 'dark'
  toggleTheme: () => void
  username?: string
  logout: () => void
}

export function AppShell(props: Props) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [deleting, setDeleting] = useState<Conversation | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen((value) => !value)
      }
      if (event.key === 'Escape') setMobileOpen(false)
    }
    document.addEventListener('keydown', shortcut)
    return () => document.removeEventListener('keydown', shortcut)
  }, [])
  function go(view: View) {
    props.navigate(view)
    setMobileOpen(false)
    setPaletteOpen(false)
  }
  function newChat() {
    props.newConversation()
    setMobileOpen(false)
    setPaletteOpen(false)
  }
  function select(id: string) {
    props.select(id)
    setMobileOpen(false)
    setPaletteOpen(false)
  }
  const histories = props.conversations.filter((item) =>
    item.title.toLowerCase().includes(filter.toLowerCase()),
  )
  const commands = [
    { label: 'New conversation', icon: Plus, action: newChat },
    { label: 'Explore guides', icon: BookOpen, action: () => go('guides') },
    { label: 'Service operations', icon: LayoutDashboard, action: () => go('admin') },
    {
      label: `Switch to ${props.theme === 'light' ? 'dark' : 'light'} theme`,
      icon: Moon,
      action: () => {
        props.toggleTheme()
        setPaletteOpen(false)
      },
    },
  ].filter((item) => item.label.toLowerCase().includes(paletteQuery.toLowerCase()))
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {mobileOpen && (
        <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />
      )}
      <aside className={`sidebar ${mobileOpen ? 'is-open' : ''}`} aria-label="Workspace navigation">
        <div className="sidebar-brand">
          <button className="brand-button" onClick={() => go('support')} aria-label="Support home">
            <Brand />
          </button>
          <button
            className="icon-button mobile-close"
            aria-label="Close navigation"
            onClick={() => setMobileOpen(false)}
          >
            <X size={20} />
          </button>
          <span className="edition">WORKSPACE</span>
        </div>
        <button className="new-conversation" onClick={newChat}>
          <Plus size={19} /> New conversation <span>↗</span>
        </button>
        <nav className="primary-nav" aria-label="Main navigation">
          <button
            className={props.view === 'support' ? 'active' : ''}
            onClick={() => go('support')}
            aria-current={props.view === 'support' ? 'page' : undefined}
          >
            <MessageCircle size={18} />
            <span>Conversations</span>
            <span className="nav-count">{props.conversations.length.toString().padStart(2, '0')}</span>
          </button>
          <button
            className={props.view === 'guides' ? 'active' : ''}
            onClick={() => go('guides')}
            aria-current={props.view === 'guides' ? 'page' : undefined}
          >
            <BookOpen size={18} />
            <span>Knowledge & guides</span>
            <ArrowUpRight size={14} />
          </button>
          {props.username && (
            <button
              className={props.view === 'admin' ? 'active' : ''}
              onClick={() => go('admin')}
              aria-current={props.view === 'admin' ? 'page' : undefined}
            >
              <LayoutDashboard size={18} />
              <span>Service operations</span>
            </button>
          )}
        </nav>
        <div className="history-heading">
          <span>RECENT CONVERSATIONS</span>
          <span>{props.conversations.length}</span>
        </div>
        {props.conversations.length > 3 && (
          <label className="history-search">
            <Search size={14} />
            <input
              aria-label="Search conversations"
              placeholder="Find a conversation"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
        )}
        <div className="history-list">
          {histories.map((item) => (
            <div
              className={`history-entry ${item.id === props.activeId && props.view === 'support' ? 'active' : ''}`}
              key={item.id}
            >
              <button className="history-select" onClick={() => select(item.id)} title={item.title}>
                <MessageCircle size={14} />
                <span>{item.title}</span>
                {item.messages.some((message) => message.status === 'pending') && (
                  <span className="pending-dot" aria-label="Reply in progress" />
                )}
              </button>
              <button
                className="history-delete"
                onClick={() => setDeleting(item)}
                aria-label={`Delete ${item.title}`}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          {!histories.length && (
            <div className="history-empty">
              <span className="history-empty-line" />
              <p>{filter ? 'No matching conversations.' : 'A little space for your next big question.'}</p>
            </div>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="note-symbol">✦</span>
            <strong>A clearer way forward.</strong>
            <p>
              A little guidance.
              <br />A lot less guesswork.
            </p>
            <button onClick={() => go('guides')}>
              Meet your support team <ArrowUpRight size={14} />
            </button>
          </div>
          <button
            className="command-trigger"
            onClick={() => {
              setPaletteQuery('')
              setPaletteOpen(true)
            }}
          >
            <Search size={16} />
            <span>Quick navigation</span>
            <kbd>⌘ K</kbd>
          </button>
          {props.username ? (
            <div className="sidebar-account">
              <span className="account-avatar">{props.username.slice(0, 2).toUpperCase()}</span>
              <div>
                <strong>{props.username}</strong>
                <span>Administrator</span>
              </div>
              <button className="icon-button" onClick={props.logout} aria-label="Sign out">
                <LogOut size={17} />
              </button>
            </div>
          ) : (
            <button
              className={`staff-portal-entry${props.view === 'admin' ? ' active' : ''}`}
              onClick={() => go('admin')}
              aria-label="Staff sign in"
              aria-current={props.view === 'admin' ? 'page' : undefined}
            >
              <span className="staff-portal-icon">
                <ShieldCheck size={19} />
              </span>
              <span className="staff-portal-copy">
                <span className="staff-portal-eyebrow">
                  <i /> Staff portal
                </span>
                <strong>Open operations</strong>
                <small>Metrics, knowledge &amp; tools</small>
              </span>
              <ArrowUpRight className="staff-portal-arrow" size={17} />
            </button>
          )}
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
              aria-expanded={mobileOpen}
            >
              <Menu size={21} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>
              {props.view === 'support'
                ? 'Customer support'
                : props.view === 'guides'
                  ? 'Knowledge & guides'
                  : 'Service operations'}
            </strong>
          </div>
          <div className="topbar-actions">
            <span className="demo-badge">
              <span /> Interactive demo
            </span>
            <a
              className="github-link"
              href="https://github.com/Lwindsy/companyagent"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="View source on GitHub"
              title="View the project on GitHub (opens in a new tab)"
            >
              <svg viewBox="0 0 16 16" width="17" height="17" fill="currentColor" aria-hidden="true">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
              </svg>
              <span>GitHub</span>
            </a>
            <button
              className="icon-button"
              onClick={props.toggleTheme}
              aria-label={`Switch to ${props.theme === 'light' ? 'dark' : 'light'} theme`}
            >
              {props.theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}
            </button>
          </div>
        </header>
        <main id="main-content" className={`main-content view-${props.view}`} tabIndex={-1}>
          {props.children}
        </main>
        <footer className="workspace-footer">
          <span>Thoughtful support, one conversation at a time.</span>
          <span>
            Built for clarity <span className="footer-star">✦</span>
          </span>
        </footer>
      </div>
      {deleting && (
        <Dialog title="Delete conversation" onClose={() => setDeleting(null)}>
          <span className="dialog-eyebrow">YOUR WORKSPACE</span>
          <h2>Clear this conversation?</h2>
          <p>
            “{deleting.title}” will be removed from this browser. This does not delete records held by the
            service.
          </p>
          <div className="dialog-actions">
            <button className="button secondary" onClick={() => setDeleting(null)}>
              Keep conversation
            </button>
            <button
              className="button danger"
              onClick={() => {
                props.remove(deleting.id)
                setDeleting(null)
              }}
            >
              Delete conversation
            </button>
          </div>
        </Dialog>
      )}
      {paletteOpen && (
        <Dialog title="Quick navigation" onClose={() => setPaletteOpen(false)}>
          <div className="palette-heading">
            <Command size={22} />
            <h2>Go somewhere.</h2>
          </div>
          <label className="search-input">
            <Search size={18} />
            <input
              autoFocus
              aria-label="Find a page or conversation"
              placeholder="Find a page or conversation…"
              value={paletteQuery}
              onChange={(event) => setPaletteQuery(event.target.value)}
            />
          </label>
          <div className="palette-results">
            {commands.map((item) => (
              <button key={item.label} onClick={item.action}>
                <item.icon size={18} />
                <span>{item.label}</span>
                <ArrowUpRight size={15} />
              </button>
            ))}
            {props.conversations
              .filter((item) => item.title.toLowerCase().includes(paletteQuery.toLowerCase()))
              .slice(0, 6)
              .map((item) => (
                <button key={item.id} onClick={() => select(item.id)}>
                  <MessageCircle size={17} />
                  <span>{item.title}</span>
                </button>
              ))}
            {!commands.length &&
              !props.conversations.some((item) =>
                item.title.toLowerCase().includes(paletteQuery.toLowerCase()),
              ) && <p className="muted">No matching pages or conversations.</p>}
          </div>
        </Dialog>
      )}
    </div>
  )
}
