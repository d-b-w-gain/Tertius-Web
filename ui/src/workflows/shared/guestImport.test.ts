import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GUEST_WORKSPACE_KEY, saveGuestWorkspace, type GuestWorkspace } from './guestWorkspace'
import { importGuestWorkspace } from './guestImport'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

const workspace: GuestWorkspace = {
  version: 1,
  activeProject: 'demo',
  projects: {
    demo: {
      activeFile: 'helper.py',
      updatedAt: '2026-06-10T00:00:00.000Z',
      files: {
        'design.py': 'print("design")',
        'helper.py': 'print("helper")',
      },
    },
  },
}

describe('guestImport', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('imports guest files through resolved Intus URLs and returns active-file metadata', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ projects: ['demo', 'demo-guest-20260610-1130'] })))
      .mockResolvedValue(new Response(JSON.stringify({ success: true })))
    saveGuestWorkspace(workspace)

    const result = await importGuestWorkspace({ getAccessToken: vi.fn(), timestamp: () => '20260610-1130' })

    expect(result.importedProjects.demo).toBe('demo-guest-20260610-1130-2')
    expect(result.activeProject).toBe('demo-guest-20260610-1130-2')
    expect(result.activeFile).toBe('helper.py')
    expect(mocks.apiFetch.mock.calls.map((call) => call[0])).toEqual([
      '/api/intus/projects',
      '/api/intus/projects/demo-guest-20260610-1130-2/new',
      '/api/intus/projects/demo-guest-20260610-1130-2/save',
      '/api/intus/projects/demo-guest-20260610-1130-2/save',
      '/api/intus/projects/demo-guest-20260610-1130-2/activate',
    ])
    expect(mocks.apiFetch.mock.calls.some((call) => String(call[0]).includes('/proxy'))).toBe(false)
    expect(mocks.apiFetch.mock.calls[2]![2]).toMatchObject({
      method: 'POST',
      body: JSON.stringify({ file: 'design.py', code: 'print("design")' }),
    })
    expect(localStorage.getItem(GUEST_WORKSPACE_KEY)).toBeNull()
  })

  it('preserves guest localStorage when an upload fails', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ projects: [] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: true })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ success: false }), { status: 500 }))
    saveGuestWorkspace(workspace)

    await expect(importGuestWorkspace({ getAccessToken: vi.fn() })).rejects.toThrow('Failed to save design.py')

    expect(localStorage.getItem(GUEST_WORKSPACE_KEY)).toContain('helper.py')
  })
})
