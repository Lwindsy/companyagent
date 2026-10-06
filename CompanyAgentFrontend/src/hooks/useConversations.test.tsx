import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { requestChat } from '../api/backend'
import { readConversations } from '../lib/conversations'
import { useConversations } from './useConversations'
import type { ChatResponse } from '../types'

vi.mock('../api/backend', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  requestChat: vi.fn(),
}))
const context = { backend: 'python' as const, baseUrl: '/api/python', userId: 'u' }
const answer: ChatResponse = {
  response: 'Hello',
  conversationId: 'server',
  agentType: 'general',
  escalated: false,
  latencyMs: 1,
  knowledgeUsed: false,
}

describe('conversation lifecycle', () => {
  it('ignores repeated synchronous submissions before the next render', async () => {
    vi.mocked(requestChat).mockResolvedValue(answer)
    const { result } = renderHook(() => useConversations(context))
    await act(async () => {
      await Promise.all([result.current.send('Once'), result.current.send('Once')])
    })
    expect(requestChat).toHaveBeenCalledTimes(1)
    expect(result.current.conversations).toHaveLength(1)
  })
  it('routes a late response to its original conversation after starting a new chat', async () => {
    let finish!: (value: ChatResponse) => void
    vi.mocked(requestChat).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const { result } = renderHook(() => useConversations(context))
    act(() => {
      void result.current.send('First')
    })
    const firstId = result.current.activeId
    act(() => result.current.newConversation())
    await act(async () => {
      finish(answer)
    })
    expect(result.current.activeId).toBe('')
    expect(result.current.conversations.find((item) => item.id === firstId)?.messages.at(-1)?.content).toBe(
      'Hello',
    )
  })
  it('does not resurrect deleted conversations', async () => {
    let finish!: (value: ChatResponse) => void
    vi.mocked(requestChat).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const { result } = renderHook(() => useConversations(context))
    act(() => {
      void result.current.send('Delete me')
    })
    act(() => result.current.remove(result.current.activeId))
    await act(async () => {
      finish(answer)
    })
    expect(result.current.conversations).toHaveLength(0)
  })
  it('keeps existing conversations on their original backend', async () => {
    vi.mocked(requestChat).mockResolvedValue(answer)
    const { result, rerender } = renderHook(({ baseUrl }) => useConversations({ ...context, baseUrl }), {
      initialProps: { baseUrl: '/api/python' },
    })
    await act(async () => {
      await result.current.send('First')
    })
    rerender({ baseUrl: '/new-service' })
    await act(async () => {
      await result.current.send('Follow up')
    })
    expect(vi.mocked(requestChat).mock.calls.at(-1)?.[0].baseUrl).toBe('/api/python')
    expect(vi.mocked(requestChat).mock.calls.at(-1)?.[2]).toBe('server')
  })
  it('retries a failed message without duplicating the customer text', async () => {
    vi.mocked(requestChat).mockRejectedValueOnce(new Error('Offline')).mockResolvedValueOnce(answer)
    const { result } = renderHook(() => useConversations(context))
    await act(async () => {
      await result.current.send('Help')
    })
    const message = result.current.active!.messages[0]
    expect(message.status).toBe('error')
    await act(async () => {
      await result.current.send(message.content, message.id)
    })
    await waitFor(() => expect(result.current.active?.messages).toHaveLength(2))
  })
  it('marks interrupted persisted requests cancelled on reload', () => {
    sessionStorage.setItem(
      'support.conversation.session',
      JSON.stringify([{ id: '1', messages: [{ id: 'm', role: 'user', content: 'Hi', status: 'pending' }] }]),
    )
    expect(readConversations(context)[0].messages[0].status).toBe('cancelled')
  })
})
