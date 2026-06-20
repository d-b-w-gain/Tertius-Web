import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { GenerateDesignWindow } from './GenerateDesignWindow'

const storage = vi.hoisted(() => ({
  getActiveProject: vi.fn(),
  listProjects: vi.fn(),
  createProject: vi.fn(),
  activateProject: vi.fn(),
  listFiles: vi.fn(),
  listFileMetadata: vi.fn(),
  loadCode: vi.fn(),
  saveCode: vi.fn(),
  deleteFile: vi.fn(),
  getStatus: vi.fn(),
  getHistory: vi.fn(),
  applyLlmFileEdit: vi.fn(),
  applyLlmFileEditJob: vi.fn(),
  getLlmFileEditJob: vi.fn(),
  listLlmEditConversation: vi.fn(),
  listLlmModels: vi.fn(),
}))

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn().mockResolvedValue('test-token'),
  login: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  apiFetch: mocks.apiFetch,
}))

vi.mock('../../auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'authenticated',
    getAccessToken: mocks.getAccessToken,
    login: mocks.login,
  }),
}))

vi.mock('../shared/projectStorage', async () => {
  const actual = await vi.importActual<typeof import('../shared/projectStorage')>('../shared/projectStorage')
  return {
    ...actual,
    createProjectStorage: () => storage,
  }
})

vi.mock('../shared/ui/ProjectSelector', () => ({
  ACTIVE_PROJECT_CHANGED_EVENT: 'tertius:active-project-changed',
  ProjectSelector: () => <div>Project selector mock</div>,
}))

vi.mock('../extus/ui/ViewerTab', () => ({
  LatestModelViewer: ({ statusTextOverride }: { statusTextOverride?: string }) => (
    <div>
      <span>Latest model viewer</span>
      {statusTextOverride && <span>{statusTextOverride}</span>}
    </div>
  ),
  ModelViewerCanvas: ({ modelUrl, statusText }: { modelUrl: string; statusText?: string }) => (
    <div>
      Model viewer {modelUrl}
      {statusText && <span>{statusText}</span>}
    </div>
  ),
}))

function jsonResponse(data: unknown, ok = true) {
  return {
    ok,
    json: vi.fn().mockResolvedValue(data),
  }
}

describe('GenerateDesignWindow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    for (const mock of Object.values(storage)) {
      mock.mockReset()
    }
    localStorage.clear()
    storage.getActiveProject.mockResolvedValue('project_a')
    storage.listFileMetadata.mockResolvedValue([
      { id: 'helpers-id', filename: 'helpers.py', updated_at: '2026-06-18T00:00:00Z' },
      { id: 'design-id', filename: 'design.py', updated_at: '2026-06-19T00:00:00Z' },
      { filename: 'notes.py' },
      { id: 'stale-id', filename: 'stale.py' },
    ])
    storage.loadCode.mockResolvedValue('box = Box(1, 1, 1)')
    storage.listLlmModels.mockResolvedValue({
      default_model_id: 'kimi-k2.7-code',
      daily_budget_usd: 2,
      models: [
        {
          id: 'kimi-k2.7-code',
          label: 'Kimi K2.7 Code',
          model: 'kimi-k2.7-code',
          api: 'openai-chat-completions',
          endpoint: 'https://opencode.ai/zen/go/v1/chat/completions',
          input_price_per_million: 0.95,
          output_price_per_million: 4,
          cached_read_price_per_million: 0.19,
          cached_write_price_per_million: null,
          enabled: true,
        },
      ],
    })
    storage.applyLlmFileEditJob.mockResolvedValue({
      success: true,
      job_id: 'llm-job-1',
      status: 'queued',
    })
    storage.listLlmEditConversation.mockResolvedValue([])
    storage.getLlmFileEditJob.mockResolvedValue({
      job_id: 'llm-job-1',
      status: 'succeeded',
      result: {
        success: true,
        outcome: 'changed',
        message: 'updated',
        model: 'test-model',
        usage: { prompt_tokens: 7, completion_tokens: 5, total_tokens: 12 },
        snapshot: { id: 'snap-1', message: 'edit', content_hash: 'abc' },
        files: [
          {
            id: 'design-id',
            filename: 'design.py',
            content: 'box = Box(2, 2, 2)',
            updated_at: '2026-06-19T00:01:00Z',
            changed: true,
            summary: 'Made the box larger.',
          },
        ],
        cost_usd: 0.01,
      },
    })
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url === '/api/intus/projects/project_a/compile' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ success: true, job_id: 'job-1', status: 'queued' }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/job-1') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-1',
          status: 'succeeded',
          format: 'glb',
          artifact_id: 'artifact-1',
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('sends design.py first, omits files missing concurrency metadata, compiles changed output, and selects the artifact URL', async () => {
    render(<GenerateDesignWindow />)

    await screen.findByText('Latest model viewer')
    vi.useFakeTimers({ shouldAdvanceTime: true })

    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'make a larger test cube' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    expect(await screen.findByText('Compiling updated model...')).toBeInTheDocument()

    await waitFor(() => {
      expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1)
    })

    expect(storage.applyLlmFileEditJob).toHaveBeenCalledWith('project_a', {
      prompt: 'make a larger test cube',
      files: [
        { id: 'design-id', filename: 'design.py', updated_at: '2026-06-19T00:00:00Z' },
        { id: 'helpers-id', filename: 'helpers.py', updated_at: '2026-06-18T00:00:00Z' },
      ],
      active_file_id: 'design-id',
      model_id: 'kimi-k2.7-code',
      metadata: { source: 'generate_design_window' },
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(storage.getLlmFileEditJob).toHaveBeenCalledWith('project_a', 'llm-job-1')
    })

    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/intus/projects/project_a/compile',
        mocks.getAccessToken,
        expect.objectContaining({ method: 'POST' }),
      )
    })
    const compileRequest = mocks.apiFetch.mock.calls.find(([url]) => url === '/api/intus/projects/project_a/compile')?.[2] as RequestInit
    expect(JSON.parse(compileRequest.body as string)).toEqual({
      code: 'box = Box(2, 2, 2)',
      export_format: 'glb',
      quality: 'sketch',
      file: 'design.py',
      originating_llm_edit_job_id: 'llm-job-1',
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(screen.getAllByText(/Compiled glb artifact artifact-1/).length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText(/Compile queued as glb\/sketch.[\s\S]*Compiled glb artifact artifact-1/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-1\/model\?t=.*&project=project_a/)).toBeInTheDocument()
    expect(localStorage.getItem('tertius:ai-tokens-used-today')).toBe('12')
  })

  it('hydrates persisted Generate Design conversation history on project load', async () => {
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'llm-job-old',
        prompt: 'make a small bracket',
        content: 'Updated 1 file.',
        created_at: '2026-06-19T00:01:00Z',
        status: 'succeeded',
        model: 'test-model',
        usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 },
        files: [{ filename: 'design.py', summary: 'Added bracket.', changed: true }],
        compile: {
          job_id: 'compile-job-old',
          status: 'succeeded',
          artifact_id: 'artifact-old',
          export_format: 'glb',
        },
      },
    ])

    render(<GenerateDesignWindow />)

    expect(await screen.findByText('make a small bracket')).toBeInTheDocument()
    expect(screen.getAllByText('Updated 1 file.').length).toBeGreaterThan(0)
    expect(screen.getByText('test-model / 7 tokens')).toBeInTheDocument()
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-old\/model\?t=.*&project=project_a/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('make a small bracket').closest('button')!)
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-old\/model\?t=.*&project=project_a/)).toBeInTheDocument()
    expect(storage.listLlmEditConversation).toHaveBeenCalledWith('project_a')
  })

  it('resumes every hydrated non-terminal LLM job and linked compile job', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'llm-job-running',
        prompt: 'still editing',
        content: '',
        created_at: '2026-06-19T00:01:00Z',
        status: 'running',
        compile: null,
      },
      {
        job_id: 'llm-job-finished',
        prompt: 'compile still running',
        content: 'Updated 1 file.',
        created_at: '2026-06-19T00:02:00Z',
        status: 'succeeded',
        compile: {
          job_id: 'compile-job-running',
          status: 'running',
          export_format: 'glb',
        },
      },
    ])
    storage.getLlmFileEditJob.mockResolvedValue({
      job_id: 'llm-job-running',
      status: 'running',
    })
    mocks.apiFetch.mockImplementation((url: string) => {
      if (url === '/api/intus/projects/project_a/compile/jobs/compile-job-running') {
        return Promise.resolve(jsonResponse({
          job_id: 'compile-job-running',
          status: 'running',
          format: 'glb',
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)

    expect(await screen.findByText('still editing')).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(storage.getLlmFileEditJob).toHaveBeenCalledWith('project_a', 'llm-job-running')
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/intus/projects/project_a/compile/jobs/compile-job-running',
        mocks.getAccessToken,
      )
    })
  })

  it('does not queue compile when the AI edit returns no_change', async () => {
    storage.applyLlmFileEditJob.mockResolvedValueOnce({
      success: true,
      job_id: 'llm-job-2',
      status: 'queued',
    })
    storage.getLlmFileEditJob.mockResolvedValueOnce({
      job_id: 'llm-job-2',
      status: 'succeeded',
      result: {
        success: true,
        outcome: 'no_change',
        message: 'No edits needed.',
        model: 'test-model',
        usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
        snapshot: null,
        files: [],
      },
    })

    render(<GenerateDesignWindow />)

    await screen.findByText('Latest model viewer')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'leave it alone' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(screen.getAllByText('No edits needed.').length).toBeGreaterThan(0)
    })

    expect(mocks.apiFetch).not.toHaveBeenCalledWith(
      '/api/intus/projects/project_a/compile',
      expect.anything(),
      expect.anything(),
    )
  })
})
