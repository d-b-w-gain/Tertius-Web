import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AiUsageGauge, recordAiUsage } from './AiUsageGauge'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn().mockResolvedValue('test-token'),
}))

vi.mock('../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

vi.mock('../../auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'authenticated',
    getAccessToken: mocks.getAccessToken,
  }),
}))

function jsonResponse(data: unknown, ok = true) {
  return {
    ok,
    json: vi.fn().mockResolvedValue(data),
  }
}

describe('AiUsageGauge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders server token quota when available', async () => {
    mocks.apiFetch.mockResolvedValue(jsonResponse({
      tenant_daily_token_quota: 1000,
      tenant_tokens_used_today: 250,
      tenant_tokens_remaining_today: 750,
      user_daily_token_quota: 500,
      user_tokens_used_today: 100,
      user_tokens_remaining_today: 400,
      last_edit: { model: 'test-model', total_tokens: 25 },
    }))

    render(<AiUsageGauge serverUrl="/api/intus" />)

    expect(await screen.findByText('250 / 1.0k')).toBeInTheDocument()
    expect(screen.getByText('Today')).toBeInTheDocument()
    expect(screen.getByText('test-model')).toBeInTheDocument()
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/llm-usage/today', mocks.getAccessToken)
  })

  it('updates browser-session usage from local edit events', async () => {
    mocks.apiFetch.mockResolvedValue(jsonResponse({}, false))

    render(<AiUsageGauge serverUrl="/api/intus" />)
    recordAiUsage(42)

    await waitFor(() => {
      expect(screen.getByText('42 used')).toBeInTheDocument()
    })
  })
})
