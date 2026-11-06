import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ShareProjectDialog } from './ShareProjectDialog'

const mocks = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ apiFetch: mocks.apiFetch }))

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const existing = { id: 's1', token: 's1.sig', workbench: 'extus', created_at: '2026-11-06T01:00:00Z' }

describe('ShareProjectDialog (#417)', () => {
  const getAccessToken = vi.fn()

  beforeEach(() => {
    mocks.apiFetch.mockReset()
  })

  it('lists, creates, copies and revokes the active project links', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    mocks.apiFetch
      .mockResolvedValueOnce(json({ project_name: 'shed', shares: [existing] }))
      .mockResolvedValueOnce(json({ ...existing, id: 's2', token: 's2.sig' }, 201))
      .mockResolvedValueOnce(json({ revoked: true }))

    render(<ShareProjectDialog serverUrl="/api/extus" getAccessToken={getAccessToken} onClose={vi.fn()} />)

    expect(await screen.findByText('Share shed')).toBeInTheDocument()
    expect(screen.getByDisplayValue(`${window.location.origin}/share#s1.sig`)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Create link' }))
    expect(await screen.findByDisplayValue(`${window.location.origin}/share#s2.sig`)).toBeInTheDocument()
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/extus/shares', getAccessToken, { method: 'POST' })

    await act(async () => {
      fireEvent.click(screen.getAllByRole('button', { name: 'Copy' })[0]!)
    })
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/share#s2.sig`)

    fireEvent.click(screen.getAllByRole('button', { name: 'Revoke' })[1]!)
    await waitFor(() => expect(screen.queryByDisplayValue(`${window.location.origin}/share#s1.sig`)).not.toBeInTheDocument())
    expect(mocks.apiFetch).toHaveBeenCalledWith('/api/extus/shares/s1', getAccessToken, { method: 'DELETE' })
  })

  it('shows why sharing is unavailable', async () => {
    mocks.apiFetch.mockResolvedValueOnce(json({ error: 'Share links are not configured' }, 503))

    render(<ShareProjectDialog serverUrl="/api/extus" getAccessToken={getAccessToken} onClose={vi.fn()} />)

    expect(await screen.findByText('Share links are not configured')).toBeInTheDocument()
  })
})
