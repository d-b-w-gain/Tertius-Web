import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GUEST_WORKSPACE_KEY } from './guestWorkspace'
import { createProjectStorage } from './projectStorage'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

describe('projectStorage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('stores guest projects and files locally without calling apiFetch', async () => {
    const storage = createProjectStorage({
      authMode: 'guest',
      serverUrl: '/proxy/api/intus',
      getAccessToken: vi.fn(),
    })

    await storage.createProject('demo')
    await storage.saveCode('demo', 'helper.py', 'print("local")')

    expect(await storage.listProjects()).toContain('demo')
    expect(await storage.loadCode('demo', 'helper.py')).toBe('print("local")')
    expect(localStorage.getItem(GUEST_WORKSPACE_KEY)).toContain('helper.py')
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })

  it('delegates authenticated operations to the Intus API', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ projects: ['demo'] })))
    const getAccessToken = vi.fn()
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    expect(await storage.listProjects()).toEqual(['demo'])

    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/projects', getAccessToken)
  })

  it('throws server error messages when authenticated writes fail', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ success: false, error: 'bad file' }), { status: 400 }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.saveCode('demo', 'bad.py', 'print("x")')).rejects.toThrow('bad file')
  })

  it('includes HTTP status when authenticated writes fail without JSON details', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response('<h1>Server Error</h1>', {
      status: 500,
      statusText: 'Internal Server Error',
      headers: { 'Content-Type': 'text/html' },
    }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.createProject('demo')).rejects.toThrow('Failed to create project demo (500 Internal Server Error)')
  })

  it('parses file_metadata from the authenticated list-files response', async () => {
    mocks.apiFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          files: ['design.py', 'helper.py'],
          file_metadata: [
            { id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' },
            { id: 'f-2', filename: 'helper.py' },
          ],
        }),
      ),
    )
    const getAccessToken = vi.fn()
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    expect(await storage.listFileMetadata('demo')).toEqual([
      { id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' },
      { id: 'f-2', filename: 'helper.py' },
    ])
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/projects/demo/files', getAccessToken)
  })

  it('derives id-less metadata from filenames when file_metadata is missing', async () => {
    mocks.apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ files: ['design.py', 'helper.py'] })),
    )
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    expect(await storage.listFileMetadata('demo')).toEqual([
      { id: '', filename: 'design.py' },
      { id: '', filename: 'helper.py' },
    ])
  })

  it('posts to /files/llm-edit and returns the success payload for authenticated edits', async () => {
    const getAccessToken = vi.fn()
    const responseBody = {
      success: true,
      outcome: 'changed',
      message: '',
      model: 'gpt-4',
      usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
      snapshot: { id: 'snap-1', message: 'llm edit', content_hash: 'abc123' },
      files: [
        { id: 'f-1', filename: 'design.py', content: 'print(1)\n', updated_at: '2024-01-02T00:00:00Z', changed: true, summary: 'added print' },
      ],
    }
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify(responseBody)))

    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    const result = await storage.applyLlmFileEdit('demo', {
      prompt: 'add a docstring',
      files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      active_file_id: 'f-1',
      metadata: { source: 'inline' },
    })

    expect(result).toEqual(responseBody)
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/intus/projects/demo/files/llm-edit',
      getAccessToken,
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          prompt: 'add a docstring',
          files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
          active_file_id: 'f-1',
          metadata: { source: 'inline' },
        }),
      }),
    )
  })

  it('rejects guest applyLlmFileEdit with a login prompt', async () => {
    const storage = createProjectStorage({
      authMode: 'guest',
      serverUrl: '/proxy/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(
      storage.applyLlmFileEdit('demo', {
        prompt: 'add a docstring',
        files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      }),
    ).rejects.toThrow('Log in to use AI file edits')
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })
})
