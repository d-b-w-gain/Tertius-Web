import * as THREE from 'three'
import type {
  CollisionComponent,
  CollisionAnalysisResult,
  PotentialCollision,
} from './collisionAnalysis'
import type {
  CollisionNarrowPhaseRequest,
  CollisionNarrowPhaseResponse,
  SerializedCollisionMesh,
} from './collisionNarrowPhase.types'

export type VerifiedCollisionAnalysisResult = {
  componentCount: number
  candidatePairCount: number
  confirmedPairCount: number
  pairs: PotentialCollision[]
  truncated: boolean
}

export const COLLISION_ENGINE_CHECKPOINT = 'mesh-bvh-shed-v1'

type VerifyCollisionOptions = {
  maxPairs?: number
  minimumPenetrationMm?: number
  sceneUnitsPerMillimeter?: number
  signal?: AbortSignal
  onPreparationProgress?: (processed: number, total: number) => void
  onProgress?: (processed: number, total: number) => void
  onPartialResult?: (result: VerifiedCollisionAnalysisResult) => void
}

const copyPositions = (geometry: THREE.BufferGeometry): Float32Array => {
  const source = geometry.getAttribute('position')
  if (
    source instanceof THREE.BufferAttribute
    && source.itemSize === 3
    && source.array instanceof Float32Array
  ) {
    return source.array.slice()
  }
  const positions = new Float32Array(source.count * 3)
  for (let index = 0; index < source.count; index += 1) {
    positions[index * 3] = source.getX(index)
    positions[index * 3 + 1] = source.getY(index)
    positions[index * 3 + 2] = source.getZ(index)
  }
  return positions
}

const copyIndices = (geometry: THREE.BufferGeometry): Uint32Array | undefined => {
  const source = geometry.getIndex()
  if (!source) return undefined
  if (source.itemSize === 1) {
    return Uint32Array.from(source.array)
  }
  const indices = new Uint32Array(source.count)
  for (let index = 0; index < source.count; index += 1) indices[index] = source.getX(index)
  return indices
}

export const serializeCollisionMesh = (mesh: THREE.Mesh): SerializedCollisionMesh => ({
  id: mesh.uuid,
  positions: copyPositions(mesh.geometry),
  indices: copyIndices(mesh.geometry),
  matrixWorld: mesh.matrixWorld.toArray(),
})

export const serializeCollisionComponent = (
  component: Pick<CollisionComponent, 'id' | 'meshes'>,
): SerializedCollisionMesh => {
  const vertexCounts = component.meshes.map(mesh => mesh.geometry.getAttribute('position').count)
  const indexCounts = component.meshes.map((mesh, index) => (
    mesh.geometry.getIndex()?.count ?? vertexCounts[index]!
  ))
  const positions = new Float32Array(vertexCounts.reduce((total, count) => total + count, 0) * 3)
  const indices = new Uint32Array(indexCounts.reduce((total, count) => total + count, 0))
  const worldPosition = new THREE.Vector3()
  let vertexOffset = 0
  let indexOffset = 0

  component.meshes.forEach((mesh, meshIndex) => {
    const sourcePositions = mesh.geometry.getAttribute('position')
    const sourceIndices = mesh.geometry.getIndex()
    mesh.updateWorldMatrix(true, false)
    for (let vertex = 0; vertex < sourcePositions.count; vertex += 1) {
      worldPosition.fromBufferAttribute(sourcePositions, vertex).applyMatrix4(mesh.matrixWorld)
      const target = (vertexOffset + vertex) * 3
      positions[target] = worldPosition.x
      positions[target + 1] = worldPosition.y
      positions[target + 2] = worldPosition.z
    }
    const meshIndexCount = indexCounts[meshIndex]!
    for (let sourceIndex = 0; sourceIndex < meshIndexCount; sourceIndex += 1) {
      indices[indexOffset + sourceIndex] = vertexOffset + (
        sourceIndices ? sourceIndices.getX(sourceIndex) : sourceIndex
      )
    }
    vertexOffset += vertexCounts[meshIndex]!
    indexOffset += meshIndexCount
  })

  return {
    id: `component:${component.id}`,
    positions,
    indices,
    matrixWorld: new THREE.Matrix4().toArray(),
  }
}

const yieldToBrowser = () => new Promise<void>(resolve => setTimeout(resolve, 0))

export async function verifyPotentialCollisions(
  broadPhase: CollisionAnalysisResult,
  options: VerifyCollisionOptions = {},
): Promise<VerifiedCollisionAnalysisResult> {
  const maxPairs = Math.max(1, Math.floor(options.maxPairs ?? 250))
  const minimumPenetrationMm = Math.max(0, options.minimumPenetrationMm ?? 1)
  const sceneUnitsPerMillimeter = Math.max(0, options.sceneUnitsPerMillimeter ?? 0.001)
  const componentById = new Map<string, CollisionComponent>()
  broadPhase.pairs.forEach((pair) => {
    componentById.set(pair.a.id, pair.a)
    componentById.set(pair.b.id, pair.b)
  })
  const sourceComponents = [...componentById.values()]
  const meshes: SerializedCollisionMesh[] = []
  let sliceStartedAt = performance.now()
  for (let index = 0; index < sourceComponents.length; index += 1) {
    if (options.signal?.aborted) throw new DOMException('Collision scan cancelled', 'AbortError')
    meshes.push(serializeCollisionComponent(sourceComponents[index]!))
    options.onPreparationProgress?.(index + 1, sourceComponents.length)
    if (performance.now() - sliceStartedAt >= 8 && index + 1 < sourceComponents.length) {
      await yieldToBrowser()
      sliceStartedAt = performance.now()
    }
  }
  const request: CollisionNarrowPhaseRequest = {
    type: 'analyze',
    meshes,
    candidates: broadPhase.pairs.map(pair => ({
      id: pair.id,
      meshPairs: [[`component:${pair.a.id}`, `component:${pair.b.id}`]],
      minimumPenetrationSceneUnits: minimumPenetrationMm * sceneUnitsPerMillimeter,
      sceneUnitsPerMillimeter,
    })),
  }
  const transfers = meshes.flatMap((mesh) => [
    mesh.positions.buffer,
    ...(mesh.indices ? [mesh.indices.buffer] : []),
  ])
  const contactByPairId = new Map<string, [number, number, number]>()
  const createResult = (): VerifiedCollisionAnalysisResult => {
    const confirmedPairs = broadPhase.pairs.flatMap((pair) => {
      const contact = contactByPairId.get(pair.id)
      return contact ? [{ ...pair, contactPoint: new THREE.Vector3(...contact) }] : []
    })
    const visiblePairs = confirmedPairs.slice(0, maxPairs)
    return {
      componentCount: broadPhase.componentCount,
      candidatePairCount: broadPhase.totalPairCount,
      confirmedPairCount: confirmedPairs.length,
      pairs: visiblePairs,
      truncated: confirmedPairs.length > visiblePairs.length,
    }
  }

  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(new DOMException('Collision scan cancelled', 'AbortError'))
      return
    }

    const worker = new Worker(new URL('./collisionAnalysis.worker.ts', import.meta.url), { type: 'module' })
    const abort = () => {
      worker.terminate()
      reject(new DOMException('Collision scan cancelled', 'AbortError'))
    }
    options.signal?.addEventListener('abort', abort, { once: true })

    worker.onerror = (event) => {
      options.signal?.removeEventListener('abort', abort)
      worker.terminate()
      reject(new Error(event.message || 'Mesh collision worker failed'))
    }
    worker.onmessageerror = () => {
      options.signal?.removeEventListener('abort', abort)
      worker.terminate()
      reject(new Error('Browser could not decode collision worker results'))
    }
    let lastPartialResultAt = 0
    worker.onmessage = (event: MessageEvent<CollisionNarrowPhaseResponse>) => {
      if (event.data.type === 'progress') {
        event.data.matches.forEach(match => contactByPairId.set(match.pairId, match.contactPoint))
        options.onProgress?.(event.data.processed, event.data.total)
        const now = performance.now()
        if (event.data.matches.length > 0 && now - lastPartialResultAt >= 250) {
          lastPartialResultAt = now
          options.onPartialResult?.(createResult())
        }
        return
      }
      options.signal?.removeEventListener('abort', abort)
      worker.terminate()
      if (event.data.type === 'error') {
        reject(new Error(event.data.message))
        return
      }

      event.data.matches.forEach(match => contactByPairId.set(match.pairId, match.contactPoint))
      resolve(createResult())
    }

    try {
      worker.postMessage(request, transfers)
    } catch (error) {
      options.signal?.removeEventListener('abort', abort)
      worker.terminate()
      reject(error instanceof Error ? error : new Error('Could not start collision worker'))
    }
  })
}
