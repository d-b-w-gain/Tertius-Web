import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DraftingTab } from './DraftingTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'authenticated', getAccessToken: mocks.getAccessToken }),
}))

describe('DraftingTab inactive state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not poll project state while hidden', async () => {
    render(<DraftingTab serverUrl="/api/timus" isActive={false} />)

    await act(async () => {})

    expect(mocks.apiFetch).not.toHaveBeenCalled()
    expect(mocks.getAccessToken).not.toHaveBeenCalled()
  })
})
