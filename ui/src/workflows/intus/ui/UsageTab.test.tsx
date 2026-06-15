import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UsageTab } from './UsageTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn().mockResolvedValue('test-token'),
}))

vi.mock('../../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

function jsonResponse(data: unknown, ok = true) {
  return {
    ok,
    json: vi.fn().mockResolvedValue(data),
  }
}

function setupApiResponses() {
  mocks.apiFetch.mockImplementation((url: string) => {
    if (url.includes('/usage/summary')) {
      return Promise.resolve(jsonResponse({
        total_jobs: 10,
        total_cost_cents: 500,
        total_compute_seconds: 18000,
        total_artifact_bytes: 1000000,
      }))
    }
    if (url.includes('/usage/daily')) {
      return Promise.resolve(jsonResponse([
        { day: '2026-06-15', job_count: 3, cost_cents: 100, compute_seconds: 3600 },
        { day: '2026-06-14', job_count: 7, cost_cents: 400, compute_seconds: 14400 },
      ]))
    }
    if (url.includes('/usage/by-format')) {
      return Promise.resolve(jsonResponse([
        { export_format: 'stl', job_count: 5, cost_cents: 200, compute_seconds: 7200 },
        { export_format: 'glb', job_count: 5, cost_cents: 300, compute_seconds: 10800 },
      ]))
    }
    if (url.includes('/usage/recent')) {
      return Promise.resolve(jsonResponse([
        {
          created_at: '2026-06-15T10:00:00Z',
          export_format: 'glb',
          status: 'succeeded',
          compute_duration_seconds: 120,
          artifact_byte_size: 5000,
          cost_cents: 7,
          username: 'testuser',
        },
      ]))
    }
    return Promise.resolve({ ok: false, json: vi.fn().mockResolvedValue({}) })
  })
}

describe('UsageTab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('renders summary cards after loading', async () => {
    setupApiResponses()

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    await waitFor(() => {
      expect(screen.getByText('$5.00')).toBeTruthy()
    })

    expect(screen.getByText('10')).toBeTruthy()
    expect(screen.getByText('$5.00')).toBeTruthy()
  })

  it('renders daily chart', async () => {
    setupApiResponses()

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    await waitFor(() => {
      expect(screen.getByText('Daily Cost (last 30 days)')).toBeTruthy()
    })
  })

  it('renders recent jobs table', async () => {
    setupApiResponses()

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    await waitFor(() => {
      expect(screen.getByText('testuser')).toBeTruthy()
    })
  })

  it('shows loading state initially', () => {
    mocks.apiFetch.mockImplementation(() => new Promise(() => {}))

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    expect(screen.getByText('Loading usage data...')).toBeTruthy()
  })

  it('hides on 403 response', async () => {
    mocks.apiFetch.mockResolvedValue({
      ok: false,
      status: 403,
      json: vi.fn().mockResolvedValue({}),
    })

    const { container } = render(
      <UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />
    )

    await waitFor(() => {
      expect(container.innerHTML).toBe('')
    })
  })

  it('handles empty state gracefully', async () => {
    mocks.apiFetch.mockImplementation((url: string) => {
      if (url.includes('/usage/summary')) {
        return Promise.resolve(jsonResponse({
          total_jobs: 0,
          total_cost_cents: 0,
          total_compute_seconds: 0,
          total_artifact_bytes: 0,
        }))
      }
      if (url.includes('/usage/daily')) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url.includes('/usage/by-format')) {
        return Promise.resolve(jsonResponse([]))
      }
      if (url.includes('/usage/recent')) {
        return Promise.resolve(jsonResponse([]))
      }
      return Promise.resolve({ ok: false, json: vi.fn().mockResolvedValue({}) })
    })

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    await waitFor(() => {
      expect(screen.getByText('No data yet')).toBeTruthy()
      expect(screen.getByText('No jobs yet')).toBeTruthy()
    })
  })

  it('shows error state with retry button', async () => {
    mocks.apiFetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: vi.fn().mockResolvedValue({}),
    })

    render(<UsageTab serverUrl="/api/intus" getAccessToken={mocks.getAccessToken} />)

    await waitFor(() => {
      expect(screen.getByText('Failed to load usage data')).toBeTruthy()
      expect(screen.getByText('Retry')).toBeTruthy()
    })
  })
})
