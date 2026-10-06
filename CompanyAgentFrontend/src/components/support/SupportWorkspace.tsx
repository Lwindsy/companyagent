import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  Copy,
  CreditCard,
  KeyRound,
  Package,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  Wrench,
} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ConversationController } from '../../hooks/useConversations'
import type { Message } from '../../types'
import { SupportOrb } from '../ui/Brand'

const topics = [
  {
    title: 'Orders & delivery',
    description: 'From checkout to your doorstep.',
    prompt: 'I need help with my order or delivery.',
    icon: Package,
    color: 'mint',
    number: '01',
  },
  {
    title: 'Billing & refunds',
    description: 'Make sense of the numbers.',
    prompt: 'I need help with a refund or billing issue.',
    icon: CreditCard,
    color: 'peach',
    number: '02',
  },
  {
    title: 'Account & security',
    description: 'Get back to what matters.',
    prompt: 'I need help accessing or securing my account.',
    icon: KeyRound,
    color: 'lavender',
    number: '03',
  },
  {
    title: 'Technical help',
    description: 'A fresh perspective on the problem.',
    prompt: 'I am experiencing a technical issue.',
    icon: Wrench,
    color: 'blue',
    number: '04',
  },
]

function Reply({ message }: { message: Message }) {
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setCopyFailed(false)
      timer.current = setTimeout(() => setCopied(false), 1800)
    } catch {
      setCopyFailed(true)
    }
  }
  return (
    <article className="chat-message assistant">
      <div className="assistant-avatar">
        <Sparkles size={17} />
      </div>
      <div className="message-body">
        <div className="message-byline">
          <strong>Support</strong>
          <span>Your thinking partner</span>
        </div>
        <div className="markdown">
          <Markdown
            remarkPlugins={[remarkGfm]}
            skipHtml
            components={{
              a: ({ children, href }) => (
                <a href={href} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              ),
              img: ({ alt }) => <span className="muted">[Image: {alt || 'attachment'}]</span>,
            }}
          >
            {message.content}
          </Markdown>
        </div>
        <div className="reply-meta">
          {message.meta?.knowledgeUsed && (
            <span>
              <BookOpen size={12} /> Knowledge assisted
            </span>
          )}
          {message.meta?.escalated && (
            <span className="escalation-note">Human handoff requested · not yet connected</span>
          )}
          <button onClick={() => void copy()} className="copy-button" aria-label="Copy reply">
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
          {copyFailed && <span role="status">Copy unavailable. Select the text to copy it.</span>}
        </div>
      </div>
    </article>
  )
}

export function SupportWorkspace({
  chat,
  openGuides,
}: {
  chat: ConversationController
  openGuides: (id?: string) => void
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [showLatest, setShowLatest] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const draftKey = chat.activeId || `new-${chat.newConversationKey}`
  const draft = drafts[draftKey] || ''
  const messages = chat.active?.messages || []
  const hasMessages = messages.length > 0
  function setDraft(value: string) {
    setDrafts((items) => ({ ...items, [draftKey]: value }))
  }
  function latest() {
    endRef.current?.scrollIntoView({
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
      block: 'end',
    })
    setShowLatest(false)
  }
  useEffect(() => {
    if (!showLatest) latest()
  }, [messages.length, chat.pending]) // Keep manual reading position when the user scrolls up.
  useEffect(() => {
    setShowLatest(false)
    inputRef.current?.focus()
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [chat.activeId, chat.newConversationKey])
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 160)}px`
    }
  }, [draft])
  async function send(event?: FormEvent) {
    event?.preventDefault()
    if (!draft.trim() || chat.pending) return
    const content = draft
    setDraft('')
    setShowLatest(false)
    await chat.send(content)
  }
  function choose(prompt: string) {
    setDraft(prompt)
    inputRef.current?.focus()
  }
  return (
    <div className="support-layout">
      <section
        className={`conversation-panel ${hasMessages ? 'has-messages' : ''}`}
        aria-label="Customer support chat"
      >
        {!hasMessages ? (
          <div className="welcome-content">
            <div className="welcome-eyebrow">
              <span className="tiny-star">✦</span> A LITTLE CLARITY STARTS HERE
            </div>
            <div className="welcome-hero">
              <div>
                <h1>
                  Big questions.
                  <br />
                  <span>Clear next steps.</span>
                </h1>
                <p>
                  Whatever’s on your mind, let’s work through it.
                  <br className="desktop-break" /> Your support starts with a conversation.
                </p>
              </div>
              <SupportOrb />
            </div>
            <div className="section-caption">
              <span>WHERE WOULD YOU LIKE TO START?</span>
              <span>
                Pick a topic, or just ask <ArrowDown size={12} />
              </span>
            </div>
            <div className="topic-grid">
              {topics.map((topic) => (
                <button
                  key={topic.title}
                  className={`topic-card ${topic.color}`}
                  onClick={() => choose(topic.prompt)}
                >
                  <div className="topic-top">
                    <span className="topic-icon">
                      <topic.icon size={21} strokeWidth={1.65} />
                    </span>
                    <span className="topic-number">{topic.number}</span>
                  </div>
                  <h2>{topic.title}</h2>
                  <p>{topic.description}</p>
                  <ArrowUpRight className="topic-arrow" size={17} />
                </button>
              ))}
            </div>
            <div className="welcome-assurance">
              <span className="overlap-dots">
                <i />
                <i />
                <i />
              </span>
              <p>One conversation. The right expertise.</p>
              <span className="assurance-line" />
            </div>
          </div>
        ) : (
          <>
            <div className="conversation-heading">
              <div>
                <span className="eyebrow">YOUR CONVERSATION</span>
                <h1>{chat.active?.title}</h1>
              </div>
              <button className="button secondary small-button" onClick={chat.newConversation}>
                New chat <ArrowUpRight size={15} />
              </button>
            </div>
            <div
              className="messages"
              ref={scrollRef}
              role="log"
              aria-label="Conversation messages"
              aria-live="polite"
              aria-relevant="additions text"
              onScroll={() => {
                const element = scrollRef.current
                if (element)
                  setShowLatest(element.scrollHeight - element.scrollTop - element.clientHeight > 140)
              }}
            >
              {messages.map((message) =>
                message.role === 'assistant' ? (
                  <Reply key={message.id} message={message} />
                ) : (
                  <article key={message.id} className="chat-message customer">
                    <div className="message-body">
                      <div className="message-byline">
                        <strong>You</strong>
                        <span>{message.status === 'pending' ? 'Sending' : 'Customer'}</span>
                      </div>
                      <p className="customer-bubble">{message.content}</p>
                      {['error', 'cancelled'].includes(message.status || '') && (
                        <div className="message-error" role="status">
                          <span>
                            {message.status === 'cancelled'
                              ? 'Reply stopped. The service may still process this message.'
                              : 'We couldn’t reach support. Please try again.'}
                          </span>
                          <button
                            disabled={chat.pending}
                            onClick={() => void chat.send(message.content, message.id)}
                          >
                            <RotateCcw size={13} /> Retry
                          </button>
                        </div>
                      )}
                    </div>
                    <span className="customer-avatar">Y</span>
                  </article>
                ),
              )}
              {chat.pending && (
                <div className="typing-indicator" role="status">
                  <span className="assistant-avatar">
                    <Sparkles size={17} />
                  </span>
                  <span className="typing-dots">
                    <i />
                    <i />
                    <i />
                  </span>
                  <span>Finding a way forward…</span>
                </div>
              )}
              <div ref={endRef} />
            </div>
            {showLatest && (
              <button className="latest-button" onClick={latest}>
                <ArrowDown size={14} /> Latest messages
              </button>
            )}
          </>
        )}
        <div className="composer-container">
          <form
            className={`composer ${chat.pending ? 'is-pending' : ''}`}
            onSubmit={(event) => void send(event)}
          >
            <textarea
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={2}
              maxLength={10000}
              aria-label="Message support"
              placeholder="Tell us what’s happening…"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  void send()
                }
              }}
            />
            <div className="composer-toolbar">
              <span>
                <Sparkles size={14} /> A thoughtful answer is a message away.
              </span>
              {chat.pending ? (
                <button
                  type="button"
                  className="send-button stop-button"
                  onClick={chat.cancel}
                  aria-label="Stop reply"
                >
                  <Square size={15} fill="currentColor" />
                </button>
              ) : (
                <button
                  className="send-button"
                  type="submit"
                  disabled={!draft.trim()}
                  aria-label="Send message"
                >
                  <ArrowUp size={20} />
                </button>
              )}
            </div>
          </form>
          <div className="composer-hints">
            <span>
              <ShieldCheck size={12} /> Keep passwords and payment details private.
            </span>
            <span>
              Enter to send <span className="key-hint">↵</span>
            </span>
          </div>
          {!chat.storageAvailable && (
            <p className="inline-notice" role="status">
              Browser storage is unavailable. This conversation will not survive a refresh.
            </p>
          )}
        </div>
      </section>
      <aside className="support-aside" aria-label="Support resources">
        <div className="companion-card">
          <div className="companion-label">
            <span className="status-dot" /> DESIGNED AROUND YOU
          </div>
          <div className="companion-illustration" aria-hidden="true">
            <div className="mini-window">
              <span />
              <span />
              <span />
              <div className="mini-line long" />
              <div className="mini-line" />
              <div className="mini-chat">
                <CheckCheck size={20} />
              </div>
            </div>
            <span className="floating-spark">✦</span>
          </div>
          <h2>
            Less friction.
            <br />
            More resolution.
          </h2>
          <p>From the first question to the next step, we help you find your way.</p>
          <div className="companion-divider" />
          <div className="companion-feature">
            <span>01</span>
            <div>
              <strong>Understands the context</strong>
              <p>Keep the conversation going.</p>
            </div>
          </div>
          <div className="companion-feature">
            <span>02</span>
            <div>
              <strong>Finds the right expertise</strong>
              <p>General, billing, or technical.</p>
            </div>
          </div>
          <div className="companion-feature">
            <span>03</span>
            <div>
              <strong>Gives you a next step</strong>
              <p>Guidance you can work with.</p>
            </div>
          </div>
          <button onClick={() => openGuides('workflow')}>
            See how it works <ArrowRight size={16} />
          </button>
        </div>
        <div className="guide-teaser">
          <span className="eyebrow">A LITTLE MORE CONTEXT</span>
          <BookOpen size={23} strokeWidth={1.5} />
          <h3>Curiosity looks good on you.</h3>
          <p>Explore the ideas and technology behind your support experience.</p>
          <button onClick={() => openGuides('technical')}>
            Explore the guides <ArrowUpRight size={16} />
          </button>
        </div>
        <p className="demo-note">
          A demonstration workspace. Browser history is temporary; the service may retain conversation
          records.
        </p>
      </aside>
    </div>
  )
}
