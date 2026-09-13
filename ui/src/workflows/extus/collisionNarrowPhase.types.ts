export type SerializedCollisionMesh = {
  id: string
  positions: Float32Array
  indices?: Uint32Array
  matrixWorld: number[]
}

export type SerializedCollisionCandidate = {
  id: string
  meshPairs: Array<[string, string]>
  minimumPenetrationSceneUnits: number
  sceneUnitsPerMillimeter: number
}

export type CollisionNarrowPhaseRequest = {
  type: 'analyze'
  meshes: SerializedCollisionMesh[]
  candidates: SerializedCollisionCandidate[]
}

export type CollisionNarrowPhaseMatch = {
  pairId: string
  contactPoint: [number, number, number]
}

export type CollisionNarrowPhaseProgress = {
  type: 'progress'
  processed: number
  total: number
  matches: CollisionNarrowPhaseMatch[]
}

export type CollisionNarrowPhaseComplete = {
  type: 'complete'
  matches: CollisionNarrowPhaseMatch[]
}

export type CollisionNarrowPhaseFailure = {
  type: 'error'
  message: string
}

export type CollisionNarrowPhaseResponse =
  | CollisionNarrowPhaseProgress
  | CollisionNarrowPhaseComplete
  | CollisionNarrowPhaseFailure
