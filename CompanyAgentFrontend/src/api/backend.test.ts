import { describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  createInitialSettings,
  requestChat,
  requestJson,
  requestSearch,
  validEndpoint,
} from './backend'

describe('backend contract', () => {
  it.each(['python', 'java', 'dotnet'] as const)(
    'sends the %s conversation key and normalizes the response',
    async (backend) => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            conv_id: 'server-1',
            response: 'Hello',
            agent_type: 'billing',
            knowledge_used: true,
          }),
        ),
      )
      vi.stubGlobal('fetch', fetchMock)
      const result = await requestChat(
        { backend, baseUrl: '/api/test/', userId: 'customer' },
        'Refund?',
        'previous',
      )
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
        message: 'Refund?',
        user_id: 'customer',
        [backend === 'python' ? 'conv_id' : 'conversation_id']: 'previous',
      })
      expect(fetchMock.mock.calls[0][0]).toBe('/api/test/chat')
      expect(result).toMatchObject({ conversationId: 'server-1', agentType: 'billing', knowledgeUsed: true })
    },
  )
  it('preserves HTTP status for expired sessions without exposing HTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>bad gateway</html>', { status: 401 })),
    )
    await expect(requestJson('/api', '/monitor')).rejects.toMatchObject({ status: 401 })
  })
  it('does not accept empty chat replies', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')))
    await expect(
      requestChat({ backend: 'java', baseUrl: '/api/java', userId: 'a' }, 'Hello', ''),
    ).rejects.toBeInstanceOf(ApiError)
  })
  it('encodes search and sends backend-scoped credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"results":[]}'))
    vi.stubGlobal('fetch', fetchMock)
    await requestSearch('/api/dotnet', 'scoped-token', 'refund & bill')
    expect(fetchMock.mock.calls[0][0]).toContain('query=refund+%26+bill')
    expect(fetchMock.mock.calls[0][1].headers.get('Authorization')).toBe('Bearer scoped-token')
  })
  it('recovers from corrupted persisted configuration', () => {
    localStorage.setItem(
      'console.frontend.settings',
      '{"backend":"wrong","endpoints":{"python":"javascript:alert(1)"}}',
    )
    expect(createInitialSettings()).toMatchObject({ backend: 'python', endpoints: { python: '/api/python' } })
    expect(validEndpoint('//untrusted.test')).toBe(false)
    expect(validEndpoint('https://example.com/api')).toBe(true)
  })
})
