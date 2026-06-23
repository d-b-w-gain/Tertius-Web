type PerfLevel = 'debug' | 'info' | 'warn' | 'error'
type PerfDetails = Record<string, unknown>

export type PerfEvent = {
  ts: string
  pageMs: number
  level: PerfLevel
  scope: string
  event: string
  hidden: boolean
  url: string
  details?: PerfDetails
}

declare global {
  interface Window {
    __TERTIUS_PERF_EVENTS__?: PerfEvent[]
    __TERTIUS_PERF__?: {
      enable: () => void
      disable: () => void
      enabled: () => boolean
      enableConsole: () => void
      disableConsole: () => void
      consoleEnabled: () => boolean
      dump: () => PerfEvent[]
      clear: () => void
      mark: (scope: string, event: string, details?: PerfDetails, level?: PerfLevel) => void
    }
  }
}

const STORAGE_KEY = 'tertius:perf-debug'
const CONSOLE_STORAGE_KEY = 'tertius:perf-console'
const RING_LIMIT = 400
let installed = false
let enabledOverride: boolean | undefined
let consoleEnabledOverride: boolean | undefined

function setDiagnosticsEnabled(value: boolean) {
  enabledOverride = value
  try {
    if (value) {
      localStorage.setItem(STORAGE_KEY, '1')
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  } catch {
    // Local storage can be blocked in hardened/private browser contexts.
  }
}

function applyQueryToggle() {
  try {
    const params = new URLSearchParams(window.location.search)
    const perfParam = params.get('perf')
    const perfConsoleParam = params.get('perfConsole')
    if (perfParam === '1') setDiagnosticsEnabled(true)
    if (perfParam === '0') setDiagnosticsEnabled(false)
    if (perfConsoleParam === '1') setConsoleEnabled(true)
    if (perfConsoleParam === '0') setConsoleEnabled(false)
  } catch {
    // Ignore malformed locations from non-browser test environments.
  }
}

export function isPerfDiagnosticsEnabled() {
  if (enabledOverride !== undefined) return enabledOverride
  try {
    return localStorage.getItem(STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function setConsoleEnabled(value: boolean) {
  consoleEnabledOverride = value
  try {
    if (value) {
      localStorage.setItem(CONSOLE_STORAGE_KEY, '1')
    } else {
      localStorage.removeItem(CONSOLE_STORAGE_KEY)
    }
  } catch {
    // Local storage can be blocked in hardened/private browser contexts.
  }
}

function isPerfConsoleEnabled() {
  if (consoleEnabledOverride !== undefined) return consoleEnabledOverride
  try {
    return localStorage.getItem(CONSOLE_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function currentUrl() {
  try {
    return `${window.location.pathname}${window.location.search}${window.location.hash}`
  } catch {
    return ''
  }
}

function consoleMethod(level: PerfLevel) {
  if (level === 'debug') return console.debug
  if (level === 'info') return console.info
  if (level === 'warn') return console.warn
  return console.error
}

function mirrorEventsForAutomation(events: PerfEvent[]) {
  if (!isPerfDiagnosticsEnabled() || typeof document === 'undefined') return

  let node = document.getElementById('tertius-perf-events') as HTMLScriptElement | null
  if (!node) {
    node = document.createElement('script')
    node.id = 'tertius-perf-events'
    node.type = 'application/json'
    node.setAttribute('data-source', 'tertius-performance-diagnostics')
    document.head.appendChild(node)
  }
  node.textContent = JSON.stringify(events)
}

export function perfLog(scope: string, event: string, details?: PerfDetails, level: PerfLevel = 'debug') {
  const entry: PerfEvent = {
    ts: new Date().toISOString(),
    pageMs: Math.round(performance.now()),
    level,
    scope,
    event,
    hidden: typeof document !== 'undefined' ? document.hidden : false,
    url: currentUrl(),
    details,
  }

  const events = window.__TERTIUS_PERF_EVENTS__ ?? []
  events.push(entry)
  if (events.length > RING_LIMIT) {
    events.splice(0, events.length - RING_LIMIT)
  }
  window.__TERTIUS_PERF_EVENTS__ = events
  mirrorEventsForAutomation(events)

  if (isPerfDiagnosticsEnabled() && isPerfConsoleEnabled()) {
    consoleMethod(level)(`[tertius:perf] ${scope}:${event}`, entry)
  }
}

export function createFrameMonitor(scope: string, intervalMs = 10_000, slowFrameMs = 50) {
  let lastFrameAt = 0
  let windowStartedAt = 0
  let frameCount = 0
  let slowFrameCount = 0
  let maxFrameMs = 0

  return (now = performance.now()) => {
    if (!isPerfDiagnosticsEnabled()) {
      lastFrameAt = now
      return
    }

    if (!lastFrameAt) {
      lastFrameAt = now
      windowStartedAt = now
      return
    }

    const frameMs = now - lastFrameAt
    lastFrameAt = now
    frameCount += 1
    maxFrameMs = Math.max(maxFrameMs, frameMs)
    if (frameMs >= slowFrameMs) {
      slowFrameCount += 1
    }

    if (now - windowStartedAt >= intervalMs) {
      const elapsedSeconds = Math.max((now - windowStartedAt) / 1000, 0.001)
      perfLog(scope, 'frame-summary', {
        averageFps: Math.round((frameCount / elapsedSeconds) * 10) / 10,
        frames: frameCount,
        slowFrames: slowFrameCount,
        maxFrameMs: Math.round(maxFrameMs),
        slowFrameThresholdMs: slowFrameMs,
      }, slowFrameCount > 0 ? 'warn' : 'debug')

      windowStartedAt = now
      frameCount = 0
      slowFrameCount = 0
      maxFrameMs = 0
    }
  }
}

function installLongTaskObserver() {
  if (!('PerformanceObserver' in window)) return

  try {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        perfLog('browser', 'long-task', {
          name: entry.name,
          durationMs: Math.round(entry.duration),
          startTimeMs: Math.round(entry.startTime),
        }, 'warn')
      }
    })
    observer.observe({ entryTypes: ['longtask'] })
  } catch {
    // Some browsers do not expose longtask even when PerformanceObserver exists.
  }
}

function installViteDiagnostics() {
  if (!import.meta.hot) return

  import.meta.hot.on('vite:beforeUpdate', (payload) => {
    perfLog('vite', 'hmr-update', {
      updates: payload.updates.map((update) => ({
        type: update.type,
        path: update.path,
        acceptedPath: update.acceptedPath,
      })),
    }, 'info')
  })

  import.meta.hot.on('vite:beforeFullReload', (payload) => {
    perfLog('vite', 'full-reload', {
      path: payload.path,
    }, 'warn')
  })

  import.meta.hot.on('vite:error', (payload) => {
    perfLog('vite', 'error', {
      message: payload.err.message,
      stack: payload.err.stack,
    }, 'error')
  })
}

export function installPerformanceDiagnostics() {
  if (installed || typeof window === 'undefined') return
  installed = true
  applyQueryToggle()

  window.__TERTIUS_PERF_EVENTS__ = window.__TERTIUS_PERF_EVENTS__ ?? []
  window.__TERTIUS_PERF__ = {
    enable: () => {
      setDiagnosticsEnabled(true)
      perfLog('diagnostics', 'enabled', undefined, 'info')
    },
    disable: () => {
      perfLog('diagnostics', 'disabled', undefined, 'info')
      setDiagnosticsEnabled(false)
    },
    enabled: isPerfDiagnosticsEnabled,
    enableConsole: () => {
      setConsoleEnabled(true)
      perfLog('diagnostics', 'console-enabled', undefined, 'info')
    },
    disableConsole: () => {
      perfLog('diagnostics', 'console-disabled', undefined, 'info')
      setConsoleEnabled(false)
    },
    consoleEnabled: isPerfConsoleEnabled,
    dump: () => [...(window.__TERTIUS_PERF_EVENTS__ ?? [])],
    clear: () => {
      window.__TERTIUS_PERF_EVENTS__ = []
      mirrorEventsForAutomation([])
    },
    mark: perfLog,
  }

  const navigation = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined
  const previousBootAt = Number(sessionStorage.getItem('tertius:perf-last-boot-at') || 0)
  sessionStorage.setItem('tertius:perf-last-boot-at', String(Date.now()))
  perfLog('app', 'boot', {
    navigationType: navigation?.type || 'unknown',
    previousBootAgeMs: previousBootAt ? Date.now() - previousBootAt : null,
    diagnosticsEnabled: isPerfDiagnosticsEnabled(),
  }, 'info')

  window.addEventListener('error', (event) => {
    perfLog('window', 'error', {
      message: event.message,
      filename: event.filename,
      lineno: event.lineno,
      colno: event.colno,
      stack: event.error instanceof Error ? event.error.stack : undefined,
    }, 'error')
  })

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason
    perfLog('window', 'unhandled-rejection', {
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
    }, 'error')
  })

  document.addEventListener('visibilitychange', () => {
    perfLog('document', 'visibility-change', { hidden: document.hidden }, 'info')
  })

  window.addEventListener('pagehide', (event) => {
    perfLog('app', 'pagehide', { persisted: event.persisted }, 'info')
  })

  installLongTaskObserver()
  installViteDiagnostics()
}
