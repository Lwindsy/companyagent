import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { createInitialSettings, requestAdminLogin } from '../api/backend'
import { readAdminSessions, useAdminSession } from './useAdminSession'

vi.mock('../api/backend', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  requestAdminLogin: vi.fn(),
}))

describe('administrator credential boundary', () => {
  it('does not reuse a token for another endpoint or backend', async () => {
    vi.mocked(requestAdminLogin).mockResolvedValue({
      token: 'python-token',
      username: 'operator',
      baseUrl: '/api/python',
    })
    const settings = createInitialSettings()
    const { result, rerender } = renderHook(({ value }) => useAdminSession(value), {
      initialProps: { value: settings },
    })
    await act(async () => result.current.login('operator', 'password', new AbortController().signal))
    expect(result.current.session?.token).toBe('python-token')
    rerender({ value: { ...settings, endpoints: { ...settings.endpoints, python: '/api/other' } } })
    expect(result.current.session).toBeUndefined()
    rerender({ value: { ...settings, backend: 'java' } })
    expect(result.current.session).toBeUndefined()
    rerender({ value: settings })
    expect(result.current.session?.token).toBe('python-token')
    act(() => result.current.logout())
    expect(sessionStorage.getItem('support.admin.sessions.v2')).toBe('{}')
  })
  it('binds legacy credentials to their configured endpoint', () => {
    sessionStorage.setItem('support.admin.python.token', 'legacy-token')
    expect(readAdminSessions(createInitialSettings())['python:/api/python'].token).toBe('legacy-token')
  })
  it('does not save a login response after the dialog was cancelled', async () => {
    vi.mocked(requestAdminLogin).mockResolvedValue({
      token: 'late-token',
      username: 'operator',
      baseUrl: '/api/python',
    })
    const { result } = renderHook(() => useAdminSession(createInitialSettings()))
    const controller = new AbortController()
    controller.abort()
    await act(async () => result.current.login('operator', 'password', controller.signal))
    expect(result.current.session).toBeUndefined()
  })
})
