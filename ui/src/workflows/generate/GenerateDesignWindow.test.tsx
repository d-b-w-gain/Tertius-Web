import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LlmEditProgressSnapshot } from '../shared/projectStorage'
import { GenerateDesignWindow } from './GenerateDesignWindow'
import { ProgressActivity } from './ui/ProgressActivity'
import { ConversationPanel } from './ui/ConversationPanel'

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

function piProgressSnapshot(
  overrides: Partial<LlmEditProgressSnapshot> = {},
): LlmEditProgressSnapshot {
  return {
    schema_version: 1,
    execution_id: '7d364c43-45d4-4c66-9565-7885f65e6730',
    execution_started_at: '2026-07-27T11:00:00Z',
    last_batch_sequence: 1,
    last_sequence: 1,
    truncated_before_sequence: null,
    events: [
      {
        sequence: 1,
        kind: 'reasoning_delta',
        text: 'Inspecting the model structure.',
        tool_name: null,
        target: null,
        is_error: null,
        occurred_at: '2026-07-27T11:00:01Z',
      },
    ],
    ...overrides,
  }
}

describe('Generate conversation UI', () => {
  afterEach(cleanup)

  it('renders progress activity from snapshot props', () => {
    render(
      <ProgressActivity
        progress={piProgressSnapshot()}
        active
        defaultOpen
      />,
    )

    expect(screen.getByText('Thinking & activity')).toBeInTheDocument()
    expect(screen.getByText('Inspecting the model structure.')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(
      'AI activity updated: 1 event. Latest: Reasoning updated. Sequence: 1.',
    )
  })

  it('routes conversation prompt and selection interactions through props', () => {
    const onPromptChange = vi.fn()
    const onSelectMessage = vi.fn()
    const onSubmit = vi.fn((event: React.FormEvent) => event.preventDefault())

    render(
      <ConversationPanel
        statusText="Ready to generate."
        compileFormat="glb"
        compileQuality="sketch"
        projectSelector={<div>Project selector</div>}
        messages={[{
          id: 'assistant-1',
          role: 'assistant',
          content: 'Generated one file.',
          createdAt: 1,
          compileStatus: 'succeeded',
        }]}
        selectedMessageId={null}
        llmModels={[{
          id: 'gpt-test',
          label: 'GPT Test',
          model: 'gpt-test',
          enabled: true,
        }]}
        selectedModelId="gpt-test"
        prompt=""
        error={null}
        isSubmitting={false}
        canSubmit={false}
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onSelectModel={vi.fn()}
        onSelectMessage={onSelectMessage}
        onPromptChange={onPromptChange}
        onSubmit={onSubmit}
      />,
    )

    fireEvent.click(screen.getByText('Generated one file.'))
    fireEvent.change(
      screen.getByPlaceholderText('Describe the CAD design or modification...'),
      { target: { value: 'Make a bracket' } },
    )
    fireEvent.submit(screen.getByRole('button', { name: 'Generate Design' }).closest('form')!)

    expect(onSelectMessage).toHaveBeenCalledWith('assistant-1')
    expect(onPromptChange).toHaveBeenCalledWith('Make a bracket')
    expect(onSubmit).toHaveBeenCalledOnce()
  })
})

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
      default_model_id: 'gpt-5.6-sol',
      models: [
        {
          id: 'gpt-5.6-sol',
          label: 'GPT-5.6 Sol',
          model: 'gpt-5.6-sol',
          enabled: true,
        },
        {
          id: 'gpt-5.6-luna',
          label: 'GPT-5.6 Luna',
          model: 'gpt-5.6-luna',
          enabled: true,
        },
        {
          id: 'gpt-5.6-terra',
          label: 'GPT-5.6 Terra',
          model: 'gpt-5.6-terra',
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

  it('shows AI working on the closed conversation control only while progress is active', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.getLlmFileEditJob
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'running',
        progress: null,
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'succeeded',
        progress: null,
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
    fireEvent.change(
      screen.getByPlaceholderText('Describe the CAD design or modification...'),
      { target: { value: 'inspect the model' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))
    fireEvent.click(screen.getByRole('button', { name: 'Close Generate Design conversation' }))

    expect(screen.getByRole('button', {
      name: /Open Generate Design conversation.*AI working/,
    })).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByRole('button', {
      name: /Open Generate Design conversation.*AI working/,
    })).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    await waitFor(() => {
      expect(screen.getByRole('button', {
        name: 'Open Generate Design conversation',
      })).toBeInTheDocument()
    })
  })

  it('scrolls the submitted assistant card once without moving focus or following progress updates', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.getLlmFileEditJob.mockResolvedValue({
      job_id: 'llm-job-1',
      status: 'running',
      progress: piProgressSnapshot(),
    })
    const scrollIntoView = vi.fn()
    const focus = vi.spyOn(HTMLElement.prototype, 'focus')
    const originalScrollIntoView = Element.prototype.scrollIntoView
    Object.defineProperty(Element.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    })

    try {
      render(<GenerateDesignWindow />)
      await screen.findByText('Latest model viewer')
      openGenerateDesignConversation()
      fireEvent.change(
        screen.getByPlaceholderText('Describe the CAD design or modification...'),
        { target: { value: 'inspect the model' } },
      )
      fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

      await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1))
      expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
      expect(focus).not.toHaveBeenCalled()

      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      await waitFor(() => {
        expect(screen.getByText('Inspecting the model structure.')).toBeInTheDocument()
      })
      expect(scrollIntoView).toHaveBeenCalledTimes(1)
      expect(focus).not.toHaveBeenCalled()
    } finally {
      focus.mockRestore()
      if (originalScrollIntoView) {
        Object.defineProperty(Element.prototype, 'scrollIntoView', {
          configurable: true,
          value: originalScrollIntoView,
        })
      } else {
        delete (Element.prototype as { scrollIntoView?: Element['scrollIntoView'] }).scrollIntoView
      }
    }
  })

  it('selects the configured default model and allows switching models', async () => {
    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    const selector = await screen.findByRole('combobox', { name: 'AI model' })
    expect(selector).toHaveValue('gpt-5.6-sol')
    expect(within(selector).getAllByRole('option')).toHaveLength(3)
    fireEvent.change(selector, { target: { value: 'gpt-5.6-terra' } })
    expect(selector).toHaveValue('gpt-5.6-terra')
    expect(screen.queryByRole('combobox', { name: 'AI context size' })).not.toBeInTheDocument()
    expect(screen.queryByText(/\$|per 1M|week/i)).not.toBeInTheDocument()
  })

  it('disables generation when every configured model is unavailable', async () => {
    storage.listLlmModels.mockResolvedValueOnce({
      default_model_id: 'gpt-5.6-sol',
      models: [
        { id: 'gpt-5.6-sol', label: 'GPT-5.6 Sol', model: 'gpt-5.6-sol', enabled: false },
        { id: 'gpt-5.6-luna', label: 'GPT-5.6 Luna', model: 'gpt-5.6-luna', enabled: false },
      ],
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    const selector = await screen.findByRole('combobox', { name: 'AI model' })
    expect((selector as HTMLSelectElement).value).toBe('')
    for (const option of within(selector).getAllByRole('option')) {
      expect(option).toBeDisabled()
    }
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'make a bracket' },
    })
    expect(screen.getByRole('button', { name: 'Generate Design' })).toBeDisabled()
  })

  it('shows an error and disables generation when the models response is empty', async () => {
    storage.listLlmModels.mockResolvedValueOnce({ default_model_id: '', models: [] })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    expect(await screen.findByText('No AI model is configured.')).toBeInTheDocument()
    const selector = screen.getByRole('combobox', { name: 'AI model' })
    expect(within(selector).queryAllByRole('option')).toHaveLength(0)
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'make a bracket' },
    })
    expect(screen.getByRole('button', { name: 'Generate Design' })).toBeDisabled()
  })

  it('clears a stale model selection when model refresh fails', async () => {
    const { rerender } = render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    const selector = await screen.findByRole('combobox', { name: 'AI model' })
    fireEvent.change(selector, { target: { value: 'gpt-5.6-terra' } })
    expect(selector).toHaveValue('gpt-5.6-terra')

    storage.listLlmModels.mockRejectedValueOnce(new Error('Model discovery unavailable.'))
    rerender(<GenerateDesignWindow isActive={false} />)
    rerender(<GenerateDesignWindow isActive />)

    expect(await screen.findByText('Model discovery unavailable.')).toBeInTheDocument()
    await waitFor(() => {
      const refreshedSelector = screen.getByRole('combobox', { name: 'AI model' })
      expect((refreshedSelector as HTMLSelectElement).value).toBe('')
      expect(within(refreshedSelector).queryAllByRole('option')).toHaveLength(0)
    })
  })

  it('clears a stale model selection when model refresh returns an empty catalog', async () => {
    const { rerender } = render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    const selector = await screen.findByRole('combobox', { name: 'AI model' })
    fireEvent.change(selector, { target: { value: 'gpt-5.6-terra' } })
    expect(selector).toHaveValue('gpt-5.6-terra')

    storage.listLlmModels.mockResolvedValueOnce({ default_model_id: '', models: [] })
    rerender(<GenerateDesignWindow isActive={false} />)
    rerender(<GenerateDesignWindow isActive />)

    expect(await screen.findByText('No AI model is configured.')).toBeInTheDocument()
    await waitFor(() => {
      const refreshedSelector = screen.getByRole('combobox', { name: 'AI model' })
      expect((refreshedSelector as HTMLSelectElement).value).toBe('')
      expect(within(refreshedSelector).queryAllByRole('option')).toHaveLength(0)
    })
  })

  it('sends design.py first, omits files missing concurrency metadata, compiles changed output, and selects the artifact URL', async () => {
    render(<GenerateDesignWindow />)

    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    vi.useFakeTimers({ shouldAdvanceTime: true })

    fireEvent.change(screen.getByRole('combobox', { name: 'AI model' }), {
      target: { value: 'gpt-5.6-terra' },
    })
    expect(screen.queryByRole('combobox', { name: 'AI context size' })).not.toBeInTheDocument()

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
      model_id: 'gpt-5.6-terra',
      metadata: { source: 'generate_design_window' },
    })
    expect(storage.applyLlmFileEditJob.mock.calls[0]?.[1]).not.toHaveProperty('context_tier')

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
        progress: piProgressSnapshot(),
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
    const hydratedActivity = screen.getByText('Thinking & activity').closest('details')
    expect(hydratedActivity).toHaveAttribute('open')
    expect(screen.getByText('Working')).toBeInTheDocument()

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

    const selector = screen.getByRole('combobox', { name: 'AI model' })
    fireEvent.change(selector, { target: { value: 'gpt-5.6-terra' } })

    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'Generate a door handle for 3d printing' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await waitFor(() => {
      expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1)
    })
    fireEvent.change(selector, { target: { value: 'gpt-5.6-luna' } })
    expect(selector).toHaveValue('gpt-5.6-luna')

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
    expect(repairRequest.model_id).toBe('gpt-5.6-terra')
    expect(repairRequest).not.toHaveProperty('context_tier')
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

  it('keeps an automatic repair discoverable while closed until the repair job completes', async () => {
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

    let repairPollCount = 0
    storage.getLlmFileEditJob.mockImplementation((_projectName: string, jobId: string) => {
      if (jobId === 'repair-job-1') {
        repairPollCount += 1
        if (repairPollCount === 1) {
          return Promise.resolve({
            job_id: 'repair-job-1',
            status: 'running',
            progress: null,
          })
        }
        return Promise.resolve({
          job_id: 'repair-job-1',
          status: 'succeeded',
          progress: null,
          result: {
            success: true,
            outcome: 'no_change',
            message: 'No repair changes needed.',
            model: 'test-model',
            usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
            snapshot: null,
            files: [],
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

    mocks.apiFetch.mockImplementation((url: string, _token: unknown, init?: RequestInit) => {
      if (url === '/api/intus/projects/project_a/compile' && init?.method === 'POST') {
        return Promise.resolve(jsonResponse({
          success: true,
          job_id: 'compile-job-1',
          status: 'queued',
        }, true))
      }
      if (url === '/api/intus/projects/project_a/compile/jobs/compile-job-1') {
        return Promise.resolve(jsonResponse({
          job_id: 'compile-job-1',
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
      target: { value: 'Generate a printable handle' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/intus/projects/project_a/compile',
        mocks.getAccessToken,
        expect.objectContaining({ method: 'POST' }),
      )
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    await waitFor(() => {
      expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(2)
    })

    fireEvent.click(screen.getByRole('button', { name: 'Close Generate Design conversation' }))
    expect(screen.getByRole('button', {
      name: /Open Generate Design conversation.*AI working/,
    })).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByRole('button', {
      name: /Open Generate Design conversation.*AI working/,
    })).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    await waitFor(() => {
      expect(screen.getByRole('button', {
        name: 'Open Generate Design conversation',
      })).toBeInTheDocument()
    })
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

  it('fails automatic repair with a bounded error when the original model snapshot is missing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'llm-job-without-model',
        prompt: 'Generate a printable latch',
        content: 'Updated 1 file.',
        created_at: '2026-06-19T00:03:00Z',
        status: 'succeeded',
        compile: {
          job_id: 'compile-job-without-model',
          status: 'running',
          export_format: 'glb',
        },
      },
    ])
    mocks.apiFetch.mockImplementation((url: string) => {
      if (url === '/api/intus/projects/project_a/compile/jobs/compile-job-without-model') {
        return Promise.resolve(jsonResponse({
          job_id: 'compile-job-without-model',
          status: 'failed',
          error_code: 'sandbox_error',
          retryable: true,
          user_message: 'Compile failed. Fix the model source and try again.',
          error: 'Traceback:\nNameError: latch is not defined',
        }, true))
      }
      return Promise.resolve(jsonResponse({}, false))
    })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect((await screen.findAllByText(/Automatic repair could not start: Original AI model is unavailable\./)).length).toBeGreaterThan(0)
    expect(storage.applyLlmFileEditJob).not.toHaveBeenCalled()
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

  it('hydrates a compact collapsed terminal activity log with safe labels and truncation notice', async () => {
    storage.listLlmEditConversation.mockResolvedValueOnce([
      {
        job_id: 'llm-job-history-progress',
        prompt: 'refine the bearing seat',
        content: 'Updated 1 file.',
        created_at: '2026-07-27T11:00:00Z',
        status: 'succeeded',
        progress: piProgressSnapshot({
          last_batch_sequence: 4,
          last_sequence: 16,
          truncated_before_sequence: 12,
          events: [
            { sequence: 13, kind: 'reasoning_delta', text: 'Checking the wall thickness before applying the edit.', tool_name: null, target: null, is_error: null, occurred_at: '2026-07-27T11:00:01Z' },
            { sequence: 14, kind: 'tool_started', text: null, tool_name: 'read', target: 'design.py', is_error: null, occurred_at: '2026-07-27T11:00:02Z' },
            { sequence: 15, kind: 'tool_finished', text: null, tool_name: 'edit', target: 'design.py', is_error: false, occurred_at: '2026-07-27T11:00:03Z' },
            { sequence: 16, kind: 'tool_finished', text: null, tool_name: 'write', target: 'parts/bearing.py', is_error: true, occurred_at: '2026-07-27T11:00:04Z' },
          ],
        }),
        compile: null,
      },
    ])

    render(<GenerateDesignWindow />)
    openGenerateDesignConversation()

    const activitySummary = await screen.findByText('Thinking & activity')
    const details = activitySummary.closest('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
    expect(screen.getByText('Complete')).toBeInTheDocument()
    expect(screen.getByText('4 updates')).toBeInTheDocument()
    const selectionButton = screen.getAllByText('Updated 1 file.')
      .map(element => element.closest('button'))
      .find(Boolean)
    expect(selectionButton?.contains(details)).toBe(false)
    fireEvent.click(activitySummary)

    expect(screen.getByRole('list')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(5)
    expect(screen.getByText('Earlier activity was truncated.')).toBeInTheDocument()
    expect(screen.getByText('Checking the wall thickness before applying the edit.')).toBeInTheDocument()
    expect(screen.getByText('Read started')).toBeInTheDocument()
    expect(screen.getByText('Edit completed')).toBeInTheDocument()
    expect(screen.getByText('Write failed')).toBeInTheDocument()
    const fullToolTarget = screen.getByText('parts/bearing.py')
    expect(fullToolTarget).toBeInTheDocument()
    expect(fullToolTarget).toHaveClass('break-all')
    expect(fullToolTarget).not.toHaveClass('truncate')
    expect(fullToolTarget).not.toHaveAttribute('title')
    expect(details?.querySelector('[aria-live]')).toBeNull()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('announces capped active progress when the retained event count stays unchanged', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const cappedEvents = (startSequence: number) => Array.from({ length: 128 }, (_, index) => {
      const sequence = startSequence + index
      return {
        sequence,
        kind: 'tool_started' as const,
        text: null,
        tool_name: 'read' as const,
        target: `private/part-${sequence}.py`,
        is_error: null,
        occurred_at: '2026-07-27T11:00:01Z',
      }
    })
    storage.getLlmFileEditJob
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'running',
        progress: piProgressSnapshot({
          last_batch_sequence: 8,
          last_sequence: 128,
          events: cappedEvents(1),
        }),
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'running',
        progress: piProgressSnapshot({
          last_batch_sequence: 9,
          last_sequence: 129,
          truncated_before_sequence: 1,
          events: cappedEvents(2),
        }),
      })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'inspect the capped activity stream' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))
    await waitFor(() => expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByRole('status')).toHaveTextContent(
      'AI activity updated: 128 events. Latest: Read started. Sequence: 128.',
    )
    expect(screen.getByRole('status')).not.toHaveTextContent('private/part-128.py')

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByRole('status')).toHaveTextContent(
      'AI activity updated: 128 events. Latest: Read started. Sequence: 129.',
    )
    expect(screen.getByRole('status')).not.toHaveTextContent('private/part-129.py')
  })

  it('merges progress snapshots while preserving the open disclosure through completion', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const firstExecution = '7d364c43-45d4-4c66-9565-7885f65e6730'
    const secondExecution = '503ecbd5-6c5a-4564-88df-456ab503a207'
    storage.getLlmFileEditJob
      .mockResolvedValueOnce({ job_id: 'llm-job-1', status: 'running', progress: piProgressSnapshot({ execution_id: firstExecution }) })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1', status: 'running', progress: piProgressSnapshot({
          execution_id: firstExecution, last_batch_sequence: 2, last_sequence: 2,
          events: [{ sequence: 2, kind: 'tool_started', text: null, tool_name: 'read', target: 'design.py', is_error: null, occurred_at: '2026-07-27T11:00:02Z' }],
        }),
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1', status: 'running', progress: piProgressSnapshot({
          execution_id: firstExecution,
          events: [{ sequence: 1, kind: 'reasoning_delta', text: 'This stale snapshot must be ignored.', tool_name: null, target: null, is_error: null, occurred_at: '2026-07-27T11:00:01Z' }],
        }),
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1', status: 'running', progress: piProgressSnapshot({
          execution_id: secondExecution, execution_started_at: '2026-07-27T11:10:00Z',
          events: [{ sequence: 1, kind: 'reasoning_delta', text: 'Fresh execution reasoning.', tool_name: null, target: null, is_error: null, occurred_at: '2026-07-27T11:10:01Z' }],
        }),
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1', status: 'succeeded',
        progress: piProgressSnapshot({
          execution_id: secondExecution, execution_started_at: '2026-07-27T11:10:00Z', last_batch_sequence: 2, last_sequence: 2,
          events: [
            { sequence: 1, kind: 'reasoning_delta', text: 'Fresh execution reasoning.', tool_name: null, target: null, is_error: null, occurred_at: '2026-07-27T11:10:01Z' },
            { sequence: 2, kind: 'tool_finished', text: null, tool_name: 'write', target: 'design.py', is_error: true, occurred_at: '2026-07-27T11:10:02Z' },
          ],
        }),
        result: {
          success: true, outcome: 'no_change', message: 'No edits needed.', model: 'test-model',
          usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 }, snapshot: null, files: [],
        },
      })

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), { target: { value: 'inspect and refine the model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))
    const initialDetails = screen.getByText('Thinking & activity').closest('details')
    expect(initialDetails).toHaveAttribute('open')
    expect(screen.getByText('Starting')).toBeInTheDocument()
    expect(screen.getByText('0 updates')).toBeInTheDocument()
    expect(screen.getByText('Waiting for the first progress update…')).toBeInTheDocument()
    await waitFor(() => expect(storage.applyLlmFileEditJob).toHaveBeenCalledTimes(1))

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    let details = screen.getByText('Thinking & activity').closest('details')
    expect(details).toBe(initialDetails)
    expect(details).toHaveAttribute('open')
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(screen.getByText('1 update')).toHaveClass('text-slate-400')
    expect(screen.getByText('Inspecting the model structure.')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('AI activity updated: 1 event. Latest: Reasoning updated.')
    expect(screen.getByRole('status')).not.toHaveTextContent('Inspecting the model structure.')
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('Read started')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('AI activity updated: 2 events. Latest: Read started.')
    expect(screen.getByRole('status')).not.toHaveTextContent('design.py')
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.queryByText('This stale snapshot must be ignored.')).not.toBeInTheDocument()
    expect(screen.getByText('Inspecting the model structure.')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(screen.getByText('Fresh execution reasoning.')).toBeInTheDocument()
    expect(screen.queryByText('Inspecting the model structure.')).not.toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    await waitFor(() => expect(screen.getAllByText('No edits needed.').length).toBeGreaterThan(0))
    details = screen.getByText('Thinking & activity').closest('details')
    expect(details).toBe(initialDetails)
    expect(details).toHaveAttribute('open')
    expect(screen.getByText('Complete')).toBeInTheDocument()
    expect(screen.getByText('2 updates')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getAllByText('Fresh execution reasoning.')).toHaveLength(1)
    expect(screen.getByText('Write failed')).toBeInTheDocument()
  })

  it('preserves a manual activity close when the current turn completes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.getLlmFileEditJob
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'running',
        progress: piProgressSnapshot(),
      })
      .mockResolvedValueOnce({
        job_id: 'llm-job-1',
        status: 'succeeded',
        progress: piProgressSnapshot(),
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
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'inspect without changing the model' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    const details = screen.getByText('Thinking & activity').closest('details')
    expect(details).toHaveAttribute('open')
    fireEvent.click(screen.getByText('Thinking & activity'))
    expect(details).not.toHaveAttribute('open')

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(screen.getByText('Thinking & activity').closest('details')).toBe(details)
    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(details).not.toHaveAttribute('open')

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    await waitFor(() => expect(screen.getByText('Complete')).toBeInTheDocument())
    expect(screen.getByText('Thinking & activity').closest('details')).toBe(details)
    expect(details).not.toHaveAttribute('open')
  })

  it('keeps a current terminal disclosure visible when no activity details arrive', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    storage.getLlmFileEditJob.mockResolvedValueOnce({
      job_id: 'llm-job-1',
      status: 'succeeded',
      progress: null,
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
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'check whether an edit is needed' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    await waitFor(() => expect(screen.getByText('Complete')).toBeInTheDocument())
    expect(screen.getByText('0 updates')).toBeInTheDocument()
    expect(screen.getByText('No activity details were received.')).toBeInTheDocument()
    expect(screen.getByText('Thinking & activity').closest('details')).toHaveAttribute('open')
  })

  it('marks the pending disclosure complete when submission fails', async () => {
    storage.applyLlmFileEditJob.mockRejectedValueOnce(new Error('Provider unavailable.'))

    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    fireEvent.change(screen.getByPlaceholderText('Describe the CAD design or modification...'), {
      target: { value: 'submit an edit that cannot start' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    expect(screen.getByText('Starting')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Complete')).toBeInTheDocument())
    expect(screen.getByText('No activity details were received.')).toBeInTheDocument()
    expect(screen.getAllByText('Error: Provider unavailable.').length).toBeGreaterThan(0)
  })

  it('does not render activity for terminal history without a progress snapshot', async () => {
    storage.listLlmEditConversation.mockResolvedValueOnce([
      { job_id: 'llm-job-without-progress', prompt: 'keep this simple', content: 'No edits needed.', created_at: '2026-07-27T11:00:00Z', status: 'succeeded', progress: null, compile: null },
    ])
    render(<GenerateDesignWindow />)
    openGenerateDesignConversation()
    expect((await screen.findAllByText('No edits needed.')).length).toBeGreaterThan(0)
    expect(screen.queryByText('Thinking & activity')).not.toBeInTheDocument()
  })
})
