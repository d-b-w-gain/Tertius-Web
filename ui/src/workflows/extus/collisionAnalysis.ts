import * as THREE from 'three'
import {
  collisionDisplayLabel,
  collisionPolicyForNode,
  shouldAnalyzeCollisionPair,
} from './collisionPolicy'

export {
  collisionDisplayLabel,
  collisionGroupFromLabel,
} from './collisionPolicy'

export type CollisionSourceReference = {
  callId: string
  functionName?: string
  sourceFile?: string
  sourceLine?: number
  definitionFile?: string
  definitionLine?: number
}

export type CollisionComponent = {
  id: string
  label: string
  node: THREE.Object3D
  bounds: THREE.Box3
  meshes: THREE.Mesh[]
  meshBounds: THREE.Box3[]
  meshCount: number
  collisionGroup?: string
  collisionGroups: string[]
  sourceReferences: CollisionSourceReference[]
}

export type PotentialCollision = {
  id: string
  a: CollisionComponent
  b: CollisionComponent
  meshPairs: Array<[THREE.Mesh, THREE.Mesh]>
  overlapSize: THREE.Vector3
  overlapVolume: number
  contactPoint?: THREE.Vector3
}

export type CollisionAnalysisResult = {
  componentCount: number
  totalPairCount: number
  pairs: PotentialCollision[]
  truncated: boolean
}

type SourceCallRecord = {
  function?: unknown
  source_file?: unknown
  source_line?: unknown
  definition_file?: unknown
  definition_line?: unknown
}

type CollisionAnalysisOptions = {
  minimumPenetrationMm?: number
  sceneUnitsPerMillimeter?: number
  maxPairs?: number
}

type AsyncCollisionAnalysisOptions = CollisionAnalysisOptions & {
  signal?: AbortSignal
  onProgress?: (processed: number, total: number) => void
}

const sourceCallIds = (node: THREE.Object3D): string[] => {
  const value = node.userData?.tertiusSourceCallIds
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

const isViewerBatchMesh = (node: THREE.Object3D) => (
  node.userData?.tertiusViewerBatch === true
  || node.name === 'TertiusBatchedMesh'
  || node.name === 'TertiusAppearanceBatchMesh'
)

const componentNodeForMesh = (mesh: THREE.Object3D, root: THREE.Object3D): THREE.Object3D => {
  let current: THREE.Object3D | null = mesh
  let namedFallback: THREE.Object3D | null = mesh.name ? mesh : null

  while (current && current !== root) {
    if (sourceCallIds(current).length > 0) return current
    if (!namedFallback && current.name && !isViewerBatchMesh(current)) namedFallback = current
    current = current.parent
  }

  return namedFallback || mesh
}

const sourceReferencesFor = (node: THREE.Object3D, root: THREE.Object3D): CollisionSourceReference[] => {
  const sourceMap = root.userData?.tertiusSourceMap
  const calls = sourceMap && typeof sourceMap === 'object'
    ? (sourceMap as { source_calls?: unknown }).source_calls
    : undefined
  const records = calls && typeof calls === 'object' ? calls as Record<string, SourceCallRecord> : {}

  return sourceCallIds(node).map((callId) => {
    const record = records[callId]
    return {
      callId,
      functionName: typeof record?.function === 'string' ? record.function : undefined,
      sourceFile: typeof record?.source_file === 'string' ? record.source_file : undefined,
      sourceLine: typeof record?.source_line === 'number' ? record.source_line : undefined,
      definitionFile: typeof record?.definition_file === 'string' ? record.definition_file : undefined,
      definitionLine: typeof record?.definition_line === 'number' ? record.definition_line : undefined,
    }
  })
}

const collisionMeshesByNode = (root: THREE.Object3D): Map<THREE.Object3D, THREE.Mesh[]> => {
  root.updateMatrixWorld(true)
  const meshesByNode = new Map<THREE.Object3D, THREE.Mesh[]>()

  root.traverse((child) => {
    if (
      child.userData?.tertiusStructuralOverlay
      || isViewerBatchMesh(child)
      || !(child as THREE.Mesh).isMesh
    ) return
    const node = componentNodeForMesh(child, root)
    // Exported Build123D compounds can put provenance on an assembly ancestor
    // while preserving collision policy on the rendered child below it.
    if (!collisionPolicyForNode(child, root).check) return
    const meshes = meshesByNode.get(node) || []
    meshes.push(child as THREE.Mesh)
    meshesByNode.set(node, meshes)
  })
  return meshesByNode
}

const collisionComponent = (
  root: THREE.Object3D,
  node: THREE.Object3D,
  meshes: THREE.Mesh[],
  index: number,
): CollisionComponent | undefined => {
  const boundedMeshes = meshes
    .map((mesh) => ({ mesh, bounds: new THREE.Box3().setFromObject(mesh) }))
    .filter(({ bounds: meshBox }) => !meshBox.isEmpty())
  const collisionMeshes = boundedMeshes.map(({ mesh }) => mesh)
  const meshBounds = boundedMeshes.map(({ bounds: meshBox }) => meshBox)
  const bounds = new THREE.Box3()
  meshBounds.forEach((meshBox) => bounds.union(meshBox))
  if (bounds.isEmpty()) return undefined

  const gltfNodeId = node.userData?.tertiusGltfNodeId
  const id = typeof gltfNodeId === 'string' && gltfNodeId ? gltfNodeId : node.uuid
  const meshGroups = new Set<string>()
  collisionMeshes.forEach((mesh) => {
    const policy = collisionPolicyForNode(mesh, root)
    ;(policy.groups ?? (policy.group ? [policy.group] : [])).forEach(group => meshGroups.add(group))
  })
  const collisionGroups = [...meshGroups]
  const collisionGroup = collisionGroups.length === 1 ? collisionGroups[0] : undefined
  return {
    id,
    label: collisionDisplayLabel(node.name) || `Component ${index + 1}`,
    node,
    bounds,
    meshes: collisionMeshes,
    meshBounds,
    meshCount: collisionMeshes.length,
    collisionGroup,
    collisionGroups,
    sourceReferences: sourceReferencesFor(node, root),
  }
}

export function collectCollisionComponents(root: THREE.Object3D): CollisionComponent[] {
  return [...collisionMeshesByNode(root).entries()].flatMap(([node, meshes], index) => {
    const component = collisionComponent(root, node, meshes, index)
    return component ? [component] : []
  })
}

function componentOverlap(
  a: CollisionComponent,
  b: CollisionComponent,
  minimumPenetration: number,
): { overlapSize: THREE.Vector3, meshPairs: Array<[THREE.Mesh, THREE.Mesh]> } | null {
  let largestOverlap: THREE.Vector3 | null = null
  let largestVolume = 0
  const meshPairs: Array<[THREE.Mesh, THREE.Mesh]> = []

  for (let aIndex = 0; aIndex < a.meshBounds.length; aIndex += 1) {
    const aBounds = a.meshBounds[aIndex]!
    for (let bIndex = 0; bIndex < b.meshBounds.length; bIndex += 1) {
      const bBounds = b.meshBounds[bIndex]!
      const overlapSize = new THREE.Vector3(
        Math.min(aBounds.max.x, bBounds.max.x) - Math.max(aBounds.min.x, bBounds.min.x),
        Math.min(aBounds.max.y, bBounds.max.y) - Math.max(aBounds.min.y, bBounds.min.y),
        Math.min(aBounds.max.z, bBounds.max.z) - Math.max(aBounds.min.z, bBounds.min.z),
      )
      if (
        overlapSize.x < -minimumPenetration
        || overlapSize.y < -minimumPenetration
        || overlapSize.z < -minimumPenetration
      ) continue

      // Keep touching and near-touching rendered-mesh pairs once another mesh
      // pair qualifies the component. Imported profiled sheets can intersect
      // a rotated member while their world AABBs are separated by a tiny
      // amount, so exact BVH verification remains the final authority.
      meshPairs.push([a.meshes[aIndex]!, b.meshes[bIndex]!])
      if (
        overlapSize.x < minimumPenetration
        || overlapSize.y < minimumPenetration
        || overlapSize.z < minimumPenetration
      ) continue

      const volume = overlapSize.x * overlapSize.y * overlapSize.z
      if (volume > largestVolume) {
        largestOverlap = overlapSize
        largestVolume = volume
      }
    }
  }

  return largestOverlap ? { overlapSize: largestOverlap, meshPairs } : null
}

function insertRankedPair(pairs: PotentialCollision[], pair: PotentialCollision, maxPairs: number): void {
  let low = 0
  let high = pairs.length
  while (low < high) {
    const middle = (low + high) >>> 1
    if (pairs[middle]!.overlapVolume >= pair.overlapVolume) low = middle + 1
    else high = middle
  }
  pairs.splice(low, 0, pair)
  if (pairs.length > maxPairs) pairs.pop()
}

export function analyzePotentialCollisions(
  root: THREE.Object3D,
  options: CollisionAnalysisOptions = {},
): CollisionAnalysisResult {
  const sceneUnitsPerMillimeter = Math.max(0, options.sceneUnitsPerMillimeter ?? 0.001)
  const minimumPenetration = Math.max(0, options.minimumPenetrationMm ?? 1) * sceneUnitsPerMillimeter
  const maxPairs = Math.max(1, Math.floor(options.maxPairs ?? 250))
  const components = collectCollisionComponents(root)
    .sort((a, b) => a.bounds.min.x - b.bounds.min.x)
  const pairs: PotentialCollision[] = []
  let totalPairCount = 0

  for (let index = 0; index < components.length; index += 1) {
    const a = components[index]!
    for (let otherIndex = index + 1; otherIndex < components.length; otherIndex += 1) {
      const b = components[otherIndex]!
      if (b.bounds.min.x > a.bounds.max.x - minimumPenetration) break
      if (!shouldAnalyzeCollisionPair(a, b)) continue

      const overlap = componentOverlap(a, b, minimumPenetration)
      if (!overlap) continue

      totalPairCount += 1
      const overlapVolume = overlap.overlapSize.x * overlap.overlapSize.y * overlap.overlapSize.z
      insertRankedPair(pairs, {
        id: `${a.id}::${b.id}`,
        a,
        b,
        meshPairs: overlap.meshPairs,
        overlapSize: overlap.overlapSize,
        overlapVolume,
      }, maxPairs)
    }
  }

  return {
    componentCount: components.length,
    totalPairCount,
    pairs,
    truncated: totalPairCount > pairs.length,
  }
}

const collisionScanYield = () => new Promise<void>(resolve => setTimeout(resolve, 0))

const throwIfCollisionScanAborted = (signal?: AbortSignal) => {
  if (signal?.aborted) throw new DOMException('Collision scan cancelled', 'AbortError')
}

export async function analyzePotentialCollisionsAsync(
  root: THREE.Object3D,
  options: AsyncCollisionAnalysisOptions = {},
): Promise<CollisionAnalysisResult> {
  const sceneUnitsPerMillimeter = Math.max(0, options.sceneUnitsPerMillimeter ?? 0.001)
  const minimumPenetration = Math.max(0, options.minimumPenetrationMm ?? 1) * sceneUnitsPerMillimeter
  const maxPairs = Math.max(1, Math.floor(options.maxPairs ?? 250))
  const entries = [...collisionMeshesByNode(root).entries()]
  const components: CollisionComponent[] = []
  let sliceStartedAt = performance.now()

  for (let index = 0; index < entries.length; index += 1) {
    throwIfCollisionScanAborted(options.signal)
    const [node, meshes] = entries[index]!
    const component = collisionComponent(root, node, meshes, index)
    if (component) components.push(component)
    options.onProgress?.(index + 1, entries.length)
    if (performance.now() - sliceStartedAt >= 8 && index + 1 < entries.length) {
      await collisionScanYield()
      sliceStartedAt = performance.now()
    }
  }

  components.sort((a, b) => a.bounds.min.x - b.bounds.min.x)
  const pairs: PotentialCollision[] = []
  let totalPairCount = 0
  sliceStartedAt = performance.now()

  for (let index = 0; index < components.length; index += 1) {
    throwIfCollisionScanAborted(options.signal)
    const a = components[index]!
    for (let otherIndex = index + 1; otherIndex < components.length; otherIndex += 1) {
      const b = components[otherIndex]!
      if (b.bounds.min.x > a.bounds.max.x - minimumPenetration) break
      if (!shouldAnalyzeCollisionPair(a, b)) continue

      const overlap = componentOverlap(a, b, minimumPenetration)
      if (!overlap) continue

      totalPairCount += 1
      const overlapVolume = overlap.overlapSize.x * overlap.overlapSize.y * overlap.overlapSize.z
      insertRankedPair(pairs, {
        id: `${a.id}::${b.id}`,
        a,
        b,
        meshPairs: overlap.meshPairs,
        overlapSize: overlap.overlapSize,
        overlapVolume,
      }, maxPairs)
    }
    options.onProgress?.(index + 1, components.length)
    if (performance.now() - sliceStartedAt >= 8 && index + 1 < components.length) {
      await collisionScanYield()
      sliceStartedAt = performance.now()
    }
  }

  return {
    componentCount: components.length,
    totalPairCount,
    pairs,
    truncated: totalPairCount > pairs.length,
  }
}
