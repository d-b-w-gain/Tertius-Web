import { afterEach, describe, expect, it, vi } from 'vitest'
import * as THREE from 'three'
import { analyzePotentialCollisions } from './collisionAnalysis'
import { verifyPotentialCollisions } from './collisionNarrowPhase'
import type {
  CollisionNarrowPhaseRequest,
  CollisionNarrowPhaseResponse,
} from './collisionNarrowPhase.types'

class CollisionWorkerStub {
  static requests: CollisionNarrowPhaseRequest[] = []
  onerror: ((event: ErrorEvent) => void) | null = null
  onmessage: ((event: MessageEvent<CollisionNarrowPhaseResponse>) => void) | null = null
  onmessageerror: ((event: MessageEvent) => void) | null = null

  postMessage(request: CollisionNarrowPhaseRequest) {
    CollisionWorkerStub.requests.push(request)
    const pairId = request.candidates[0]!.id
    const match = { pairId, contactPoint: [0, 0, 0] as [number, number, number] }
    queueMicrotask(() => {
      this.onmessage?.({
        data: { type: 'progress', processed: 1, total: 1, matches: [match] },
      } as MessageEvent<CollisionNarrowPhaseResponse>)
      this.onmessage?.({
        data: { type: 'complete', matches: [match] },
      } as MessageEvent<CollisionNarrowPhaseResponse>)
    })
  }

  terminate() {}
}

const collisionComponent = (name: string, callId: string, x: number) => {
  const group = new THREE.Group()
  group.name = name
  group.position.x = x
  group.userData.tertiusSourceCallIds = [callId]
  group.add(new THREE.Mesh(
    new THREE.BoxGeometry(0.01, 0.01, 0.01),
    new THREE.MeshBasicMaterial(),
  ))
  return group
}

describe('Extus collision narrow phase bridge', () => {
  afterEach(() => {
    CollisionWorkerStub.requests = []
    vi.unstubAllGlobals()
  })

  it('publishes confirmed pairs while exact verification is still running', async () => {
    vi.stubGlobal('Worker', CollisionWorkerStub)
    const root = new THREE.Group()
    root.add(collisionComponent('A', 'call_a', 0))
    root.add(collisionComponent('B', 'call_b', 0.004))
    const broadPhase = analyzePotentialCollisions(root, { minimumPenetrationMm: 1 })
    const partialResults: number[] = []

    const result = await verifyPotentialCollisions(broadPhase, {
      onPartialResult: partial => partialResults.push(partial.confirmedPairCount),
    })

    expect(partialResults).toEqual([1])
    expect(result.confirmedPairCount).toBe(1)
    expect(result.pairs[0]?.contactPoint?.toArray()).toEqual([0, 0, 0])
    expect(CollisionWorkerStub.requests[0]?.candidates[0]).toMatchObject({
      minimumPenetrationSceneUnits: 0.001,
      sceneUnitsPerMillimeter: 0.001,
    })
  })
})
