import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import fixture from './fixtures/shedCollisionGolden.json'
import { analyzeCollisionMeshes } from './collisionNarrowPhaseCore'
import { serializeCollisionComponent } from './collisionNarrowPhase'
import type { SerializedCollisionMesh } from './collisionNarrowPhase.types'
import {
  collisionGroupFromLabel,
  shouldAnalyzeCollisionPair,
} from './collisionPolicy'

const serializedBox = (
  id: string,
  position: [number, number, number],
): SerializedCollisionMesh => {
  const geometry = new THREE.BoxGeometry(1, 1, 1)
  const positionAttribute = geometry.getAttribute('position')
  const index = geometry.getIndex()
  const positions = Float32Array.from(positionAttribute.array)
  const indices = index ? Uint32Array.from(index.array) : undefined
  geometry.dispose()
  return {
    id,
    positions,
    indices,
    matrixWorld: new THREE.Matrix4().makeTranslation(...position).toArray(),
  }
}

const fixtureMesh = (mesh: {
  id: string
  positions: number[]
  indices: number[]
  matrixWorld: number[]
}): THREE.Mesh => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(mesh.positions, 3))
  geometry.setIndex(mesh.indices)
  const result = new THREE.Mesh(geometry)
  result.matrixAutoUpdate = false
  result.matrix.fromArray(mesh.matrixWorld)
  result.matrixWorld.copy(result.matrix)
  return result
}

describe('collision engine checkpoint mesh-bvh-shed-v1', () => {
  it('MUST DETECT a genuine solid penetration', () => {
    const meshes = [serializedBox('a', [0, 0, 0]), serializedBox('b', [0.4, 0, 0])]
    const matches = analyzeCollisionMeshes(meshes, [{
      id: 'crossing-solids',
      meshPairs: [['a', 'b']],
      minimumPenetrationSceneUnits: 0.1,
      sceneUnitsPerMillimeter: 1,
    }])

    expect(matches.map(match => match.pairId)).toEqual(['crossing-solids'])
  })

  it('MUST REJECT the real shed ceiling cutout and skylight diffuser', () => {
    const testCase = fixture.cases[0]!
    const ceiling = testCase.components[0]!
    const diffuser = testCase.components[1]!
    const meshes = [
      serializeCollisionComponent({ id: ceiling.id, meshes: ceiling.meshes.map(fixtureMesh) }),
      serializeCollisionComponent({ id: diffuser.id, meshes: diffuser.meshes.map(fixtureMesh) }),
    ]
    const matches = analyzeCollisionMeshes(meshes, [{
      id: testCase.id,
      meshPairs: [[`component:${ceiling.id}`, `component:${diffuser.id}`]],
      minimumPenetrationSceneUnits: testCase.minimumPenetrationMm * testCase.sceneUnitsPerMillimeter,
      sceneUnitsPerMillimeter: testCase.sceneUnitsPerMillimeter,
    }])

    expect(testCase.expected).toBe('reject')
    expect(matches).toEqual([])
  })

  it('MUST IGNORE matching Custom Orb laps, using policy rather than geometry', () => {
    const testCase = fixture.policyCases[0]!
    const first = testCase.components[0]!
    const second = testCase.components[1]!
    const firstGroup = collisionGroupFromLabel(first.label)
    const secondGroup = collisionGroupFromLabel(second.label)

    expect(testCase.expected).toBe('ignore-pair')
    expect(firstGroup).toBe('roof-sheet-left')
    expect(secondGroup).toBe('roof-sheet-left')
    expect(shouldAnalyzeCollisionPair(
      { collisionGroup: firstGroup },
      { collisionGroup: secondGroup },
    )).toBe(false)
  })
})
