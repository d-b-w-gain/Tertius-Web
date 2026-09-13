import { describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import {
  DEFAULT_MODEL_COLOR,
  createViewerMeshMaterials,
  hasAuthoredMaterialColor,
} from '../scene/materials'
import {
  applyViewerGeometryTransform,
  buildViewerBatch,
  isViewerObjectHidden,
} from '../scene/batching'

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

  it('preserves authored part colours in selection and transparency overlay materials', () => {
    const fallbackMaterial = new THREE.MeshStandardMaterial({ color: DEFAULT_MODEL_COLOR })
    const redMaterial = new THREE.MeshStandardMaterial({ color: 0xff0000 })
    redMaterial.userData.tertiusAuthoredColor = true

    const materials = createViewerMeshMaterials(redMaterial, fallbackMaterial)

    expect((materials.base as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
    expect((materials.highlight as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
    expect((materials.transparent as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
    expect((materials.transparent as THREE.MeshStandardMaterial).transparent).toBe(true)
    expect((materials.transparentHighlight as THREE.MeshStandardMaterial).color.getHex()).toBe(0xff0000)
  })

  it('excludes Assembly Tree-hidden objects and their children from picker hits', () => {
    const root = new THREE.Group()
    const component = new THREE.Group()
    const mesh = meshWithPositions(new THREE.MeshStandardMaterial())
    root.add(component)
    component.add(mesh)

    expect(isViewerObjectHidden(root, mesh, {})).toBe(false)
    expect(isViewerObjectHidden(root, mesh, { 'path:0': { hidden: true } })).toBe(true)
    expect(isViewerObjectHidden(root, mesh, { 'path:0.0': { hidden: true } })).toBe(true)
  })

  it('reverses triangle winding when a mirrored instance transform is baked for batching', () => {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([
      0, 0, 0,
      1, 0, 0,
      0, 1, 0,
    ]), 3))
    geometry.setIndex([0, 1, 2])

    applyViewerGeometryTransform(geometry, new THREE.Matrix4().makeScale(-1, 1, 1))

    expect(Array.from(geometry.getIndex()!.array)).toEqual([0, 2, 1])
    expect(geometry.getAttribute('position').getX(1)).toBe(-1)
  })

  it('reverses every attribute for mirrored non-indexed triangle geometry', () => {
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(new Float32Array([
      0, 0, 0,
      1, 0, 0,
      0, 1, 0,
    ]), 3))
    geometry.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([
      0, 0,
      1, 0,
      0, 1,
    ]), 2))

    applyViewerGeometryTransform(geometry, new THREE.Matrix4().makeScale(-1, 1, 1))

    const position = geometry.getAttribute('position')
    const uv = geometry.getAttribute('uv')
    expect([position.getX(1), position.getY(1)]).toEqual([0, 1])
    expect([position.getX(2), position.getY(2)]).toEqual([-1, 0])
    expect([uv.getX(1), uv.getY(1)]).toEqual([0, 1])
    expect([uv.getX(2), uv.getY(2)]).toEqual([1, 0])
  })
})
