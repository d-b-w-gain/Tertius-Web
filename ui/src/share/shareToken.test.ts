import { afterEach, describe, expect, it } from 'vitest'
import { isSharePage, shareLinkUrl, takeShareToken } from './shareToken'

describe('share link tokens (#417)', () => {
  afterEach(() => {
    sessionStorage.clear()
    window.history.replaceState(null, '', '/')
  })

  it('builds links with the token after the #, out of every request path', () => {
    expect(shareLinkUrl('abc.def', 'https://tertius.example')).toBe('https://tertius.example/share#abc.def')
  })

  it('recognises the share page', () => {
    expect(isSharePage({ pathname: '/share' })).toBe(true)
    expect(isSharePage({ pathname: '/share/' })).toBe(true)
    expect(isSharePage({ pathname: '/' })).toBe(false)
  })

  it('takes the token out of the address bar and keeps it for a reload', () => {
    window.history.replaceState(null, '', '/share#abc.def')

    expect(takeShareToken()).toBe('abc.def')
    expect(window.location.hash).toBe('')
    expect(window.location.pathname).toBe('/share')
    // A reload of the cleaned URL still finds it in this tab.
    expect(takeShareToken()).toBe('abc.def')
  })

  it('has no token for a bare share page', () => {
    window.history.replaceState(null, '', '/share')
    expect(takeShareToken()).toBeNull()
  })
})
