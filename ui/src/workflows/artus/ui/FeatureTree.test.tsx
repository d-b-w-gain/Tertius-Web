import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FeatureTree } from './FeatureTree'

describe('FeatureTree', () => {
  afterEach(cleanup)

  it('delegates node selection and viewer controls to its owner', () => {
    const sceneGraph = new THREE.Group()
    const frame = new THREE.Group()
    frame.name = 'Frame'
    sceneGraph.add(frame)

    const onSelect = vi.fn()
    const onTarget = vi.fn()
    const onToggleVisibility = vi.fn()
    const onToggleTransparency = vi.fn()

    render(
      <FeatureTree
        isActive
        sceneGraph={sceneGraph}
        selectedValue={null}
        appearanceByPath={{}}
        onActivate={vi.fn()}
        onExportJson={vi.fn()}
        onExportCsv={vi.fn()}
        onSelect={onSelect}
        onTarget={onTarget}
        onToggleVisibility={onToggleVisibility}
        onToggleTransparency={onToggleTransparency}
      />,
    )

    fireEvent.click(screen.getByText('Frame'))
    fireEvent.doubleClick(screen.getByText('Frame'))
    fireEvent.click(screen.getByRole('button', { name: 'Frame Frame' }))
    fireEvent.click(screen.getByRole('button', { name: 'Hide Frame' }))
    fireEvent.click(screen.getByRole('button', { name: 'Make Frame transparent' }))

    expect(onSelect).toHaveBeenNthCalledWith(1, frame, false)
    expect(onSelect).toHaveBeenNthCalledWith(2, frame, true)
    expect(onTarget).toHaveBeenCalledWith(frame)
    expect(onToggleVisibility).toHaveBeenCalledWith(frame)
    expect(onToggleTransparency).toHaveBeenCalledWith(frame)
  })
})
