import * as THREE from 'three'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  datedAssemblyExportFilename,
  isAssemblyTreeNode,
  isGeneratedOrDefaultName,
  sanitizePathSegment,
} from './featureTree'

describe('Artus feature tree model', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps assembly containers and excludes leaf geometry and unsupported scene nodes', () => {
    const group = new THREE.Group()
    const object = new THREE.Object3D()
    const leafMesh = new THREE.Mesh()
    const containerMesh = new THREE.Mesh()
    containerMesh.add(new THREE.Object3D())

    expect(isAssemblyTreeNode(group)).toBe(true)
    expect(isAssemblyTreeNode(object)).toBe(true)
    expect(isAssemblyTreeNode(containerMesh)).toBe(true)
    expect(isAssemblyTreeNode(leafMesh)).toBe(false)
    expect(isAssemblyTreeNode(new THREE.AmbientLight())).toBe(false)
  })

  it('classifies the existing default and generated BOM candidate names exactly', () => {
    expect(isGeneratedOrDefaultName(' Component ')).toBe(true)
    expect(isGeneratedOrDefaultName('mesh-12')).toBe(true)
    expect(isGeneratedOrDefaultName('=>12_3')).toBe(true)
    expect(isGeneratedOrDefaultName('123e4567-e89b-12d3-a456-426614174000')).toBe(true)
    expect(isGeneratedOrDefaultName('Roof Assembly')).toBe(false)
  })

  it('sanitizes tree path segments while preserving the existing fallback results', () => {
    expect(sanitizePathSegment(' Roof / East ', 'Component_1')).toBe('Roof _ East')
    expect(sanitizePathSegment('', 'Component_1')).toBe('Component_1')
    expect(sanitizePathSegment('///', 'Component_1')).toBe('___')
  })

  it('builds the existing dated BOM candidate export filenames', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-16T13:00:00Z'))

    expect(datedAssemblyExportFilename('json', ' My Roof / Model ')).toBe(
      'assembly-tree-candidates-my-roof-model-2026-08-16.json',
    )
    expect(datedAssemblyExportFilename('csv', '---')).toBe(
      'assembly-tree-candidates-2026-08-16.csv',
    )
  })
})
