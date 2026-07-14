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

function openGenerateDesignConversation() {
  fireEvent.click(screen.getByRole('button', { name: 'Open Generate Design conversation' }))
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
      default_model_id: 'gpt-5.6',
      models: [
        {
          id: 'gpt-5.6',
          label: 'GPT-5.6',
          model: 'gpt-5.6',
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

  it('starts viewer-first with the Generate Design conversation collapsed into a floating panel', async () => {
    render(<GenerateDesignWindow />)

    await screen.findByText('Latest model viewer')

    expect(screen.getByRole('button', { name: 'Open Generate Design conversation' })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Describe the CAD design or modification...')).not.toBeInTheDocument()

    openGenerateDesignConversation()

    expect(screen.getByRole('complementary', { name: 'Generate Design conversation' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Describe the CAD design or modification...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close Generate Design conversation' })).toBeInTheDocument()
  })

  it('shows the configured fixed model without pricing controls', async () => {
    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    expect(await screen.findByText('GPT-5.6')).toBeInTheDocument()
    expect(screen.getByText('gpt-5.6')).toBeInTheDocument()
    expect(screen.queryByText(/\$|per 1M|week/i)).not.toBeInTheDocument()
  })

  it('shows an error when the models response is empty', async () => {
    storage.listLlmModels.mockResolvedValueOnce({ default_model_id: '', models: [] })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    expect(await screen.findByText('No AI model is configured.')).toBeInTheDocument()
  })

  it('sends design.py first, omits files missing concurrency metadata, compiles changed output, and selects the artifact URL', async () => {
    render(<GenerateDesignWindow />)

    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    vi.useFakeTimers({ shouldAdvanceTime: true })

    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'make a larger test cube' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    expect(screen.queryByText('Compiling updated model...')).not.toBeInTheDocument()

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
      model_id: 'gpt-5.6',
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
    expect(await screen.findByText('Compiling updated model...')).toBeInTheDocument()
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
    openGenerateDesignConversation()

    expect(await screen.findByText('make a small bracket')).toBeInTheDocument()
    expect(screen.getAllByText('Updated 1 file.').length).toBeGreaterThan(0)
    expect(screen.getByText('test-model / 7 tokens')).toBeInTheDocument()
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-old\/model\?t=.*&project=project_a/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('make a small bracket').closest('button')!)
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-old\/model\?t=.*&project=project_a/)).toBeInTheDocument()
    expect(storage.listLlmEditConversation).toHaveBeenCalledWith('project_a')
  })

  it('switches the model viewer when an older hydrated prompt is selected', async () => {
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'llm-job-old',
        prompt: 'make a small bracket',
        content: 'Updated old file.',
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
      {
        job_id: 'llm-job-new',
        prompt: 'make it taller',
        content: 'Updated new file.',
        created_at: '2026-06-19T00:02:00Z',
        status: 'succeeded',
        model: 'test-model',
        usage: { prompt_tokens: 5, completion_tokens: 6, total_tokens: 11 },
        files: [{ filename: 'design.py', summary: 'Made taller.', changed: true }],
        compile: {
          job_id: 'compile-job-new',
          status: 'succeeded',
          artifact_id: 'artifact-new',
          export_format: 'glb',
        },
      },
    ])

    render(<GenerateDesignWindow />)
    openGenerateDesignConversation()

    expect(await screen.findByText('make a small bracket')).toBeInTheDocument()
    expect(screen.getByText('make it taller')).toBeInTheDocument()
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-new\/model\?t=.*&project=project_a/)).toBeInTheDocument()

    fireEvent.click(screen.getByText('make a small bracket').closest('button')!)

    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-old\/model\?t=.*&project=project_a/)).toBeInTheDocument()
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
    openGenerateDesignConversation()

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
    openGenerateDesignConversation()
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

  it('runs one automatic repair when generated design compile fails with sandbox_error', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.applyLlmFileEditJob
      .mockResolvedValueOnce({
        success: true,
        job_id: 'llm-job-1',
        status: 'queued',
      })
      .mockResolvedValueOnce({
        success: true,
        job_id: 'repair-job-1',
        status: 'queued',
      })
    storage.getLlmFileEditJob.mockImplementation((_projectName: string, jobId: string) => {
      if (jobId === 'repair-job-1') {
        return Promise.resolve({
          job_id: 'repair-job-1',
          status: 'succeeded',
          result: {
            success: true,
            outcome: 'changed',
            message: 'repaired',
            model: 'test-model',
            usage: { prompt_tokens: 11, completion_tokens: 5, total_tokens: 16 },
            snapshot: { id: 'snap-repair', message: 'repair', content_hash: 'def' },
            files: [
              {
                id: 'design-id',
                filename: 'design.py',
                content: 'box = Box(3, 3, 3)',
                updated_at: '2026-06-19T00:02:00Z',
                changed: true,
                summary: 'Removed unavailable RoundedPolygon API.',
              },
            ],
          },
        })
      }
      return Promise.resolve({
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
              content: 'lever = bd.RoundedPolygon([])',
              updated_at: '2026-06-19T00:01:00Z',
              changed: true,
              summary: 'Generated a lever.',
            },
          ],
        },
      })
    })

    let compilePostCount = 0
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url === '/api/intus/projects/project_a/compile' && init?.method === 'POST') {
        compilePostCount += 1
        return Promise.resolve(jsonResponse({
          success: true,
          job_id: compilePostCount === 1 ? 'job-1' : 'job-2',
          status: 'queued',
        }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/job-1') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-1',
          status: 'failed',
          error_code: 'sandbox_error',
          retryable: true,
          user_message: 'Compile failed. Fix the model source and try again.',
          error: "Traceback:\nAttributeError: module 'build123d' has no attribute 'RoundedPolygon'",
        }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/job-2') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-2',
          status: 'succeeded',
          format: 'glb',
          artifact_id: 'artifact-repaired',
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'Generate a door handle for 3d printing' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await waitFor(() => {
      expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/intus/projects/project_a/compile',
        mocks.getAccessToken,
        expect.objectContaining({ method: 'POST' }),
      )
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    await waitFor(() => {
      expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(2)
    })

    const repairRequest = storage.applyLlmFileEditJob.mock.calls[1]?.[1]
    expect(repairRequest).toBeDefined()
    if (!repairRequest) throw new Error('repair request was not captured')
    expect(repairRequest.prompt).toContain('Generate a door handle for 3d printing')
    expect(repairRequest.prompt).toContain("AttributeError: module 'build123d' has no attribute 'RoundedPolygon'")
    expect(repairRequest.metadata).toEqual({ source: 'generate_design_compile_repair' })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    await waitFor(() => {
      expect(compilePostCount).toBe(2)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    await waitFor(() => {
      expect(screen.getAllByText(/Compiled glb artifact artifact-repaired/).length).toBeGreaterThan(0)
    })

    fireEvent.click(screen.getByText('Generate a door handle for 3d printing').closest('button')!)
    expect(screen.getByText(/Model viewer \/api\/extus\/artifacts\/artifact-repaired\/model\?t=.*&project=project_a/)).toBeInTheDocument()
  })

  it('does not auto-repair non-sandbox compile failures', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url === '/api/intus/projects/project_a/compile' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({ success: true, job_id: 'job-1', status: 'queued' }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/job-1') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-1',
          status: 'failed',
          error_code: 'source_bundle_too_large',
          retryable: false,
          user_message: 'Compile source is too large to queue. Split the model into smaller files.',
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'make a larger test cube' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(screen.getAllByText(/Compile failed: Compile source is too large/).length).toBeGreaterThan(0)
    })
    expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1)
  })

  it('does not run more than one automatic repair for the same assistant message', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.applyLlmFileEditJob
      .mockResolvedValueOnce({ success: true, job_id: 'llm-job-1', status: 'queued' })
      .mockResolvedValueOnce({ success: true, job_id: 'repair-job-1', status: 'queued' })
    storage.getLlmFileEditJob.mockImplementation((_projectName: string, jobId: string) => Promise.resolve({
      job_id: jobId,
      status: 'succeeded',
      result: {
        success: true,
        outcome: 'changed',
        message: jobId === 'repair-job-1' ? 'repaired' : 'updated',
        model: 'test-model',
        usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
        snapshot: { id: `snap-${jobId}`, message: 'edit', content_hash: 'abc' },
        files: [
          {
            id: 'design-id',
            filename: 'design.py',
            content: 'lever = bd.RoundedPolygon([])',
            updated_at: jobId === 'repair-job-1' ? '2026-06-19T00:02:00Z' : '2026-06-19T00:01:00Z',
            changed: true,
            summary: 'Generated a lever.',
          },
        ],
      },
    }))

    let compilePostCount = 0
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url === '/api/intus/projects/project_a/compile' && init?.method === 'POST') {
        compilePostCount += 1
        return Promise.resolve(jsonResponse({
          success: true,
          job_id: compilePostCount === 1 ? 'job-1' : 'job-2',
          status: 'queued',
        }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/job-1' || url === '/api/intus/projects/project_a/compile/jobs/job-2') {
        return Promise.resolve(jsonResponse({
          status: 'failed',
          error_code: 'sandbox_error',
          retryable: true,
          user_message: 'Compile failed. Fix the model source and try again.',
          error: "Traceback:\nAttributeError: module 'build123d' has no attribute 'RoundedPolygon'",
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'Generate a door handle for 3d printing' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    for (let i = 0; i < 5; i += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000)
      })
    }

    await waitFor(() => {
      expect(compilePostCount).toBe(2)
    })
    expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(2)
    expect(screen.getAllByText(/Compile failed: Compile failed. Fix the model source/).length).toBeGreaterThan(0)
  })

  it('does not run another automatic repair after hydrating a repaired edit job', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'repair-job-1',
        prompt: 'The previous generated design failed to compile in the Tertius build123d sandbox.',
        content: 'Updated 1 file.',
        created_at: '2026-06-19T00:03:00Z',
        status: 'succeeded',
        model: 'test-model',
        usage: { prompt_tokens: 11, completion_tokens: 5, total_tokens: 16 },
        metadata: { source: 'generate_design_compile_repair' },
        files: [{ filename: 'design.py', summary: 'Removed unavailable RoundedPolygon API.', changed: true }],
        compile: {
          job_id: 'job-2',
          status: 'running',
          export_format: 'glb',
        },
      },
    ])
    mocks.apiFetch.mockImplementation((url: string) => {
      if (url === '/api/intus/projects/project_a/compile/jobs/job-2') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-2',
          status: 'failed',
          error_code: 'sandbox_error',
          retryable: true,
          user_message: 'Compile failed. Fix the model source and try again.',
          error: "Traceback:\nAttributeError: module 'build123d' has no attribute 'RoundedPolygon'",
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    await waitFor(() => {
      expect(screen.getAllByText(/Compile failed: Compile failed. Fix the model source/).length).toBeGreaterThan(0)
    })
    expect(storage.applyLlmFileEditJob).not.toHaveBeenCalled()
  })
})
