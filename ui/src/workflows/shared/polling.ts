const JITTER_FRACTION = 0.1

export function shouldRunPollingRequest() {
  return typeof document === 'undefined' || !document.hidden
}

export function getPollingDelay(baseMs: number) {
  return baseMs + Math.floor(baseMs * JITTER_FRACTION * Math.random())
}
