import { describe, expect, it } from 'vitest'
import * as THREE from 'three'

import { annotateGltfNodeIds, centreModelInPlan } from './ViewerTab'

describe('ViewerTab GLTF associations', () => {
  it('ignores empty loader associations and annotates explicit primitive identities', () => {
    const root = new THREE.Group()
    const ignored = new THREE.Group()
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial())
    root.add(ignored, mesh)
    const associations = new Map<THREE.Object3D, {
      nodes?: number
      meshes?: number
      primitives?: number
    } | undefined>([
      [ignored, undefined],
      [mesh, { nodes: 11, meshes: 7, primitives: 2 }],
    ])

    expect(() => annotateGltfNodeIds(root, {}, associations)).not.toThrow()
    expect(mesh.userData).toMatchObject({
      tertiusGltfNodeId: '11',
      tertiusGltfMeshId: 7,
      tertiusGltfPrimitiveId: 2,
    })
  })
})

describe('centring a loaded model (#420)', () => {
  // A glTF (Y-up) model 10 long in X, 3 tall and 20 long along CAD Y, which
  // glTF stores as -Z, offset from the origin as a design often is.
  function yUpModel() {
    const model = new THREE.Group()
    const part = new THREE.Mesh(new THREE.BoxGeometry(10, 3, 20), new THREE.MeshBasicMaterial())
    part.position.set(105, 1.5, -40)     // CAD: x 100..110, y 30..50, z 0..3
    model.add(part)
    return model
  }

  it('centres the model in plan and keeps its real height', () => {
    const model = yUpModel()
    model.rotation.x = Math.PI / 2       // the viewer's Y-up to Z-up turn

    const box = centreModelInPlan(model)

    const world = new THREE.Box3().setFromObject(model)
    for (const bounds of [box, world]) {
      expect(bounds.min.x).toBeCloseTo(-5)
      expect(bounds.max.x).toBeCloseTo(5)
      expect(bounds.min.y).toBeCloseTo(-10)
      expect(bounds.max.y).toBeCloseTo(10)
      expect(bounds.min.z).toBeCloseTo(0)
      expect(bounds.max.z).toBeCloseTo(3)
    }
  })

  it('does not move the model up or down with its length, as the old centring did', () => {
    const model = yUpModel()
    // The old code: centre in glTF axes, then turn.
    const oldCentre = new THREE.Box3().setFromObject(model).getCenter(new THREE.Vector3())
    model.position.sub(oldCentre)
    model.rotation.x = Math.PI / 2
    model.updateMatrixWorld(true)
    expect(new THREE.Box3().setFromObject(model).min.z).toBeCloseTo(40)    // floats 40 up

    const fixed = yUpModel()
    fixed.rotation.x = Math.PI / 2
    expect(centreModelInPlan(fixed).min.z).toBeCloseTo(0)
  })
})
