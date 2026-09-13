import { describe, expect, it } from 'vitest'
import * as THREE from 'three'

import { annotateGltfNodeIds } from './ViewerTab'

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
