let transientFailureCount = 0
let readonlyBackoffUntil = 0
const readonlyInFlightRequests = new Map<string, Promise<Response>>()
const STALE_TOKEN_REDIRECT_KEY = 'tertius:stale-token-redirecting'
const CSRF_COOKIE_NAME = 'tertius_csrf'

function isReadonlyRequest(init: RequestInit) {
  const method = (init.method || 'GET').toUpperCase()
  return method === 'GET' || method === 'HEAD'
}

function isTransientServerFailure(status: number) {
  return status === 500 || status === 502 || status === 503 || status === 504
}

function readonlyRequestKey(url: string, init: RequestInit) {
  return `${(init.method || 'GET').toUpperCase()} ${url}`
}

function recordTransientFailure() {
  transientFailureCount = Math.min(transientFailureCount + 1, 6)
  const delay = Math.min(30_000, 1_000 * 2 ** (transientFailureCount - 1))
  readonlyBackoffUntil = Date.now() + delay
}

function recordSuccess() {
  transientFailureCount = 0
  readonlyBackoffUntil = 0
  sessionStorage.removeItem(STALE_TOKEN_REDIRECT_KEY)
}

async function isInvalidBearerToken(response: Response) {
  if (response.status !== 401) {
    return false
  }

  try {
    const body = await response.clone().json()
    return body?.detail === 'Invalid bearer token'
  } catch {
    return false
  }
}

async function isSessionInvalid() {
  try {
    const response = await fetch('/api/auth/me', { credentials: 'same-origin' })
    return response.status === 401
  } catch {
    return false
  }
}

async function shouldForceFreshLogin(response: Response) {
  if (await isInvalidBearerToken(response)) {
    return true
  }
  if (response.status !== 401) {
    return false
  }
  return isSessionInvalid()
}

async function forceFreshLogin() {
  if (sessionStorage.getItem(STALE_TOKEN_REDIRECT_KEY) === 'true') {
    return
  }

  sessionStorage.setItem(STALE_TOKEN_REDIRECT_KEY, 'true')

  window.location.assign(`/api/auth/login?return_to=${encodeURIComponent(window.location.pathname + window.location.search + window.location.hash)}`)
}

function readCookie(name: string) {
  const prefix = `${name}=`
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length)
}

function backoffResponse() {
  const retryAfterSeconds = Math.max(1, Math.ceil((readonlyBackoffUntil - Date.now()) / 1000))
  return new Response(JSON.stringify({
    error: 'Backend is recovering; polling paused briefly.',
    retryAfterSeconds,
  }), {
    status: 503,
    statusText: 'Service Unavailable',
    headers: {
      'Content-Type': 'application/json',
      'Retry-After': String(retryAfterSeconds),
    },
  })
}

export async function apiFetch(
  url: string,
  _getAccessToken: () => Promise<string>,
  init: RequestInit = {},
) {
  const readonly = isReadonlyRequest(init)
  if (readonly && Date.now() < readonlyBackoffUntil) {
    return backoffResponse()
  }

  const key = readonly ? readonlyRequestKey(url, init) : undefined
  const inFlight = key ? readonlyInFlightRequests.get(key) : undefined
  if (inFlight) {
    return inFlight.then((response) => response.clone())
  }

  const request = (async () => {
    const headers = new Headers(init.headers)
    if (init.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
    if (!readonly && !headers.has('X-CSRF-Token')) {
      const csrfToken = readCookie(CSRF_COOKIE_NAME)
      if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
    }

    let response: Response
    try {
      response = await fetch(url, { ...init, headers, credentials: 'same-origin' })
    } catch (error) {
      if (readonly) {
        recordTransientFailure()
      }
      throw error
    }

    if (readonly && isTransientServerFailure(response.status)) {
      recordTransientFailure()
    } else if (await shouldForceFreshLogin(response)) {
      void forceFreshLogin()
    } else if (response.ok) {
      recordSuccess()
    }

    return response
  })()

  if (key) {
    readonlyInFlightRequests.set(key, request)
    request.then(
      () => readonlyInFlightRequests.delete(key),
      () => readonlyInFlightRequests.delete(key),
    )
  }

  return request.then((response) => response.clone())
}
