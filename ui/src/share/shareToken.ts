/**
 * Share links (#417) are `/share#<token>`. The token sits after `#` so no
 * server or proxy ever logs it. The share page takes it out of the address
 * bar before telemetry starts (page-load tracing records the URL), keeping
 * it in this tab's sessionStorage so a reload still works.
 */

export const SHARE_PATH = '/share'
export const SHARE_TOKEN_HEADER = 'X-Tertius-Share-Token'
const SESSION_KEY = 'tertius:share-token'

export function shareLinkUrl(token: string, origin = window.location.origin): string {
  return `${origin}${SHARE_PATH}#${token}`
}

export function isSharePage(location: Pick<Location, 'pathname'> = window.location): boolean {
  return location.pathname === SHARE_PATH || location.pathname === `${SHARE_PATH}/`
}

/** The share page's token, removed from the address bar; null if there is none. */
export function takeShareToken(): string | null {
  const fromHash = window.location.hash.replace(/^#/, '').trim()
  if (fromHash) {
    try {
      sessionStorage.setItem(SESSION_KEY, fromHash)
    } catch {
      // Without storage a reload just needs the link again.
    }
    window.history.replaceState(window.history.state, '', SHARE_PATH)
    return fromHash
  }
  try {
    return sessionStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}
