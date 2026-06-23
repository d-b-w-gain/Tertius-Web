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

  it('lists authenticated LLM edit conversation history', async () => {
    const responseBody = {
      messages: [
        {
          job_id: 'llm-job-1',
          prompt: 'make a bracket',
          content: 'Updated 1 file.',
          created_at: '2026-06-19T00:00:00Z',
          status: 'succeeded',
          usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 },
          files: [{ filename: 'design.py', summary: 'Changed bracket.', changed: true }],
          compile: {
            job_id: 'compile-job-1',
            status: 'succeeded',
            artifact_id: 'artifact-1',
            export_format: 'glb',
          },
        },
      ],
    }
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify(responseBody)))
    const getAccessToken = vi.fn()
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    await expect(storage.listLlmEditConversation('demo')).resolves.toEqual(responseBody.messages)
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/projects/demo/files/llm-edit/jobs', getAccessToken)
  })

  it('surfaces authenticated LLM edit conversation list failures', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'history unavailable' }), { status: 503 }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.listLlmEditConversation('demo')).rejects.toThrow('history unavailable')
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

  it('returns an empty LLM edit conversation for guest projects', async () => {
    const storage = createProjectStorage({
      authMode: 'guest',
      serverUrl: '/proxy/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.listLlmEditConversation('demo')).resolves.toEqual([])
    expect(mocks.apiFetch).not.toHaveBeenCalled()
  })
})
