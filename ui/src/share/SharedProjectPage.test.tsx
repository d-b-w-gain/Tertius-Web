import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SharedProjectPage } from './SharedProjectPage'

const viewer = vi.hoisted(() => ({ props: [] as Array<Record<string, unknown>> }))

vi.mock('../workflows/extus/ui/ViewerTab', () => ({
  LatestModelViewer: (props: Record<string, unknown>) => {
    viewer.props.push(props)
    return <div data-testid="viewer" />
  },
}))

describe('SharedProjectPage (#417)', () => {
  it('opens the shared Extus routes with the link token', () => {
    render(<SharedProjectPage token="abc.def" />)

    expect(screen.getByText('Shared read-only view')).toBeInTheDocument()
    const props = viewer.props.at(-1)
    expect(String(props?.serverUrl)).toMatch(/\/extus\/shared$/)
    expect(props?.requestHeaders).toEqual({ 'X-Tertius-Share-Token': 'abc.def' })
    expect(props?.onShare).toBeUndefined()
  })

  it('explains an incomplete link instead of loading anything', () => {
    viewer.props.length = 0
    render(<SharedProjectPage token={null} />)

    expect(screen.getByText(/share link is incomplete/i)).toBeInTheDocument()
    expect(viewer.props).toHaveLength(0)
  })
})
