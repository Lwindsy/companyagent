import { isBackendId, validEndpoint } from '../api/backend'
import { readStorage, record, textValue } from './storage'
import type { Conversation, Message, RequestContext } from '../types'

export const HISTORY_KEY = 'support.conversation.session'
export const MAX_CONVERSATIONS = 30

export function readConversations(fallback: RequestContext): Conversation[] {
  const saved = readStorage('sessionStorage', HISTORY_KEY)
  if (!Array.isArray(saved)) return []
  return saved
    .flatMap((value) => {
      const item = record(value)
      if (typeof item.id !== 'string' || !Array.isArray(item.messages)) return []
      const context = record(item.context)
      const messages: Message[] = item.messages.flatMap((value) => {
        const message = record(value)
        if (!['user', 'assistant'].includes(String(message.role)) || typeof message.content !== 'string')
          return []
        return [
          {
            id: textValue(message.id, crypto.randomUUID()),
            role: message.role as Message['role'],
            content: message.content,
            status:
              message.status === 'pending'
                ? 'cancelled'
                : ['sent', 'error', 'cancelled'].includes(String(message.status))
                  ? (message.status as Message['status'])
                  : 'sent',
          },
        ]
      })
      return [
        {
          id: item.id,
          title: textValue(item.title, 'Untitled conversation'),
          updatedAt: typeof item.updatedAt === 'number' ? item.updatedAt : Date.now(),
          conversationId: textValue(item.conversationId),
          messages,
          context:
            isBackendId(context.backend) &&
            typeof context.baseUrl === 'string' &&
            validEndpoint(context.baseUrl)
              ? {
                  backend: context.backend,
                  baseUrl: context.baseUrl,
                  userId: textValue(context.userId, fallback.userId),
                }
              : fallback,
        },
      ]
    })
    .slice(0, MAX_CONVERSATIONS)
}

export const conversationTitle = (content: string) => content.replace(/\s+/g, ' ').trim().slice(0, 54)

export function updateConversation(
  items: Conversation[],
  id: string,
  transform: (conversation: Conversation) => Conversation,
) {
  // A late response must never recreate a conversation that has been deleted.
  return items
    .map((item) => (item.id === id ? transform(item) : item))
    .sort((a, b) => b.updatedAt - a.updatedAt)
}
