import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FeatureTreeTab } from './FeatureTreeTab'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
}))

vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../../auth/AuthProvider', () => ({
  useAuth: () => ({ authMode: 'authenticated', getAccessToken: mocks.getAccessToken }),
}))
vi.mock('../../shared/ui/ProjectSelector', () => ({
  ACTIVE_PROJECT_CHANGED_EVENT: 'tertius:active-project-changed',
  ProjectSelector: () => <div data-testid="project-selector" />,
}))

type MockRouteState = {
  projectName?: string
  features?: Array<{ name: string; value: string | number | boolean; type: string; description: string }>
  operations?: unknown[]
  metadata?: Array<{ id: string; filename: string; updated_at?: string }>
  editStatus?: number
  editBody?: Record<string, unknown>
}

function jsonResponse(data: unknown, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    statusText: ok ? 'OK' : 'HTTP error',
    clone: vi.fn(() => jsonResponse(data, ok, status)),
    json: vi.fn().mockResolvedValue(data),
  }
}

function setupRoutes(state: MockRouteState = {}) {
  const routeState = {
    projectName: 'default_purlin',
    features: [
      { name: 'length', value: 100, type: 'int', description: '' },
    ],
    operations: [],
    metadata: [
      { id: 'file-design-id', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
    ],
    editStatus: 200,
    editBody: {
      success: true,
      outcome: 'changed',
      message: '',
      model: 'test-model',
      usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      snapshot: { id: 'snap-1', message: 'edit', content_hash: 'hash' },
      files: [
        {
          id: 'file-design-id',
          filename: 'design.py',
          content: 'updated design',
          updated_at: '2026-06-17T00:02:00Z',
          changed: true,
          summary: 'updated design',
        },
      ],
    },
    ...state,
  }

  mocks.apiFetch.mockImplementation((url: string, _token: unknown, options?: RequestInit) => {
    if (url === '/api/artus/features') {
      return Promise.resolve(jsonResponse({
        project_name: routeState.projectName,
        features: routeState.features,
        operations: routeState.operations,
      }))
    }
    if (url === '/api/extus/status') {
      return Promise.resolve(jsonResponse({}))
    }
    if (url === '/api/artus/update_features') {
      return Promise.resolve(jsonResponse({ success: true }))
    }
    if (url === `/api/intus/projects/${routeState.projectName}/files` && !options) {
      return Promise.resolve(jsonResponse({
        files: routeState.metadata.map(file => file.filename),
        file_metadata: routeState.metadata,
      }))
    }
    if (url === `/api/intus/projects/${routeState.projectName}/files/llm-edit/jobs` && options?.method === 'POST') {
      const ok = routeState.editStatus >= 200 && routeState.editStatus < 300
      return Promise.resolve(jsonResponse(
        ok ? { success: true, job_id: 'llm-job-1', status: 'queued' } : routeState.editBody,
        ok,
        routeState.editStatus,
      ))
    }
    if (url === `/api/intus/projects/${routeState.projectName}/files/llm-edit/jobs/llm-job-1`) {
      return Promise.resolve(jsonResponse({
        job_id: 'llm-job-1',
        status: 'succeeded',
        result: routeState.editBody,
      }))
    }
    if (url === `/api/intus/projects/${routeState.projectName}/compile` && options?.method === 'POST') {
      return Promise.resolve(jsonResponse({
        success: true,
        job_id: 'compile-job-1',
        status: 'queued',
        format: 'glb',
      }, true, 202))
    }
    return Promise.resolve(jsonResponse({ error: `Unhandled ${url}` }, false, 404))
  })

  return routeState
}

async function renderAuthenticatedFeatureTree() {
  render(<FeatureTreeTab serverUrl="/api/artus" />)
  await waitFor(() => {
    expect(screen.getAllByDisplayValue('100').length).toBeGreaterThan(0)
  })
}

function editRequests() {
  return mocks.apiFetch.mock.calls.filter(([url, , options]) => (
    url === '/api/intus/projects/default_purlin/files/llm-edit/jobs' && options?.method === 'POST'
  ))
}

function compileRequests(projectName = 'default_purlin') {
  return mocks.apiFetch.mock.calls.filter(([url, , options]) => (
    url === `/api/intus/projects/${projectName}/compile` && options?.method === 'POST'
  ))
}

function aiPromptInput() {
  return screen.getAllByPlaceholderText(/Change the length/i)[0]!
}

describe('FeatureTreeTab authenticated AI edit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getAccessToken.mockResolvedValue('token')
  })

  afterEach(() => {
    cleanup()
  })

  it('calls the Intus LLM edit endpoint instead of the Artus ai_modify endpoint', async () => {
    setupRoutes({
      metadata: [
        { id: 'file-design-id', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
        { id: 'file-helper-id', filename: 'helper.py', updated_at: '2026-06-17T00:01:00Z' },
      ],
    })
    await renderAuthenticatedFeatureTree()

    fireEvent.change(aiPromptInput(), {
      target: { value: 'add a brace' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(editRequests()).toHaveLength(1)
    })
    expect(mocks.apiFetch.mock.calls.some(([url]) => url === '/api/artus/ai_modify')).toBe(false)
  })

  it('sends all editable files with design.py first and active_file_id set to design.py', async () => {
    setupRoutes({
      metadata: [
        { id: 'file-helper-id', filename: 'helper.py', updated_at: '2026-06-17T00:01:00Z' },
        { id: 'file-design-id', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
        { id: 'file-parts-id', filename: 'parts.py', updated_at: '2026-06-17T00:02:00Z' },
      ],
    })
    await renderAuthenticatedFeatureTree()

    fireEvent.change(aiPromptInput(), {
      target: { value: 'update the assembly' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(editRequests()).toHaveLength(1)
    })
    const body = JSON.parse(editRequests()[0]![2]!.body as string)
    expect(body.files).toEqual([
      { id: 'file-design-id', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
      { id: 'file-helper-id', filename: 'helper.py', updated_at: '2026-06-17T00:01:00Z' },
      { id: 'file-parts-id', filename: 'parts.py', updated_at: '2026-06-17T00:02:00Z' },
    ])
    expect(body.active_file_id).toBe('file-design-id')
    expect(body.metadata).toEqual({
      source: 'artus_feature_tree',
      active_panel: 'variables',
      highlighted_node: '',
    })
  })

  it('caps AI edit requests at 20 files while preserving the warning after success', async () => {
    const metadata = [
      { id: 'file-design-id', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
      ...Array.from({ length: 24 }, (_, index) => ({
        id: `file-${index + 1}`,
        filename: `file_${index + 1}.py`,
        updated_at: `2026-06-17T00:${String(index + 1).padStart(2, '0')}:00Z`,
      })),
    ]
    setupRoutes({ metadata })
    await renderAuthenticatedFeatureTree()

    fireEvent.change(aiPromptInput(), {
      target: { value: 'update a large project' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(screen.getByText(/AI edit included 20 of 25 files/)).toBeInTheDocument()
    })
    const body = JSON.parse(editRequests()[0]![2]!.body as string)
    expect(body.files).toHaveLength(20)
    expect(body.files[0]).toEqual({
      id: 'file-design-id',
      filename: 'design.py',
      updated_at: '2026-06-17T00:00:00Z',
    })
    expect(body.files.find((file: { filename: string }) => file.filename === 'file_20.py')).toBeUndefined()
    expect(screen.getByText(/AI updated 1 file/)).toBeInTheDocument()
  })

  it('clears prompt and variable edits, then explicitly refreshes features after success', async () => {
    setupRoutes()
    await renderAuthenticatedFeatureTree()

    fireEvent.change(screen.getAllByDisplayValue('100')[0]!, { target: { value: '125' } })
    await waitFor(() => {
      expect(aiPromptInput()).toHaveValue('Change length to 125.')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(screen.getByText(/AI updated 1 file/)).toBeInTheDocument()
    })
    expect(aiPromptInput()).toHaveValue('')
    expect(screen.getByDisplayValue('100')).toBeInTheDocument()
    expect(mocks.apiFetch.mock.calls.filter(([url]) => url === '/api/artus/features').length).toBeGreaterThanOrEqual(2)
  })

  it('queues an Intus compile after AI edit success without requiring the Intus tab to be active', async () => {
    setupRoutes()
    await renderAuthenticatedFeatureTree()

    fireEvent.change(aiPromptInput(), {
      target: { value: 'make it compile after edit' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(compileRequests()).toHaveLength(1)
    })
    const body = JSON.parse(compileRequests()[0]![2]!.body as string)
    expect(body).toEqual({
      code: 'updated design',
      export_format: 'glb',
      quality: 'sketch',
      file: 'design.py',
    })
    expect(screen.getByText(/Compile queued for the updated design/)).toBeInTheDocument()
  })

  it('shows backend conflict errors and refreshes metadata and features', async () => {
    setupRoutes({
      editStatus: 409,
      editBody: {
        success: false,
        error: 'Files changed while AI edit was running. Reload and try again.',
      },
    })
    await renderAuthenticatedFeatureTree()

    fireEvent.change(aiPromptInput(), {
      target: { value: 'make a conflicting edit' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply AI' }))

    await waitFor(() => {
      expect(screen.getByText(/Files changed while AI edit was running/)).toBeInTheDocument()
    })
    expect(mocks.apiFetch.mock.calls.filter(([url]) => url === '/api/intus/projects/default_purlin/files')).toHaveLength(2)
    expect(mocks.apiFetch.mock.calls.filter(([url]) => url === '/api/artus/features').length).toBeGreaterThanOrEqual(2)
  })

  it('disables AI edit immediately after a project selector change until Artus features refresh', async () => {
    let releaseFeatureRefresh: (() => void) | undefined
    mocks.apiFetch.mockImplementation((url: string, _token: unknown, options?: RequestInit) => {
      if (url === '/api/artus/features') {
        return new Promise((resolve) => {
          releaseFeatureRefresh = () => resolve(jsonResponse({
            project_name: 'project_b',
            features: [
              { name: 'width', value: 200, type: 'int', description: '' },
            ],
            operations: [],
          }))
        })
      }
      if (url === '/api/extus/status') {
        return Promise.resolve(jsonResponse({}))
      }
      if (url === '/api/intus/projects/project_b/files' && !options) {
        return Promise.resolve(jsonResponse({
          files: ['design.py'],
          file_metadata: [
            { id: 'file-design-b', filename: 'design.py', updated_at: '2026-06-17T00:00:00Z' },
          ],
        }))
      }
      if (url === '/api/intus/projects/project_b/files/llm-edit/jobs' && options?.method === 'POST') {
        return Promise.resolve(jsonResponse({ success: true, job_id: 'job-b-edit', status: 'queued' }, true, 202))
      }
      if (url === '/api/intus/projects/project_b/files/llm-edit/jobs/job-b-edit') {
        return Promise.resolve(jsonResponse({
          job_id: 'job-b-edit',
          status: 'succeeded',
          result: {
            success: true,
            outcome: 'changed',
            message: '',
            model: 'test-model',
            usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
            snapshot: { id: 'snap-1', message: 'edit', content_hash: 'hash' },
            files: [
              { id: 'file-design-b', filename: 'design.py', content: 'project b design', changed: true },
            ],
          },
        }))
      }
      if (url === '/api/intus/projects/project_b/compile' && options?.method === 'POST') {
        return Promise.resolve(jsonResponse({ success: true, job_id: 'job-b', status: 'queued', format: 'glb' }, true, 202))
      }
      return Promise.resolve(jsonResponse({ error: `Unhandled ${url}` }, false, 404))
    })

    render(<FeatureTreeTab serverUrl="/api/artus" />)
    window.dispatchEvent(new CustomEvent('tertius:active-project-changed', { detail: { activeProject: 'project_b' } }))
    fireEvent.change(aiPromptInput(), {
      target: { value: 'edit the newly selected project' },
    })
    const applyButton = screen.getByRole('button', { name: 'Apply AI' })

    expect(applyButton).toBeDisabled()
    fireEvent.click(applyButton)
    expect(editRequests()).toHaveLength(0)

    releaseFeatureRefresh?.()
    await screen.findByDisplayValue('200')
    expect(applyButton).toBeEnabled()

    fireEvent.click(applyButton)
    await waitFor(() => {
      expect(mocks.apiFetch.mock.calls.some(([url]) => url === '/api/intus/projects/project_b/files/llm-edit/jobs')).toBe(true)
    })
    expect(mocks.apiFetch.mock.calls.some(([url]) => url === '/api/intus/projects/default_purlin/files/llm-edit/jobs')).toBe(false)
  })
})
