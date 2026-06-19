import { describe, expect, it, vi } from 'vitest'
import { resolveApiBase, resolveWorkflowServerUrl } from './apiConfig'

describe('apiConfig', () => {
  it('normalizes relative API paths for same-origin cookie auth', () => {
    expect(resolveApiBase()).toBe('/api')
    expect(resolveApiBase('/')).toBe('/api')
    expect(resolveApiBase('/api')).toBe('/api')
    expect(resolveWorkflowServerUrl('intus', '/api')).toBe('/api/intus')
  })

  it('ignores cross-origin API URLs because cookies are same-origin', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    expect(resolveApiBase('http://localhost:8000')).toBe('/api')
    expect(warn).toHaveBeenCalledWith('Ignoring cross-origin VITE_API_URL; cookie-backed auth requires same-origin /api routing.')

    warn.mockRestore()
  })
})
