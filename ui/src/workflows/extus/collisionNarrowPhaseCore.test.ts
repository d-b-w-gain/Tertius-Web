import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js'
import { analyzeCollisionMeshes } from './collisionNarrowPhaseCore'
import type { SerializedCollisionMesh } from './collisionNarrowPhase.types'

const serializedMesh = (
  id: string,
  geometry: THREE.BufferGeometry,
  position: [number, number, number] = [0, 0, 0],
): SerializedCollisionMesh => {
  const positionAttribute = geometry.getAttribute('position')
  const positions = new Float32Array(positionAttribute.count * 3)
  for (let index = 0; index < positionAttribute.count; index += 1) {
    positions[index * 3] = positionAttribute.getX(index)
    positions[index * 3 + 1] = positionAttribute.getY(index)
    positions[index * 3 + 2] = positionAttribute.getZ(index)
  }
  const sourceIndex = geometry.getIndex()
  const indices = sourceIndex
    ? Uint32Array.from({ length: sourceIndex.count }, (_, index) => sourceIndex.getX(index))
    : undefined
  const matrixWorld = new THREE.Matrix4().makeTranslation(...position).toArray()
  geometry.dispose()
  return { id, positions, indices, matrixWorld }
}

const reverseWinding = (geometry: THREE.BufferGeometry): THREE.BufferGeometry => {
  const reversed = geometry.clone()
  const index = reversed.getIndex()
  if (!index) throw new Error('Expected indexed test geometry')
  for (let offset = 0; offset < index.count; offset += 3) {
    const first = index.getX(offset)
    index.setX(offset, index.getX(offset + 1))
    index.setX(offset + 1, first)
  }
  geometry.dispose()
  return reversed
}

const reverseFirstTriangle = (geometry: THREE.BufferGeometry): THREE.BufferGeometry => {
  const reversed = geometry.clone()
  const index = reversed.getIndex()
  if (!index) throw new Error('Expected indexed test geometry')
  const first = index.getX(0)
  index.setX(0, index.getX(1))
  index.setX(1, first)
  geometry.dispose()
  return reversed
}

describe('Extus mesh collision narrow phase', () => {
  it('confirms crossing triangle surfaces and returns a world-space contact', () => {
    const meshes = [
      serializedMesh('a', new THREE.BoxGeometry(1, 1, 1)),
      serializedMesh('b', new THREE.BoxGeometry(1, 1, 1), [0.5, 0, 0]),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'a::b',
      meshPairs: [['a', 'b']],
      minimumPenetrationSceneUnits: 0.1,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches).toHaveLength(1)
    expect(matches[0]?.pairId).toBe('a::b')
    expect(matches[0]?.contactPoint.every(Number.isFinite)).toBe(true)
  })

  it('rejects separated triangles even when their axis-aligned boxes overlap', () => {
    const triangle = (values: number[]) => {
      const geometry = new THREE.BufferGeometry()
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(values, 3))
      return geometry
    }
    const meshes = [
      serializedMesh('lower-left', triangle([0, 0, 0, 1, 0, 0, 0, 1, 0])),
      serializedMesh('upper-right', triangle([0.7, 0.7, 0, 1, 0.7, 0, 0.7, 1, 0])),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'triangles',
      meshPairs: [['lower-left', 'upper-right']],
      minimumPenetrationSceneUnits: 0.1,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches).toEqual([])
  })

  it('does not mistake a panel inside a framed opening for a collision', () => {
    const meshes = [
      serializedMesh('left', new THREE.BoxGeometry(0.2, 1.5, 0.2), [-0.85, 0, 0]),
      serializedMesh('right', new THREE.BoxGeometry(0.2, 1.5, 0.2), [0.85, 0, 0]),
      serializedMesh('top', new THREE.BoxGeometry(1.5, 0.2, 0.2), [0, 0.85, 0]),
      serializedMesh('bottom', new THREE.BoxGeometry(1.5, 0.2, 0.2), [0, -0.85, 0]),
      serializedMesh('diffuser', new THREE.BoxGeometry(1.5, 1.5, 0.1)),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'frame::diffuser',
      meshPairs: [
        ['left', 'diffuser'],
        ['right', 'diffuser'],
        ['top', 'diffuser'],
        ['bottom', 'diffuser'],
      ],
      minimumPenetrationSceneUnits: 0.01,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches).toEqual([])
  })

  it('does not mistake a flush infill inside one compound frame mesh for penetration', () => {
    const translatedBox = (
      size: [number, number, number],
      position: [number, number, number],
    ) => new THREE.BoxGeometry(...size).translate(...position)
    const frame = mergeGeometries([
      translatedBox([0.2, 1.5, 0.2], [-0.85, 0, 0]),
      translatedBox([0.2, 1.5, 0.2], [0.85, 0, 0]),
      translatedBox([1.5, 0.2, 0.2], [0, 0.85, 0]),
      translatedBox([1.5, 0.2, 0.2], [0, -0.85, 0]),
    ])
    if (!frame) throw new Error('Expected merged frame geometry')
    const meshes = [
      serializedMesh('frame', frame),
      serializedMesh('infill', new THREE.BoxGeometry(1.5, 1.5, 0.1)),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'compound-frame::infill',
      meshPairs: [['frame', 'infill']],
      minimumPenetrationSceneUnits: 0.01,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches).toEqual([])
  })

  it('honours the penetration threshold rather than treating surface contact as overlap', () => {
    const meshes = [
      serializedMesh('a', new THREE.BoxGeometry(2, 2, 2)),
      serializedMesh('touching', new THREE.BoxGeometry(2, 2, 2), [2, 0, 0]),
      serializedMesh('shallow', new THREE.BoxGeometry(2, 2, 2), [1.5, 0, 0]),
      serializedMesh('deep', new THREE.BoxGeometry(2, 2, 2), [0.5, 0, 0]),
    ]

    const matches = analyzeCollisionMeshes(meshes, [
      { id: 'touching', meshPairs: [['a', 'touching']], minimumPenetrationSceneUnits: 1, sceneUnitsPerMillimeter: 1 },
      { id: 'shallow', meshPairs: [['a', 'shallow']], minimumPenetrationSceneUnits: 1, sceneUnitsPerMillimeter: 1 },
      { id: 'deep', meshPairs: [['a', 'deep']], minimumPenetrationSceneUnits: 1, sceneUnitsPerMillimeter: 1 },
    ])

    expect(matches.map(match => match.pairId)).toEqual(['deep'])
  })

  it('applies millimetre thresholds to metre-scaled viewer geometry', () => {
    const meshes = [
      serializedMesh('a', new THREE.BoxGeometry(0.01, 0.01, 0.01)),
      serializedMesh('sub-mm', new THREE.BoxGeometry(0.01, 0.01, 0.01), [0.0095, 0, 0]),
      serializedMesh('two-mm', new THREE.BoxGeometry(0.01, 0.01, 0.01), [0.008, 0, 0]),
    ]

    const matches = analyzeCollisionMeshes(meshes, [
      {
        id: 'sub-mm',
        meshPairs: [['a', 'sub-mm']],
        minimumPenetrationSceneUnits: 0.001,
        sceneUnitsPerMillimeter: 0.001,
      },
      {
        id: 'two-mm',
        meshPairs: [['a', 'two-mm']],
        minimumPenetrationSceneUnits: 0.001,
        sceneUnitsPerMillimeter: 0.001,
      },
    ])

    expect(matches.map(match => match.pairId)).toEqual(['two-mm'])
  })

  it('classifies penetration when an imported solid has reversed face winding', () => {
    const meshes = [
      serializedMesh('a', new THREE.BoxGeometry(2, 2, 2)),
      serializedMesh('b', reverseWinding(new THREE.BoxGeometry(2, 2, 2)), [0.5, 0, 0]),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'reversed',
      meshPairs: [['a', 'b']],
      minimumPenetrationSceneUnits: 1,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches.map(match => match.pairId)).toEqual(['reversed'])
  })

  it('repairs locally inconsistent winding before classifying containment', () => {
    const meshes = [
      serializedMesh('a', new THREE.BoxGeometry(2, 2, 2)),
      serializedMesh('b', reverseFirstTriangle(new THREE.BoxGeometry(2, 2, 2)), [0.5, 0, 0]),
    ]

    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'mixed-winding',
      meshPairs: [['a', 'b']],
      minimumPenetrationSceneUnits: 1,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches.map(match => match.pairId)).toEqual(['mixed-winding'])
  })
})
