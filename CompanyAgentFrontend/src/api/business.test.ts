import { describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { actionsFrom, businessChat, decideAction, getActions, useBusinessCapabilities } from './business'

// Synthetic credentials and dates exist only inside fetch mocks. No actual customer
// session is opened; the tests inspect URL, headers, JSON, and response normalization.
const session = {
  token: 'customer-token',
  convId: 'server-conv',
  name: 'Anna',
  expiresAt: Date.now() + 10000,
}

describe('business workflow boundary', () => {
  it('preserves request identity on retries and binds auth independently of payload', async () => {
    // vi.stubGlobal replaces fetch. Two identical sends must preserve the body ID
    // and use Authorization separately rather than smuggling identity into JSON.
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response('{"response":"Submitted","actions":[]}'))
    vi.stubGlobal('fetch', fetchMock)
    for (let i = 0; i < 2; i++)
      await businessChat('/api/python', session, 'Refund', 'same-id', new AbortController().signal)
    const first = fetchMock.mock.calls[0][1]
    expect(JSON.parse(first.body)).toEqual({
      message: 'Refund',
      conv_id: 'server-conv',
      request_id: 'same-id',
    })
    expect(first.headers.get('Authorization')).toBe('Bearer customer-token')
    expect(fetchMock.mock.calls[1][1].body).toBe(first.body)
  })
  it('scopes customer queries and uses separate decision URLs', async () => {
    // Query escaping keeps & inside conv_id. Demo and staff decision helpers must
    // choose different routes and carry the credential explicitly supplied to each.
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => new Response('{"items":[],"action":{"id":"a","status":"executed"}}'))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    await getActions('/api/python', 'customer-token', 'conv & one', signal)
    expect(fetchMock.mock.calls[0][0]).toContain('conv_id=conv%20%26%20one')
    await decideAction('/api/python', 'customer-token', 'a', 'approve', 'Verified', true, signal)
    await decideAction('/api/python', 'admin-token', 'a', 'reject', 'Reason', false, signal)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/python/customer/demo/actions/a/decide')
    expect(fetchMock.mock.calls[2][0]).toBe('/api/python/admin/actions/a/decide')
    expect(fetchMock.mock.calls[2][1].headers.get('Authorization')).toBe('Bearer admin-token')
  })
  it('treats missing capabilities as unavailable and does not probe Java', async () => {
    // renderHook exercises real React effect cleanup. Switching off the Python flag
    // must not probe another backend or invent optional capabilities from a health OK.
    const fetchMock = vi.fn().mockResolvedValue(new Response('{"status":"ok"}'))
    vi.stubGlobal('fetch', fetchMock)
    const { result, rerender } = renderHook(({ python }) => useBusinessCapabilities('/api', python), {
      initialProps: { python: true },
    })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(result.current).toEqual([])
    rerender({ python: false })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it('rejects malformed action entries without manufacturing completed operations', () => {
    // Invalid objects are dropped. A partial response must never become a fake success
    // card just because the frontend needs something to render.
    expect(
      actionsFrom([null, {}, { id: 'one' }, { id: 'two', status: 'pending' }]).map((row) => row.status),
    ).toEqual(['pending'])
  })
})
