import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ACTIVE_PROJECT_POLL_INTERVAL_MS,
  FILE_STATUS_POLL_INTERVAL_MS,
  MODEL_STATUS_POLL_INTERVAL_MS,
  PROJECT_DATA_POLL_INTERVAL_MS,
  getPollingDelay,
  shouldRunPollingRequest,
} from './polling'

describe('polling helpers', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('pauses polling while the browser tab is hidden', () => {
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)

    expect(shouldRunPollingRequest()).toBe(false)
  })

  it('adds bounded jitter to polling intervals', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)

    expect(getPollingDelay(2000)).toBe(2100)
  })

  it('uses slower idle polling intervals for background synchronization', () => {
    expect(ACTIVE_PROJECT_POLL_INTERVAL_MS).toBeGreaterThanOrEqual(20_000)
    expect(FILE_STATUS_POLL_INTERVAL_MS).toBeGreaterThanOrEqual(10_000)
    expect(MODEL_STATUS_POLL_INTERVAL_MS).toBeGreaterThanOrEqual(30_000)
    expect(PROJECT_DATA_POLL_INTERVAL_MS).toBeGreaterThanOrEqual(40_000)
  })
})
