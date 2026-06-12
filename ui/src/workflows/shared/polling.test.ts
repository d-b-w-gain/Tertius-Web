import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getPollingDelay, shouldRunPollingRequest } from './polling'

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
})
