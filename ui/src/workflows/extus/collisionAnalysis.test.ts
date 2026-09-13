import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { analyzePotentialCollisions, collectCollisionComponents } from './collisionAnalysis'

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
