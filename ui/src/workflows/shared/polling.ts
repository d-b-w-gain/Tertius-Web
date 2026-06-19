const JITTER_FRACTION = 0.1

export const ACTIVE_PROJECT_POLL_INTERVAL_MS = 30_000
export const FILE_STATUS_POLL_INTERVAL_MS = 15_000
export const MODEL_STATUS_POLL_INTERVAL_MS = 30_000
export const PROJECT_DATA_POLL_INTERVAL_MS = 40_000

export function shouldRunPollingRequest() {
  return typeof document === 'undefined' || !document.hidden
}

export function getPollingDelay(baseMs: number) {
  return baseMs + Math.floor(baseMs * JITTER_FRACTION * Math.random())
}
