import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  analyzePotentialCollisions,
  analyzePotentialCollisionsAsync,
  collectCollisionComponents,
  collisionDisplayLabel,
  collisionGroupFromLabel,
} from './collisionAnalysis'

function component(
  name: string,
  callId: string,
  position: [number, number, number],
  size: [number, number, number] = [0.01, 0.01, 0.01],
) {
  const group = new THREE.Group()
  group.name = name
  group.position.set(...position)
  group.userData.tertiusSourceCallIds = [callId]
  group.add(new THREE.Mesh(new THREE.BoxGeometry(...size), new THREE.MeshBasicMaterial()))
  return group
}

describe('Extus collision analysis', () => {
  it('groups meshes by provenance-bearing component and maps them to design.py', () => {
    const root = new THREE.Group()
    root.userData.tertiusSourceMap = {
      source_calls: {
        call_wall: {
          function: 'make_wall_header',
          source_file: 'design.py',
          source_line: 214,
        },
      },
    }
    const wall = component('Long wall header', 'call_wall', [0, 0, 0])
    wall.add(new THREE.Mesh(new THREE.BoxGeometry(0.002, 0.002, 0.002), new THREE.MeshBasicMaterial()))
    root.add(wall)

    const components = collectCollisionComponents(root)

    expect(components).toHaveLength(1)
    expect(components[0]?.meshCount).toBe(2)
    expect(components[0]?.sourceReferences[0]).toMatchObject({
      callId: 'call_wall',
      functionName: 'make_wall_header',
      sourceFile: 'design.py',
      sourceLine: 214,
    })
  })

  it('finds and ranks penetrating component bounds while ignoring separated parts', () => {
    const root = new THREE.Group()
    root.add(component('Portal column', 'call_column', [0, 0, 0]))
    root.add(component('Wall header', 'call_header', [0.004, 0, 0]))
    root.add(component('Remote purlin', 'call_purlin', [0.1, 0, 0]))

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(3)
    expect(result.totalPairCount).toBe(1)
    expect(result.pairs[0]?.a.label).toBe('Portal column')
    expect(result.pairs[0]?.b.label).toBe('Wall header')
    expect(result.pairs[0]?.overlapSize.x).toBeCloseTo(0.006)
    expect(result.pairs[0]?.overlapSize.y).toBeCloseTo(0.01)
    expect(result.pairs[0]?.overlapSize.z).toBeCloseTo(0.01)
  })

  it('still treats nearest named groups as components when an older model has no source map', () => {
    const root = new THREE.Group()
    const a = new THREE.Group()
    a.name = 'Legacy column'
    a.add(new THREE.Mesh(new THREE.BoxGeometry(0.01, 0.01, 0.01), new THREE.MeshBasicMaterial()))
    const b = new THREE.Group()
    b.name = 'Legacy header'
    b.position.x = 0.004
    b.add(new THREE.Mesh(new THREE.BoxGeometry(0.01, 0.01, 0.01), new THREE.MeshBasicMaterial()))
    root.add(a, b)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(2)
    expect(result.totalPairCount).toBe(1)
    expect(result.pairs[0]?.a.label).toBe('Legacy column')
    expect(result.pairs[0]?.b.label).toBe('Legacy header')
  })

  it('ignores analytical overlay meshes', () => {
    const root = new THREE.Group()
    root.add(component('Physical member', 'call_member', [0, 0, 0]))
    const overlay = component('Moment ribbon', 'call_overlay', [0, 0, 0])
    overlay.traverse((node) => { node.userData.tertiusStructuralOverlay = true })
    root.add(overlay)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(1)
    expect(result.totalPairCount).toBe(0)
  })

  it('ignores components explicitly excluded through exported BoM metadata', () => {
    const root = new THREE.Group()
    root.add(component('Portal column', 'call_column', [0, 0, 0]))
    const strap = component('Flexible strap', 'call_strap', [0, 0, 0])
    strap.userData.tertiusBom = {
      collision_check: false,
      collision_ignore_reason: 'flexible tension strap',
    }
    root.add(strap)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(1)
    expect(result.totalPairCount).toBe(0)
  })

  it('honors exclusion metadata below a provenance-bearing assembly', () => {
    const root = new THREE.Group()
    root.add(component('Portal column', 'call_column', [0, 0, 0]))

    const strapAssembly = new THREE.Group()
    strapAssembly.name = 'Flexible strap assembly'
    strapAssembly.userData.tertiusSourceCallIds = ['call_strap']
    const renderedStrap = new THREE.Group()
    renderedStrap.userData.tertiusBom = {
      collision_check: false,
      collision_ignore_reason: 'flexible tension strap',
    }
    renderedStrap.add(new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    ))
    strapAssembly.add(renderedStrap)
    root.add(strapAssembly)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(1)
    expect(result.totalPairCount).toBe(0)
  })

  it('honors the exported design label marker below a provenance-bearing assembly', () => {
    const root = new THREE.Group()
    root.add(component('Portal column', 'call_column', [0, 0, 0]))

    const strapAssembly = new THREE.Group()
    strapAssembly.name = 'Flexible strap assembly'
    strapAssembly.userData.tertiusSourceCallIds = ['call_strap']
    const renderedStrap = new THREE.Group()
    renderedStrap.name = '__TERTIUS_COLLISION_IGNORE__ flexible strap'
    renderedStrap.add(new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    ))
    strapAssembly.add(renderedStrap)
    root.add(strapAssembly)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(1)
    expect(result.totalPairCount).toBe(0)
  })

  it('honors a collision marker copied from a glTF mesh definition', () => {
    const root = new THREE.Group()
    root.add(component('Portal column', 'call_column', [0, 0, 0]))
    const strap = component('Flexible strap', 'call_strap', [0, 0, 0])
    strap.children[0]!.userData.tertiusCollisionCheckDisabled = true
    root.add(strap)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(1)
    expect(result.totalPairCount).toBe(0)
  })

  it('skips overlaps only between components in the same collision group', () => {
    const root = new THREE.Group()
    const firstSheet = component('Roof sheet 1', 'call_sheet_1', [0, 0, 0])
    const secondSheet = component('Roof sheet 2', 'call_sheet_2', [0.004, 0, 0])
    firstSheet.userData.tertiusCollisionGroup = 'roof-sheet-left'
    secondSheet.userData.tertiusCollisionGroup = 'roof-sheet-left'
    root.add(firstSheet, secondSheet)
    root.add(component('Purlin', 'call_purlin', [0.002, 0, 0]))

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.totalPairCount).toBe(2)
    expect(result.pairs.every(pair => pair.a.label === 'Purlin' || pair.b.label === 'Purlin')).toBe(true)
  })

  it('reads a pair-scoped collision group marker below a provenance component', () => {
    const root = new THREE.Group()
    const markedSheet = (name: string, callId: string, x: number) => {
      const sheet = component(name, callId, [x, 0, 0])
      sheet.children[0]!.name = `__TERTIUS_COLLISION_GROUP__ roof-sheet-left :: ${name}`
      return sheet
    }
    root.add(markedSheet('Roof sheet 1', 'call_sheet_1', 0))
    root.add(markedSheet('Roof sheet 2', 'call_sheet_2', 0.004))

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(2)
    expect(result.totalPairCount).toBe(0)
  })

  it('reads and hides exporter-normalized collision policy markers', () => {
    const marked = '__TERTIUS_COLLISION_GROUP___roof-sheet-right__Right_Roof_Sheet_1'

    expect(collisionGroupFromLabel(marked)).toBe('roof-sheet-right')
    expect(collisionDisplayLabel(marked)).toBe('Right_Roof_Sheet_1')
    expect(collisionDisplayLabel('__TERTIUS_COLLISION_IGNORE___Flexible_batt')).toBe('Flexible_batt')
  })

  it('does not report empty space inside a multi-mesh component bound', () => {
    const root = new THREE.Group()
    const frame = component('Split frame', 'call_frame', [-1, 0, 0], [0.1, 0.1, 0.1])
    const secondPost = new THREE.Mesh(
      new THREE.BoxGeometry(0.1, 0.1, 0.1),
      new THREE.MeshBasicMaterial(),
    )
    secondPost.position.x = 2
    frame.add(secondPost)
    root.add(frame)
    root.add(component('Middle object', 'call_middle', [0, 0, 0], [0.1, 0.1, 0.1]))

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.componentCount).toBe(2)
    expect(result.totalPairCount).toBe(0)
  })

  it('retains only overlapping rendered mesh pairs for exact verification', () => {
    const root = new THREE.Group()
    const split = component('Split component', 'call_split', [0, 0, 0])
    const remoteMesh = new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    )
    remoteMesh.position.x = 1
    split.add(remoteMesh)
    root.add(split)
    const near = component('Near object', 'call_near', [0.004, 0, 0])
    const grazingMesh = new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    )
    grazingMesh.position.x = 0.0055
    near.add(grazingMesh)
    root.add(near)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.totalPairCount).toBe(1)
    expect(result.pairs[0]?.meshPairs).toHaveLength(2)
    expect(result.pairs[0]?.meshPairs[0]?.[0]).toBe(split.children[0])
  })

  it('retains near-touching mesh pairs for rotated exact verification', () => {
    const root = new THREE.Group()
    const split = component('Split component', 'call_split', [0, 0, 0])
    const extent = new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    )
    extent.position.x = 0.02
    split.add(extent)
    root.add(split)

    const grazing = component(
      'Rotated grazing object',
      'call_grazing',
      [0.004, 0, 0],
      [0.01, 0.01, 0.01],
    )
    const nearlyTouching = new THREE.Mesh(
      new THREE.BoxGeometry(0.01, 0.01, 0.01),
      new THREE.MeshBasicMaterial(),
    )
    nearlyTouching.position.x = 0.006
    grazing.add(nearlyTouching)
    root.add(grazing)

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })

    expect(result.totalPairCount).toBe(1)
    expect(result.pairs[0]?.overlapSize.x).toBeGreaterThanOrEqual(0.001)
    expect(result.pairs[0]?.meshPairs).toHaveLength(3)
  })

  it('runs the yielding broad phase without changing collision results', async () => {
    const root = new THREE.Group()
    root.add(component('A', 'call_a', [0, 0, 0]))
    root.add(component('B', 'call_b', [0.004, 0, 0]))
    root.add(component('C', 'call_c', [1, 0, 0]))

    const synchronous = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })
    const asynchronous = await analyzePotentialCollisionsAsync(root, { minimumPenetrationMm: 1 })

    expect(asynchronous.totalPairCount).toBe(synchronous.totalPairCount)
    expect(asynchronous.pairs.map(pair => pair.id)).toEqual(synchronous.pairs.map(pair => pair.id))
  })

  it('can cancel the yielding broad phase', async () => {
    const root = new THREE.Group()
    root.add(component('A', 'call_a', [0, 0, 0]))
    const controller = new AbortController()
    controller.abort()

    await expect(analyzePotentialCollisionsAsync(root, { signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' })
  })

  it('uses the minimum penetration threshold to suppress touching and tiny overlaps', () => {
    const root = new THREE.Group()
    root.add(component('A', 'call_a', [0, 0, 0]))
    root.add(component('B', 'call_b', [0.0095, 0, 0]))

    expect(analyzePotentialCollisions(root, { minimumPenetrationMm: 1 }).totalPairCount).toBe(0)
    expect(analyzePotentialCollisions(root, { minimumPenetrationMm: 0.25 }).totalPairCount).toBe(1)
  })

  it('caps the visible ranked list without losing the total pair count', () => {
    const root = new THREE.Group()
    root.add(component('A', 'call_a', [0, 0, 0]))
    root.add(component('B', 'call_b', [0.001, 0, 0]))
    root.add(component('C', 'call_c', [0.002, 0, 0]))

    const result = analyzePotentialCollisions(root, { minimumPenetrationMm: 1, maxPairs: 1 })

    expect(result.totalPairCount).toBe(3)
    expect(result.pairs).toHaveLength(1)
    expect(result.truncated).toBe(true)
    expect(result.pairs[0]?.overlapVolume).toBeCloseTo(0.0000009)
  })
})
