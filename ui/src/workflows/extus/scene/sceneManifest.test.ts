import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'

import { annotateGltfNodeIds } from '../ui/ViewerTab'
import {
  assembleScene,
  createSceneAssetCache,
  isSceneManifest,
  SCENE_SCHEMA_VERSION,
  type AnnotateGltf,
  type SceneManifest,
} from './sceneManifest'

const IDENTITY = new THREE.Matrix4().toArray()
const translated = (x: number) => new THREE.Matrix4().makeTranslation(x, 0, 0).toArray()

// Each fake asset is one root node holding one mesh, with the glTF JSON and
// loader associations GLTFLoader would give it; `extra` adds an own child node.
function fakeAsset(name: string, color: number, digest: string, extra?: string): GLTF {
  const scene = new THREE.Group()
  const root = new THREE.Group()
  root.name = name
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial({ color }))
  root.add(mesh)
  const associations = new Map<THREE.Object3D, { nodes?: number; meshes?: number; primitives?: number }>([
    [root, { nodes: 0 }],
    [mesh, { nodes: 0, meshes: 0, primitives: 0 }],
  ])
  const nodes: Array<Record<string, unknown>> = [{ name, mesh: 0, ...(extra ? { children: [1] } : {}) }]
  if (extra) {
    const own = new THREE.Group()
    own.name = extra
    root.add(own)
    associations.set(own, { nodes: 1 })
    nodes.push({ name: extra })
  }
  scene.add(root)
  return {
    scene,
    parser: { json: { nodes, scenes: [{ nodes: [0] }], meshes: [{ extras: { tertiusMeshDigest: digest } }] }, associations },
  } as unknown as GLTF
}

function manifest(post = 'post'): SceneManifest {
  return {
    schema_version: SCENE_SCHEMA_VERSION,
    root: {
      name: 'Shed',
      matrix: IDENTITY,
      asset: 'root',
      visual: { source_call_ids: ['call-1'] },
      children: [
        { name: 'Post', matrix: translated(1), asset: post, children: [] },
        { name: 'Frame', matrix: IDENTITY, visual: { source_call_ids: ['call-2'] }, children: [] },
        { name: 'Post', matrix: translated(2), asset: post, children: [] },
      ],
    },
    source_map: { 'call-1': { line: 3 } },
    assets: ['root', post],
  }
}

function options(cache = createSceneAssetCache(), materials = new Map<string, THREE.Material>()) {
  const fetched: string[] = []
  const buffers = new Map<ArrayBuffer, string>()
  return {
    fetched,
    cache,
    materials,
    fetchAsset: async (assetId: string) => {
      fetched.push(assetId)
      const buffer = new ArrayBuffer(1)
      buffers.set(buffer, assetId)
      return buffer
    },
    parse: async (buffer: ArrayBuffer) => {
      // A fresh parse each time, as the loader would give.
      const assetId = buffers.get(buffer) as string
      return assetId === 'root'
        ? fakeAsset('Shed', 0x808080, 'd-root', 'Frame')
        : fakeAsset('Post', 0xff0000, 'd-' + assetId)
    },
    annotate: annotateGltfNodeIds as unknown as AnnotateGltf,
  }
}

describe('scene manifests', () => {
  it('recognises only v1 manifests with a root asset', () => {
    expect(isSceneManifest(manifest())).toBe(true)
    expect(isSceneManifest({ ...manifest(), schema_version: 'tertius.scene.v0' })).toBe(false)
    expect(isSceneManifest({ ...manifest(), root: { ...manifest().root, asset: undefined } })).toBe(false)
    expect(isSceneManifest(null)).toBe(false)
  })

  it('assembles the manifest tree from its assets', async () => {
    const opts = options()
    const { scene, fetched, assets } = await assembleScene(manifest(), opts)

    expect([fetched, assets]).toEqual([2, 2])
    expect(scene.userData.tertiusSourceMap).toEqual({ 'call-1': { line: 3 } })
    const root = scene.children[0] as THREE.Object3D
    expect(root.name).toBe('Shed')
    expect(root.userData.tertiusSourceCallIds).toEqual(['call-1'])
    // The root's own mesh primitive first, then the manifest children in order.
    expect(root.children.map((child) => child.name)).toEqual(['', 'Post', 'Frame', 'Post'])
    const [, first, frame, second] = root.children as [THREE.Object3D, THREE.Object3D, THREE.Object3D, THREE.Object3D]
    expect(frame.userData.tertiusSourceCallIds).toEqual(['call-2'])
    expect(first.position.x).toBe(1)
    expect(second.position.x).toBe(2)
    // Node ids are unique per placed instance; geometry keys are shared.
    expect(first.userData.tertiusGltfNodeId).not.toBe(second.userData.tertiusGltfNodeId)
    const firstMesh = first.children[0] as THREE.Mesh
    const secondMesh = second.children[0] as THREE.Mesh
    expect(firstMesh.userData.tertiusGeometryKey).toBe('digest:d-post:primitive:0')
    expect(secondMesh.userData.tertiusGeometryKey).toBe(firstMesh.userData.tertiusGeometryKey)
    expect(secondMesh.material).toBe(firstMesh.material)
  })

  it('fetches only assets not already cached', async () => {
    const cache = createSceneAssetCache()
    const materials = new Map<string, THREE.Material>()
    await assembleScene(manifest(), options(cache, materials))

    const edit = options(cache, materials)
    const { fetched } = await assembleScene(manifest('postCopy'), edit)
    expect(fetched).toBe(1)
    expect(edit.fetched).toEqual(['postCopy'])
    expect([...cache.keys()].sort()).toEqual(['postCopy', 'root'])
  })

  it('forgets an asset whose download failed', async () => {
    const cache = createSceneAssetCache()
    const failing = { ...options(cache), fetchAsset: async () => { throw new Error('offline') } }
    await expect(assembleScene(manifest(), failing)).rejects.toThrow('offline')
    expect(cache.get('root')).toBeUndefined()
  })
})
