import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  requestAdminOverview,
  requestHealth,
  requestKnowledgeStats,
  requestMonitor,
} from '../api/backend'
import { useAdminData } from './useAdminData'
import type { JsonRecord } from '../types'

vi.mock('../api/backend', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  requestHealth: vi.fn(),
  requestKnowledgeStats: vi.fn(),
  requestMonitor: vi.fn(),
  requestAdminOverview: vi.fn(),
}))
const session = { token: 'token', baseUrl: '/api/python', username: 'operator' }
function mockHealthy() {
  vi.mocked(requestHealth).mockResolvedValue({ status: 'ok' })
  vi.mocked(requestKnowledgeStats).mockResolvedValue({ total_chunks: 12 })
  vi.mocked(requestMonitor).mockResolvedValue({ requests: 2 })
  vi.mocked(requestAdminOverview).mockResolvedValue({ alerts: [] })
}

describe('admin data consistency', () => {
  it('preserves successful metrics when one endpoint fails', async () => {
    mockHealthy()
    vi.mocked(requestMonitor).mockRejectedValue(new ApiError('Monitor offline', 503))
    const { result } = renderHook(() => useAdminData(session, vi.fn()))
    await waitFor(() => expect(result.current.dashboard.checkedAt).toBeGreaterThan(0))
    expect(result.current.dashboard).toMatchObject({
      health: 'ok',
      chunks: 12,
      monitor: null,
      errors: ['Monitoring: Monitor offline'],
    })
  })
  it('ignores a previous endpoint response after switching services', async () => {
    mockHealthy()
    let finish!: (value: JsonRecord) => void
    vi.mocked(requestHealth).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    const { result, rerender } = renderHook(({ value }) => useAdminData(value, vi.fn()), {
      initialProps: { value: session },
    })
    rerender({ value: { ...session, baseUrl: '/api/java' } })
    await waitFor(() => expect(result.current.dashboard.health).toBe('ok'))
    await act(async () => finish({ status: 'old-service-error' }))
    expect(result.current.dashboard.health).toBe('ok')
  })
  it('expires a 401 session but does not misclassify 403 as expiration', async () => {
    mockHealthy()
    vi.mocked(requestMonitor).mockRejectedValue(new ApiError('Forbidden', 403))
    const expired = vi.fn()
    const { result } = renderHook(() => useAdminData(session, expired))
    await waitFor(() => expect(result.current.dashboard.checkedAt).toBeGreaterThan(0))
    expect(expired).not.toHaveBeenCalled()
    vi.mocked(requestMonitor).mockRejectedValue(new ApiError('Expired', 401))
    await act(async () => result.current.refresh())
    expect(expired).toHaveBeenCalledOnce()
  })
})
