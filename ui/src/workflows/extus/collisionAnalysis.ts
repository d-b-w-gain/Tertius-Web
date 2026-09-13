import * as THREE from 'three'

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
  meshCount: number
  sourceReferences: CollisionSourceReference[]
}

export type PotentialCollision = {
  id: string
  a: CollisionComponent
  b: CollisionComponent
  overlapSize: THREE.Vector3
  overlapVolume: number
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

const sourceCallIds = (node: THREE.Object3D): string[] => {
  const value = node.userData?.tertiusSourceCallIds
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

const isViewerBatchMesh = (node: THREE.Object3D) => (
  node.name === 'TertiusBatchedMesh' || node.name === 'TertiusAppearanceBatchMesh'
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

export function collectCollisionComponents(root: THREE.Object3D): CollisionComponent[] {
  root.updateMatrixWorld(true)
  const meshesByNode = new Map<THREE.Object3D, THREE.Mesh[]>()

  root.traverse((child) => {
    if (
      child.userData?.tertiusStructuralOverlay
      || isViewerBatchMesh(child)
      || !(child as THREE.Mesh).isMesh
    ) return
    const node = componentNodeForMesh(child, root)
    const meshes = meshesByNode.get(node) || []
    meshes.push(child as THREE.Mesh)
    meshesByNode.set(node, meshes)
  })

  return [...meshesByNode.entries()].flatMap(([node, meshes], index) => {
    const bounds = new THREE.Box3()
    meshes.forEach((mesh) => {
      const meshBounds = new THREE.Box3().setFromObject(mesh)
      if (!meshBounds.isEmpty()) bounds.union(meshBounds)
    })
    if (bounds.isEmpty()) return []

    const gltfNodeId = node.userData?.tertiusGltfNodeId
    const id = typeof gltfNodeId === 'string' && gltfNodeId ? gltfNodeId : node.uuid
    return [{
      id,
      label: node.name || `Component ${index + 1}`,
      node,
      bounds,
      meshCount: meshes.length,
      sourceReferences: sourceReferencesFor(node, root),
    }]
  })
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

      const overlapSize = new THREE.Vector3(
        Math.min(a.bounds.max.x, b.bounds.max.x) - Math.max(a.bounds.min.x, b.bounds.min.x),
        Math.min(a.bounds.max.y, b.bounds.max.y) - Math.max(a.bounds.min.y, b.bounds.min.y),
        Math.min(a.bounds.max.z, b.bounds.max.z) - Math.max(a.bounds.min.z, b.bounds.min.z),
      )
      if (
        overlapSize.x < minimumPenetration
        || overlapSize.y < minimumPenetration
        || overlapSize.z < minimumPenetration
      ) continue

      totalPairCount += 1
      const overlapVolume = overlapSize.x * overlapSize.y * overlapSize.z
      insertRankedPair(pairs, {
        id: `${a.id}::${b.id}`,
        a,
        b,
        overlapSize,
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
