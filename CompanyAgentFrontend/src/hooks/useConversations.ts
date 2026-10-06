import { useEffect, useRef, useState } from 'react'
import { requestChat } from '../api/backend'
import {
  conversationTitle,
  HISTORY_KEY,
  MAX_CONVERSATIONS,
  readConversations,
  updateConversation,
} from '../lib/conversations'
import { removeStorage, writeStorage } from '../lib/storage'
import type { Message, RequestContext } from '../types'

export function useConversations(defaultContext: RequestContext) {
  const [conversations, setConversations] = useState(() => readConversations(defaultContext))
  const [activeId, setActiveId] = useState(() => conversations[0]?.id || '')
  const activeIdRef = useRef(activeId)
  const [newConversationKey, setNewConversationKey] = useState(0)
  const [storageAvailable, setStorageAvailable] = useState(true)
  const controllers = useRef(new Map<string, AbortController>())
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations

  useEffect(() => {
    removeStorage('localStorage', 'support.conversation.history')
    setStorageAvailable(writeStorage('sessionStorage', HISTORY_KEY, conversations))
  }, [conversations])
  useEffect(() => {
    const requests = controllers.current
    return () => {
      requests.forEach((controller) => controller.abort())
      requests.clear()
    }
  }, [])

  const active = conversations.find((item) => item.id === activeId)
  const pending = active?.messages.some((message) => message.status === 'pending') || false

  async function send(content: string, retryMessageId?: string) {
    const messageText = content.trim()
    if (!messageText) return
    const existing = conversationsRef.current.find((item) => item.id === activeIdRef.current)
    const id = existing?.id || activeIdRef.current || crypto.randomUUID()
    if (controllers.current.has(id)) return
    // Existing conversations retain their original service and customer identity.
    const context = existing?.context || { ...defaultContext }
    const messageId = retryMessageId || crypto.randomUUID()
    const userMessage: Message = { id: messageId, role: 'user', content: messageText, status: 'pending' }
    const controller = new AbortController()
    controllers.current.set(id, controller)
    activeIdRef.current = id
    setActiveId(id)
    setConversations((items) => {
      if (existing)
        return updateConversation(items, id, (item) => ({
          ...item,
          updatedAt: Date.now(),
          messages: retryMessageId
            ? item.messages.map((message) => (message.id === retryMessageId ? userMessage : message))
            : [...item.messages, userMessage],
        }))
      // Never evict a conversation with a request in flight.
      const retained = items.filter((item) => controllers.current.has(item.id))
      const rest = items
        .filter((item) => !controllers.current.has(item.id))
        .slice(0, Math.max(0, MAX_CONVERSATIONS - retained.length - 1))
      return [
        {
          id,
          context,
          title: conversationTitle(messageText),
          conversationId: '',
          updatedAt: Date.now(),
          messages: [userMessage],
        },
        ...retained,
        ...rest,
      ]
    })
    try {
      const result = await requestChat(
        context,
        messageText,
        existing?.conversationId || '',
        controller.signal,
      )
      if (controller.signal.aborted) return
      setConversations((items) =>
        updateConversation(items, id, (item) => ({
          ...item,
          conversationId: result.conversationId || item.conversationId,
          updatedAt: Date.now(),
          messages: [
            ...item.messages.map((message) =>
              message.id === messageId ? { ...message, status: 'sent' as const } : message,
            ),
            { id: crypto.randomUUID(), role: 'assistant', content: result.response, meta: result },
          ],
        })),
      )
    } catch {
      setConversations((items) =>
        updateConversation(items, id, (item) => ({
          ...item,
          messages: item.messages.map((message) =>
            message.id === messageId
              ? { ...message, status: controller.signal.aborted ? 'cancelled' : 'error' }
              : message,
          ),
        })),
      )
    } finally {
      if (controllers.current.get(id) === controller) controllers.current.delete(id)
    }
  }

  function remove(id: string) {
    controllers.current.get(id)?.abort()
    controllers.current.delete(id)
    setConversations((items) => items.filter((item) => item.id !== id))
    if (activeIdRef.current === id) select('')
  }

  function select(id: string) {
    activeIdRef.current = id
    setActiveId(id)
  }

  return {
    conversations,
    active,
    activeId,
    newConversationKey,
    pending,
    storageAvailable,
    send,
    remove,
    select,
    newConversation: () => {
      select('')
      setNewConversationKey((value) => value + 1)
    },
    cancel: () => controllers.current.get(activeId)?.abort(),
  }
}

export type ConversationController = ReturnType<typeof useConversations>
