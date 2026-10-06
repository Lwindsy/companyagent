export type BackendId = 'python' | 'java' | 'dotnet'
export type View = 'support' | 'guides' | 'admin'

export interface BackendSettings {
  backend: BackendId
  userId: string
  endpoints: Record<BackendId, string>
}

export interface RequestContext {
  backend: BackendId
  baseUrl: string
  userId: string
}

export interface ChatResponse {
  conversationId: string
  response: string
  agentType: string
  escalated: boolean
  knowledgeUsed: boolean
  latencyMs: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  status?: 'pending' | 'sent' | 'error' | 'cancelled'
  meta?: Pick<ChatResponse, 'agentType' | 'escalated' | 'knowledgeUsed' | 'latencyMs'>
}

export interface Conversation {
  id: string
  title: string
  updatedAt: number
  conversationId: string
  context: RequestContext
  messages: Message[]
}

export interface AdminSession {
  token: string
  username: string
  baseUrl: string
}
export interface KnowledgeDocument {
  title: string
  content: string
}
export interface SearchResult extends KnowledgeDocument {
  id: string
  score: number | null
}
export type JsonRecord = Record<string, unknown>

export interface CustomerDocument {
  id: string
  label: string
  eyebrow: string
  title: string
  intro: string
  sections: { number: string; title: string; detail: string; items: { title: string; detail: string }[] }[]
}
