import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  DEFAULT_MODEL_COLOR,
  buildViewerBatch,
  hasAuthoredMaterialColor,
} from './ViewerTab'

function meshWithPositions(material: THREE.Material) {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3))
  return new THREE.Mesh(geometry, material)
}

describe('ViewerTab material batching', () => {
  it('detects Build123D-authored GLTF material colours', () => {
    const material = new THREE.MeshStandardMaterial({ color: 0xff0000 })
    material.userData.tertiusAuthoredColor = true

    expect(hasAuthoredMaterialColor(material)).toBe(true)
  })

  it('uses vertex colours when loaded GLTF meshes contain authored colours', () => {
    const redMaterial = new THREE.MeshStandardMaterial({ color: 0xff0000 })
    redMaterial.userData.tertiusAuthoredColor = true
    const mesh = meshWithPositions(redMaterial)
    const createMesh = vi.fn((geometry: THREE.BufferGeometry, material: THREE.Material) => new THREE.Mesh(geometry, material))

    const batch = buildViewerBatch([mesh], { createMesh })

    expect(batch).not.toBeNull()
    if (!batch) throw new Error('expected viewer batch')
    expect(batch.usesAuthoredColors).toBe(true)
    expect(createMesh).toHaveBeenCalledTimes(1)
    expect((batch.mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(true)
    expect(batch.mesh.geometry.getAttribute('color')).toBeDefined()
  })

  it('keeps the existing steel default material for uncoloured meshes', () => {
    const mesh = meshWithPositions(new THREE.MeshStandardMaterial())
    const createMesh = vi.fn((geometry: THREE.BufferGeometry, material: THREE.Material) => new THREE.Mesh(geometry, material))

    const batch = buildViewerBatch([mesh], { createMesh })

    expect(batch).not.toBeNull()
    if (!batch) throw new Error('expected viewer batch')
    expect(batch.usesAuthoredColors).toBe(false)
    expect((batch.mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(false)
    expect((batch.mesh.material as THREE.MeshStandardMaterial).color.getHex()).toBe(DEFAULT_MODEL_COLOR)
    expect(batch.mesh.geometry.getAttribute('color')).toBeUndefined()
  })

  it('can force default vertex colours for uncoloured chunks in a mixed coloured assembly', () => {
    const mesh = meshWithPositions(new THREE.MeshStandardMaterial())
    const createMesh = vi.fn((geometry: THREE.BufferGeometry, material: THREE.Material) => new THREE.Mesh(geometry, material))

    const batch = buildViewerBatch([mesh], { createMesh, useAuthoredColors: true })

    expect(batch).not.toBeNull()
    if (!batch) throw new Error('expected viewer batch')
    expect(batch.usesAuthoredColors).toBe(true)
    expect((batch.mesh.material as THREE.MeshStandardMaterial).vertexColors).toBe(true)
    expect(batch.mesh.geometry.getAttribute('color')).toBeDefined()
  })
})
