<template>
  <main v-if="activeView === 'support'" class="support-app">
    <aside class="support-sidebar">
      <div class="sidebar-top">
        <a class="wordmark" href="#" aria-label="Support home" @click.prevent="startNewConversation">Support</a>
        <button class="new-chat-button" type="button" @click="startNewConversation"><span aria-hidden="true">+</span>New conversation</button>
        <button class="customer-docs-entry" type="button" @click="openDocuments"><span aria-hidden="true">▤</span><span><strong>Explore guides</strong><small>How support works</small></span></button>
        <nav class="conversation-history" aria-label="Recent conversations">
          <p class="history-label">Recent conversations</p>
          <p v-if="conversationHistory.length === 0" class="history-empty">Your conversations will appear here.</p>
          <div v-for="conversation in conversationHistory" :key="conversation.id" :class="['history-row', { active: conversation.id === activeConversationId }]">
            <button class="history-item" type="button" :title="conversation.title" @click="selectConversation(conversation.id)"><span>{{ conversation.title }}</span><small>{{ formatConversationTime(conversation.updatedAt) }}</small></button>
            <button class="history-delete" type="button" :aria-label="`Delete ${conversation.title}`" @click="deleteConversation(conversation.id)">Delete</button>
          </div>
        </nav>
      </div>
      <div class="sidebar-footer">
        <p>We are here to help with orders, billing, account access, and technical issues.</p>
        <span class="availability"><i></i> Support available</span>
        <button class="staff-access" type="button" @click="openAdmin">Staff sign in</button>
      </div>
    </aside>

    <section class="chat-workspace" aria-label="Customer support chat">
      <header class="chat-header"><div><p class="header-kicker">Customer support</p><h1>How can we help?</h1></div><div class="chat-header-actions"><a class="github-link" href="https://github.com/Lwindsy/companyagent" target="_blank" rel="noopener noreferrer" title="View source on GitHub"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg><span>GitHub</span></a><button class="header-docs-button" type="button" @click="openDocuments">Explore guides</button><button class="header-new-chat" type="button" @click="startNewConversation">New chat</button></div></header>
      <section ref="messageList" class="conversation" aria-live="polite">
        <div v-if="messages.length === 0" class="welcome-panel">
          <p class="welcome-label">Support center</p><h2>Get help, without the runaround.</h2><p class="welcome-copy">Tell us what is happening and we will guide you to the right next step.</p>
          <div class="suggestion-grid">
            <button v-for="suggestion in suggestions" :key="suggestion.title" class="suggestion-card" type="button" @click="useSuggestion(suggestion.prompt)"><strong>{{ suggestion.title }}</strong><span>{{ suggestion.description }}</span></button>
          </div>
        </div>
        <article v-for="item in messages" :key="item.id" :class="['chat-message', item.role]"><div class="message-inner"><p class="message-author">{{ item.role === 'user' ? 'You' : 'Support' }}</p><p v-if="item.role === 'user'" class="customer-message">{{ item.content }}</p><div v-else class="markdown-body" v-html="renderMarkdown(item.content)"></div></div></article>
        <article v-if="busy && activeConversationId === pendingConversationId" class="chat-message assistant" aria-label="Support is replying"><div class="message-inner typing-message"><p class="message-author">Support</p><span></span><span></span><span></span></div></article>
      </section>
      <footer class="composer-area"><form class="composer" @submit.prevent="sendMessage"><textarea ref="messageInput" v-model="draft" rows="1" placeholder="Message support..." aria-label="Message support" @keydown.enter.exact.prevent="sendMessage"></textarea><button type="submit" :disabled="busy || !draft.trim()" aria-label="Send message"><span aria-hidden="true">↑</span></button></form><p class="composer-note">This project is for demonstration purposes only. This browser clears its temporary conversation list when you close this page.</p><p class="composer-safety-note">Do not share passwords, verification codes, or full payment-card numbers.</p></footer>
    </section>
  </main>

  <main v-else-if="activeView === 'documents'" class="documents-app">
    <aside class="documents-sidebar">
      <button class="documents-brand" type="button" @click="closeDocuments">Support</button>
      <div class="documents-sidebar-copy"><p>Help center</p><h1>Explore guides</h1><span>Learn how requests are handled and the technology that supports the experience.</span></div>
      <nav class="documents-nav" aria-label="Customer documents">
        <button v-for="document in customerDocuments" :key="document.id" :class="{ active: selectedDocumentId === document.id }" type="button" @click="selectedDocumentId = document.id"><span>{{ document.eyebrow }}</span>{{ document.label }}</button>
      </nav>
      <button class="documents-back" type="button" @click="closeDocuments">← Back to conversation</button>
    </aside>

    <section class="documents-workspace">
      <header class="documents-header"><button class="documents-mobile-back" type="button" @click="closeDocuments">← Conversation</button><p>{{ selectedDocument.eyebrow }}</p><h2>{{ selectedDocument.title }}</h2><span>{{ selectedDocument.intro }}</span></header>
      <ol class="document-sections">
        <li v-for="section in selectedDocument.sections" :key="section.number" class="document-section">
          <div class="document-section-heading"><span>{{ section.number }}</span><div><h3>{{ section.title }}</h3><p>{{ section.detail }}</p></div></div>
          <ol class="document-items">
            <li v-for="item in section.items" :key="item.title"><h4>{{ item.title }}</h4><p>{{ item.detail }}</p></li>
          </ol>
        </li>
      </ol>
    </section>
  </main>

  <main v-else class="admin-app">
    <aside class="admin-sidebar">
      <div>
        <button class="admin-brand" type="button" @click="returnToSupport">Support <span>Admin</span></button>
        <nav class="admin-nav" aria-label="Admin navigation"><span>Workspace</span><button class="admin-nav-item active" type="button">Overview</button></nav>
        <nav class="admin-nav admin-nav-docs" aria-label="API documentation"><span>API documentation</span><a v-for="doc in docsLinks" :key="doc.id" class="admin-nav-item" :href="doc.url" :target="`api-docs-${doc.id}`" rel="noreferrer">{{ doc.label }} API</a></nav>
      </div>
      <div class="admin-account"><span>Signed in as {{ adminUsername }}</span><button type="button" @click="logout">Sign out</button></div>
    </aside>

    <section class="admin-workspace">
      <header class="admin-header"><div><p>Administrator workspace</p><h1>Service operations</h1></div><div class="admin-header-actions"><a class="github-link" href="https://github.com/Lwindsy/companyagent" target="_blank" rel="noopener noreferrer" title="View source on GitHub"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg><span>GitHub</span></a><span :class="['service-badge', healthOk ? 'online' : 'offline']">{{ healthLabel }}</span><button type="button" @click="loadAdminData" :disabled="adminBusy">{{ adminBusy ? 'Refreshing…' : 'Refresh' }}</button><button class="return-button" type="button" @click="returnToSupport">Customer view</button></div></header>

      <section class="metric-grid" aria-label="Service overview">
        <article class="metric-card"><span>Backend</span><strong>{{ currentBackend.label }}</strong><small>{{ currentBackend.baseUrl }}</small></article>
        <article class="metric-card"><span>Health</span><strong :class="healthOk ? 'metric-ok' : ''">{{ healthLabel }}</strong><small>{{ healthOk ? 'Service is reachable' : 'Check connection details' }}</small></article>
        <article class="metric-card"><span>Knowledge chunks</span><strong>{{ knowledgeCount }}</strong><small>Available to the support agents</small></article>
        <article class="metric-card"><span>Active alerts</span><strong>{{ alertCount }}</strong><small>{{ alertCount ? 'Review service alerts' : 'No active alerts reported' }}</small></article>
      </section>

      <p v-if="adminError" class="admin-error">{{ adminError }}</p>

      <section class="admin-section settings-section">
        <div class="section-heading"><div><p>Configuration</p><h2>Connection and conversation settings</h2></div><span class="section-note">Saved in this browser</span></div>
        <div class="backend-choice"><button v-for="option in BACKEND_OPTIONS" :key="option.id" :class="{ active: settings.backend === option.id }" type="button" @click="switchBackend(option.id)">{{ option.label }}</button></div>
        <div class="field-grid">
          <label><span>Python API</span><input v-model="settings.endpoints.python" @change="persist" placeholder="/api/python"></label>
          <label><span>Java API</span><input v-model="settings.endpoints.java" @change="persist" placeholder="/api/java"></label>
          <label><span>.NET API</span><input v-model="settings.endpoints.dotnet" @change="persist" placeholder="/api/dotnet"></label>
          <label><span>Customer ID</span><input v-model="settings.userId" @change="persist" placeholder="web-user"></label>
          <label><span>Conversation ID</span><input v-model="settings.conversationId" @change="persist" placeholder="Generated automatically"></label>
        </div>
      </section>

      <section class="admin-tools-grid">
        <article class="admin-section">
          <div class="section-heading"><div><p>Knowledge base</p><h2>Search support knowledge</h2></div><span class="tag">RAG</span></div>
          <div class="search-form"><input v-model="searchQuery" placeholder="How long does a refund take to arrive?"><button type="button" @click="searchKnowledge" :disabled="adminBusy || !searchQuery.trim()">Search</button></div>
          <div v-if="searchResults.length" class="result-list"><article v-for="item in searchResults" :key="item.id || item.title" class="result-item"><div><strong>{{ item.title || 'Untitled result' }}</strong><small>Relevance {{ item.score ?? '-' }}</small></div><p>{{ item.content }}</p></article></div>
          <p v-else class="empty-result">Search the knowledge base to inspect the material used by support agents.</p>
        </article>

        <article class="admin-section">
          <div class="section-heading"><div><p>Knowledge base</p><h2>Add or upload content</h2></div><span class="tag">Admin only</span></div>
          <label><span>Title</span><input v-model="docTitle" placeholder="Additional refund policy"></label>
          <label><span>Content</span><textarea v-model="docContent" rows="5" placeholder="Enter support knowledge"></textarea></label>
          <div class="admin-actions"><button type="button" @click="submitKnowledge" :disabled="adminBusy || !docTitle.trim() || !docContent.trim()">Add document</button><label class="upload-button">Upload file<input type="file" accept=".txt,.md,.json" @change="handleUpload"></label></div>
        </article>
      </section>

      <section v-if="statusText" class="admin-section status-section"><div class="section-heading"><div><p>Diagnostic output</p><h2>Latest service response</h2></div></div><pre>{{ statusText }}</pre></section>
    </section>
  </main>

  <div v-if="showLogin" class="login-backdrop" @click.self="closeLogin">
    <form class="login-card" @submit.prevent="login">
      <button class="close-login" type="button" aria-label="Close sign in" @click="closeLogin">×</button>
      <p>Staff access</p><h2>Sign in to the admin workspace</h2><span class="login-copy">Use your administrator credentials to manage service operations.</span>
      <label><span>Username</span><input v-model="loginUsername" autocomplete="username" required></label>
      <label><span>Password</span><input v-model="loginPassword" type="password" autocomplete="current-password" required></label>
      <p v-if="loginError" class="login-error">{{ loginError }}</p>
      <button class="login-submit" type="submit" :disabled="loginBusy">{{ loginBusy ? 'Signing in…' : 'Sign in' }}</button>
    </form>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { BACKEND_IDS, BACKEND_OPTIONS, addKnowledge, backendMeta, createInitialSettings, requestAdminLogin, requestAdminOverview, requestChat, requestHealth, requestKnowledgeStats, requestMonitor, requestSearch, saveSettings, uploadKnowledge } from './lib/backends'
import { customerDocuments } from './content/customerDocuments'

const CONVERSATION_SESSION_KEY = 'support.conversation.session'
const LEGACY_CONVERSATION_HISTORY_KEY = 'support.conversation.history'
const MAX_SAVED_CONVERSATIONS = 30
const settings = reactive(createInitialSettings())
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const pendingConversationId = ref('')
const messageList = ref(null)
const messageInput = ref(null)
const conversationHistory = ref(readConversationHistory())
const activeConversationId = ref('')
const activeView = ref('support')
const selectedDocumentId = ref('workflow')
const showLogin = ref(false)
const loginUsername = ref('')
const loginPassword = ref('')
const loginError = ref('')
const loginBusy = ref(false)
const initialAdminSession = readAdminSession(settings.backend, true)
if (!sessionStorage.getItem(adminSessionKey(settings.backend, 'token')) && initialAdminSession.token) {
  saveAdminSession(settings.backend, initialAdminSession.token, initialAdminSession.username)
}
const adminToken = ref(initialAdminSession.token)
const adminUsername = ref(initialAdminSession.username)
const adminBusy = ref(false)
const adminError = ref('')
const healthOk = ref(false)
const healthLabel = ref('Not checked')
const knowledgeCount = ref('-')
const alertCount = ref(0)
const statusText = ref('')
const searchQuery = ref('How long does a refund take to arrive?')
const searchResults = ref([])
const docTitle = ref('Additional Refund Policy')
const docContent = ref('During major promotions, refund reviews may take 3–5 business days.')

const currentBackend = computed(() => backendMeta(settings.backend, settings))
// Every backend's docs are listed regardless of the selected backend; each opens in its own
// named tab, so all three can be kept open side by side and a second click reuses that tab.
const docsLinks = computed(() => BACKEND_OPTIONS.map(({ id, label }) => ({ id, label, url: `${backendMeta(id, settings).baseUrl}/docs` })))
const selectedDocument = computed(() => customerDocuments.find((document) => document.id === selectedDocumentId.value) || customerDocuments[0])
const suggestions = [
  { title: 'Order and delivery', description: 'Track an order, check delivery, or update an address.', prompt: 'I need help with my order or delivery.' },
  { title: 'Refunds and billing', description: 'Ask about a refund, charge, invoice, or subscription.', prompt: 'I need help with a refund or billing issue.' },
  { title: 'Account and security', description: 'Resolve sign-in, password, or account-access issues.', prompt: 'I need help accessing or securing my account.' },
  { title: 'Technical help', description: 'Troubleshoot an error, app issue, or connection problem.', prompt: 'I am experiencing a technical issue.' }
]

onMounted(() => {
  localStorage.removeItem(LEGACY_CONVERSATION_HISTORY_KEY)
  if (!settings.userId || settings.userId === 'u1001') {
    settings.userId = `web-${crypto.randomUUID().slice(0, 8)}`
    persist()
  }
  if (conversationHistory.value[0]) selectConversation(conversationHistory.value[0].id)
})

function persist() { saveSettings(settings) }
function readConversationHistory() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(CONVERSATION_SESSION_KEY) || '[]')
    return Array.isArray(saved) ? saved.filter((item) => item && item.id && Array.isArray(item.messages)).slice(0, MAX_SAVED_CONVERSATIONS) : []
  } catch { return [] }
}

function persistConversationHistory() {
  sessionStorage.setItem(CONVERSATION_SESSION_KEY, JSON.stringify(conversationHistory.value))
}

function saveConversation() {
  if (!messages.value.length) return
  const firstUserMessage = messages.value.find((item) => item.role === 'user')
  const id = activeConversationId.value || crypto.randomUUID()
  const entry = {
    id,
    title: conversationTitle(firstUserMessage?.content),
    updatedAt: Date.now(),
    conversationId: settings.conversationId || '',
    messages: messages.value.map(({ id: messageId, role, content }) => ({ id: messageId, role, content }))
  }
  activeConversationId.value = id
  conversationHistory.value = [entry, ...conversationHistory.value.filter((item) => item.id !== id)].slice(0, MAX_SAVED_CONVERSATIONS)
  persistConversationHistory()
}

function appendToConversation(id, message, conversationId = '') {
  const conversation = conversationHistory.value.find((item) => item.id === id)
  if (!conversation) return
  const updated = {
    ...conversation,
    conversationId: conversationId || conversation.conversationId,
    updatedAt: Date.now(),
    messages: [...conversation.messages, message]
  }
  conversationHistory.value = [updated, ...conversationHistory.value.filter((item) => item.id !== id)]
  persistConversationHistory()
  if (activeConversationId.value !== id) return
  messages.value = updated.messages.map((item) => ({ ...item }))
  if (conversationId) { settings.conversationId = conversationId; persist() }
}

function conversationTitle(content) {
  const cleaned = (content || 'New conversation').replace(/\s+/g, ' ').trim()
  return cleaned.length > 42 ? `${cleaned.slice(0, 42)}…` : cleaned
}

function selectConversation(id) {
  const conversation = conversationHistory.value.find((item) => item.id === id)
  if (!conversation) return
  activeConversationId.value = id
  messages.value = conversation.messages.map((item) => ({ ...item }))
  settings.conversationId = conversation.conversationId || ''
  persist()
  scrollToLatest()
  focusComposer()
}

function deleteConversation(id) {
  conversationHistory.value = conversationHistory.value.filter((item) => item.id !== id)
  persistConversationHistory()
  if (activeConversationId.value === id) startNewConversation()
}

function formatConversationTime(updatedAt) {
  const date = new Date(updatedAt)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  if (date.toDateString() === now.toDateString()) return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return date.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

function startNewConversation() { messages.value = []; draft.value = ''; activeConversationId.value = ''; settings.conversationId = ''; persist(); focusComposer() }
function useSuggestion(prompt) { draft.value = prompt; focusComposer() }
function focusComposer() { nextTick(() => messageInput.value?.focus()) }
function returnToSupport() { activeView.value = 'support'; startNewConversation() }
function openDocuments() { activeView.value = 'documents' }
function closeDocuments() { activeView.value = 'support' }
function closeLogin() { showLogin.value = false; loginError.value = ''; loginPassword.value = '' }
function adminSessionKey(type, field) { return `support.admin.${type}.${field}` }
function readAdminSession(type, includeLegacy = false) {
  const token = sessionStorage.getItem(adminSessionKey(type, 'token')) || ''
  const username = sessionStorage.getItem(adminSessionKey(type, 'username')) || ''
  if (token || username || !includeLegacy) return { token, username }
  const legacyToken = sessionStorage.getItem('support.admin.token') || ''
  const legacyUsername = sessionStorage.getItem('support.admin.username') || ''
  return { token: legacyToken, username: legacyUsername }
}
function saveAdminSession(type, token, username) {
  sessionStorage.setItem(adminSessionKey(type, 'token'), token)
  sessionStorage.setItem(adminSessionKey(type, 'username'), username)
  sessionStorage.removeItem('support.admin.token')
  sessionStorage.removeItem('support.admin.username')
}
function restoreAdminSession(type) {
  const session = readAdminSession(type)
  adminToken.value = session.token
  adminUsername.value = session.username
}
function openAdmin() { if (adminToken.value) { activeView.value = 'admin'; loadAdminData() } else { showLogin.value = true } }
function clearAdminSession(type = settings.backend) { sessionStorage.removeItem(adminSessionKey(type, 'token')); sessionStorage.removeItem(adminSessionKey(type, 'username')); adminToken.value = ''; adminUsername.value = '' }
function isExpiredAdminSession(error) { return /(^|\s)401\b|administrator session has expired/i.test(error?.message || '') }
function requireAdminLogin() { clearAdminSession(); activeView.value = 'support'; loginPassword.value = ''; loginError.value = 'Your administrator session has expired. Please sign in again.'; showLogin.value = true }

async function login() {
  loginBusy.value = true; loginError.value = ''
  try {
    const username = loginUsername.value.trim()
    const password = loginPassword.value
    // Sign in to every backend at once; one that is down or rejects the credentials does not block the others.
    const outcomes = await Promise.allSettled(BACKEND_IDS.map(async (type) => ({
      type,
      result: await requestAdminLogin(type, settings, username, password)
    })))
    const sessions = outcomes.filter((outcome) => outcome.status === 'fulfilled').map((outcome) => outcome.value)
    sessions.forEach(({ type, result }) => saveAdminSession(type, result.access_token, result.username))
    const currentSession = sessions.find(({ type }) => type === settings.backend)?.result
    if (!currentSession) throw new Error('current backend rejected the sign-in')
    adminToken.value = currentSession.access_token
    adminUsername.value = currentSession.username
    closeLogin(); activeView.value = 'admin'; await loadAdminData()
  } catch { loginError.value = `Sign-in failed for the ${currentBackend.value.label} backend. Check the credentials and service status.` } finally { loginBusy.value = false }
}

function logout() { clearAdminSession(); activeView.value = 'support'; startNewConversation() }

async function sendMessage() {
  const content = draft.value.trim(); if (!content || busy.value) return
  messages.value.push({ id: crypto.randomUUID(), role: 'user', content }); draft.value = ''; saveConversation()
  const targetConversationId = activeConversationId.value
  const requestSettings = { ...settings, endpoints: { ...settings.endpoints }, conversationId: settings.conversationId }
  busy.value = true; pendingConversationId.value = targetConversationId; await scrollToLatest()
  try {
    const response = await requestChat(settings.backend, requestSettings, content)
    appendToConversation(targetConversationId, { id: crypto.randomUUID(), role: 'assistant', content: response.response || 'I am sorry, but I could not prepare a response. Please try again.' }, response.conversationId)
  } catch { appendToConversation(targetConversationId, { id: crypto.randomUUID(), role: 'assistant', content: 'We are having trouble connecting to support right now. Please try again in a moment.' }) } finally {
    busy.value = false; pendingConversationId.value = ''
    if (activeConversationId.value === targetConversationId) { await scrollToLatest(); focusComposer() }
  }
}

async function scrollToLatest() { await nextTick(); messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' }) }

function escapeHtml(value) {
  return String(value || '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character])
}

function renderMarkdown(value) {
  const codeBlocks = []
  const source = escapeHtml(value).replace(/```([a-zA-Z0-9_-]*)\r?\n([\s\S]*?)```/g, (_, language, code) => {
    const index = codeBlocks.push(`<pre><code${language ? ` class="language-${language}"` : ''}>${code.trim()}</code></pre>`) - 1
    return `@@CODE_BLOCK_${index}@@`
  })
  const output = []
  let paragraph = []
  let listType = ''
  const flushParagraph = () => {
    if (paragraph.length) output.push(`<p>${paragraph.join('<br>')}</p>`)
    paragraph = []
  }
  const closeList = () => {
    if (listType) output.push(`</${listType}>`)
    listType = ''
  }
  const inline = (line) => {
    const inlineCode = []
    let rendered = line.replace(/`([^`]+)`/g, (_, code) => `@@INLINE_CODE_${inlineCode.push(code) - 1}@@`)
    rendered = rendered
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^_]+)__/g, '<strong>$1</strong>')
      .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>')
      .replace(/(?<!_)_([^_]+)_(?!_)/g, '<em>$1</em>')
    return rendered.replace(/@@INLINE_CODE_(\d+)@@/g, (_, index) => `<code>${inlineCode[Number(index)]}</code>`)
  }
  for (const rawLine of source.split(/\r?\n/)) {
    if (/^@@CODE_BLOCK_\d+@@$/.test(rawLine)) { flushParagraph(); closeList(); output.push(rawLine); continue }
    if (!rawLine.trim()) { flushParagraph(); closeList(); continue }
    const heading = rawLine.match(/^(#{1,3})\s+(.+)$/)
    if (heading) { flushParagraph(); closeList(); const level = heading[1].length; output.push(`<h${level}>${inline(heading[2])}</h${level}>`); continue }
    const quote = rawLine.match(/^>\s?(.*)$/)
    if (quote) { flushParagraph(); closeList(); output.push(`<blockquote>${inline(quote[1])}</blockquote>`); continue }
    const ordered = rawLine.match(/^\d+\.\s+(.+)$/)
    const unordered = rawLine.match(/^[-*+]\s+(.+)$/)
    if (ordered || unordered) {
      flushParagraph()
      const nextType = ordered ? 'ol' : 'ul'
      if (listType && listType !== nextType) closeList()
      if (!listType) { listType = nextType; output.push(`<${listType}>`) }
      output.push(`<li>${inline((ordered || unordered)[1])}</li>`)
      continue
    }
    closeList()
    paragraph.push(inline(rawLine))
  }
  flushParagraph(); closeList()
  return output.join('\n').replace(/@@CODE_BLOCK_(\d+)@@/g, (_, index) => codeBlocks[Number(index)] || '')
}

async function loadAdminData() {
  if (!adminToken.value) return
  adminBusy.value = true; adminError.value = ''
  try {
    const [health, stats, monitor, overview] = await Promise.all([
      requestHealth(settings.backend, settings),
      requestKnowledgeStats(settings.backend, settings, adminToken.value),
      requestMonitor(settings.backend, settings, adminToken.value),
      requestAdminOverview(settings.backend, settings, adminToken.value)
    ])
    healthOk.value = health.status === 'ok'; healthLabel.value = health.status || 'ok'; knowledgeCount.value = stats.total_chunks ?? stats.totalChunks ?? overview.knowledge_chunks ?? '-'; alertCount.value = Array.isArray(overview.alerts) ? overview.alerts.length : 0; statusText.value = JSON.stringify(monitor, null, 2)
  } catch (error) {
    if (isExpiredAdminSession(error)) { requireAdminLogin(); return }
    healthOk.value = false; healthLabel.value = 'Unavailable'; adminError.value = error.message || 'Could not load the administrator workspace.'; statusText.value = adminError.value
  } finally { adminBusy.value = false }
}

async function switchBackend(type) {
  settings.backend = type; persist(); restoreAdminSession(type)
  healthOk.value = false; healthLabel.value = 'Not checked'; searchResults.value = []
  if (adminToken.value) await loadAdminData()
  else if (activeView.value === 'admin') showLogin.value = true
}
async function searchKnowledge() { adminBusy.value = true; adminError.value = ''; try { const data = await requestSearch(settings.backend, settings, searchQuery.value, 5, adminToken.value); searchResults.value = data.results || [] } catch (error) { if (isExpiredAdminSession(error)) requireAdminLogin(); else adminError.value = error.message } finally { adminBusy.value = false } }
async function submitKnowledge() { adminBusy.value = true; adminError.value = ''; try { const data = await addKnowledge(settings.backend, settings, [{ title: docTitle.value.trim(), content: docContent.value.trim() }], adminToken.value); statusText.value = JSON.stringify(data, null, 2); await loadAdminData() } catch (error) { if (isExpiredAdminSession(error)) requireAdminLogin(); else adminError.value = error.message } finally { adminBusy.value = false } }
async function handleUpload(event) { const file = event.target.files?.[0]; event.target.value = ''; if (!file) return; adminBusy.value = true; adminError.value = ''; try { const data = await uploadKnowledge(settings.backend, settings, file, adminToken.value); statusText.value = JSON.stringify(data, null, 2); await loadAdminData() } catch (error) { if (isExpiredAdminSession(error)) requireAdminLogin(); else adminError.value = error.message } finally { adminBusy.value = false } }
</script>
