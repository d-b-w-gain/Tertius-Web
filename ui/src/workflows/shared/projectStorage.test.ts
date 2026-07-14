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

  it('posts to /files/llm-edit/jobs and returns the queued job payload', async () => {
    const getAccessToken = vi.fn()
    const responseBody = {
      success: true,
      job_id: 'llm-job-1',
      status: 'queued',
    }
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify(responseBody), { status: 202 }))

    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    const result = await storage.applyLlmFileEditJob('demo', {
      prompt: 'add a docstring',
      files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      active_file_id: 'f-1',
      metadata: { source: 'inline' },
    })

    expect(result).toEqual(responseBody)
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/intus/projects/demo/files/llm-edit/jobs',
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

  it('fetches LLM edit job status by job id', async () => {
    const getAccessToken = vi.fn()
    const responseBody = {
      job_id: 'llm-job-1',
      status: 'succeeded',
      result: {
        success: true,
        outcome: 'changed',
        message: '',
        model: 'gpt-4',
        usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 },
        snapshot: { id: 'snap-1', message: 'llm edit', content_hash: 'abc123' },
        files: [
          { id: 'f-1', filename: 'design.py', content: 'print(1)\n', updated_at: '2024-01-02T00:00:00Z', changed: true, summary: 'added print' },
        ],
      },
    }
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify(responseBody)))

    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken,
    })

    await expect(storage.getLlmFileEditJob('demo', 'llm-job-1')).resolves.toEqual(responseBody)
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/projects/demo/files/llm-edit/jobs/llm-job-1', getAccessToken)
  })

  it('explains that a 524 LLM edit job submit may still finish after the edge times out', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response('<html>timeout</html>', {
      status: 524,
      statusText: '',
      headers: { 'Content-Type': 'text/html' },
    }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(
      storage.applyLlmFileEditJob('demo', {
        prompt: 'make a bracket',
        files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      }),
    ).rejects.toThrow(
      'LLM file edit failed: the request timed out at the edge. The edit may still be running; refresh the project before retrying.',
    )
  })

  it('surfaces authenticated LLM edit job status failures', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify({ error: 'LLM edit job not found' }), { status: 404 }))
    const storage = createProjectStorage({
      authMode: 'authenticated',
      serverUrl: '/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(storage.getLlmFileEditJob('demo', 'missing-job')).rejects.toThrow('LLM edit job not found')
  })

  it('rejects guest LLM edit job operations with a login prompt', async () => {
    const storage = createProjectStorage({
      authMode: 'guest',
      serverUrl: '/proxy/api/intus',
      getAccessToken: vi.fn(),
    })

    await expect(
      storage.applyLlmFileEditJob('demo', {
        prompt: 'add a docstring',
        files: [{ id: 'f-1', filename: 'design.py', updated_at: '2024-01-01T00:00:00Z' }],
      }),
    ).rejects.toThrow('Log in to use AI file edits')
    await expect(storage.getLlmFileEditJob('demo', 'llm-job-1')).rejects.toThrow('Log in to use AI file edits')
    expect(mocks.apiFetch).not.toHaveBeenCalled()
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

  it('returns the token-only fixed LLM model contract', async () => {
    const responseBody = {
      default_model_id: 'gpt-5.6-sol',
      models: [{ id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol', model: 'gpt-5.6-sol', enabled: true }],
    }
    mocks.apiFetch.mockResolvedValueOnce(new Response(JSON.stringify(responseBody)))
    const getAccessToken = vi.fn()
    const storage = createProjectStorage({ authMode: 'authenticated', serverUrl: '/api/intus', getAccessToken })

    await expect(storage.listLlmModels()).resolves.toEqual(responseBody)
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/intus/llm-usage/models', getAccessToken)
  })
})
