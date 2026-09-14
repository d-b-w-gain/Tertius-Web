import * as THREE from 'three'
import { CENTER, MeshBVH } from 'three-mesh-bvh'
import type {
  CollisionNarrowPhaseMatch,
  SerializedCollisionCandidate,
  SerializedCollisionMesh,
} from './collisionNarrowPhase.types'

type PreparedCollisionMesh = {
  geometry: THREE.BufferGeometry
  matrixWorld: THREE.Matrix4
  inverseWorld: THREE.Matrix4
  faceOrientations?: Int8Array
  requiresParity?: boolean
  boundsTree?: MeshBVH
}

const meshFaceOrientations = (geometry: THREE.BufferGeometry): {
  orientations: Int8Array
  requiresParity: boolean
} => {
  const position = geometry.getAttribute('position')
  const index = geometry.getIndex()
  const vertexIndex = (offset: number) => index ? index.getX(offset) : offset
  const faceCount = Math.floor((index ? index.count : position.count) / 3)
  const orientations = new Int8Array(faceCount)
  const adjacency: Array<Array<[number, 1 | -1]>> = Array.from(
    { length: faceCount },
    () => [],
  )
  const weldedVertexKeys = new Map<number, string>()
  const vertexKey = (offset: number): string => {
    const vertex = vertexIndex(offset)
    const cached = weldedVertexKeys.get(vertex)
    if (cached) return cached
    // CAD exporters commonly duplicate vertices at sharp edges. Weld by
    // position so the orientation pass can still follow the closed shell.
    const scale = 1e5
    const key = `${Math.round(position.getX(vertex) * scale)},${Math.round(position.getY(vertex) * scale)},${Math.round(position.getZ(vertex) * scale)}`
    weldedVertexKeys.set(vertex, key)
    return key
  }
  const edgeOwners = new Map<string, [number, 1 | -1]>()
  const edgeCounts = new Map<string, number>()

  for (let face = 0; face < faceCount; face += 1) {
    const offset = face * 3
    const corners = [vertexKey(offset), vertexKey(offset + 1), vertexKey(offset + 2)]
    for (let corner = 0; corner < 3; corner += 1) {
      const from = corners[corner]!
      const to = corners[(corner + 1) % 3]!
      const direction: 1 | -1 = from < to ? 1 : -1
      const edge = from < to ? `${from}|${to}` : `${to}|${from}`
      edgeCounts.set(edge, (edgeCounts.get(edge) || 0) + 1)
      const owner = edgeOwners.get(edge)
      if (!owner) {
        edgeOwners.set(edge, [face, direction])
        continue
      }
      // Adjacent consistently-wound triangles traverse their shared edge in
      // opposite directions. Record the flip needed to make that true.
      const relation: 1 | -1 = owner[1] === direction ? -1 : 1
      adjacency[owner[0]]!.push([face, relation])
      adjacency[face]!.push([owner[0], relation])
    }
  }

  const a = new THREE.Vector3()
  const b = new THREE.Vector3()
  const c = new THREE.Vector3()
  const cross = new THREE.Vector3()
  const queue: number[] = []
  let requiresParity = false
  let shellCount = 0

  for (let seed = 0; seed < faceCount; seed += 1) {
    if (orientations[seed] !== 0) continue
    shellCount += 1
    orientations[seed] = 1
    queue.length = 0
    queue.push(seed)
    const shellFaces: number[] = []

    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const face = queue[cursor]!
      shellFaces.push(face)
      for (const [neighbor, relation] of adjacency[face]!) {
        const expected = orientations[face]! * relation
        if (orientations[neighbor] === 0) {
          orientations[neighbor] = expected
          queue.push(neighbor)
        } else if (orientations[neighbor] !== expected) {
          requiresParity = true
        }
      }
    }

    let signedVolume = 0
    for (const face of shellFaces) {
      const offset = face * 3
      a.fromBufferAttribute(position, vertexIndex(offset))
      b.fromBufferAttribute(position, vertexIndex(offset + 1))
      c.fromBufferAttribute(position, vertexIndex(offset + 2))
      signedVolume += orientations[face]! * a.dot(cross.crossVectors(b, c))
    }
    if (signedVolume < 0) {
      for (const face of shellFaces) orientations[face] = -orientations[face]!
    }
  }

  // Open and non-manifold tessellations cannot provide one unambiguous signed
  // nearest surface. Disconnected closed shells are safe because each shell is
  // oriented independently.
  if ([...edgeCounts.values()].some(count => count !== 2)) requiresParity = true
  return { orientations, requiresParity: requiresParity || shellCount === 0 }
}

const createPreparedMesh = (mesh: SerializedCollisionMesh): PreparedCollisionMesh => {
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3))
  if (mesh.indices) geometry.setIndex(new THREE.BufferAttribute(mesh.indices, 1))
  const matrixWorld = new THREE.Matrix4().fromArray(mesh.matrixWorld)
  return {
    geometry,
    matrixWorld,
    inverseWorld: matrixWorld.clone().invert(),
  }
}

const getFaceOrientations = (mesh: PreparedCollisionMesh): Int8Array => {
  if (!mesh.faceOrientations) {
    const prepared = meshFaceOrientations(mesh.geometry)
    mesh.faceOrientations = prepared.orientations
    mesh.requiresParity = prepared.requiresParity
  }
  return mesh.faceOrientations
}

const getBoundsTree = (mesh: PreparedCollisionMesh): MeshBVH => {
  if (!mesh.boundsTree) {
    mesh.boundsTree = new MeshBVH(mesh.geometry, {
      strategy: CENTER,
      targetLeafSize: 16,
    })
  }
  return mesh.boundsTree
}

const pointInsideByRayParity = (
  pointLocal: THREE.Vector3,
  mesh: PreparedCollisionMesh,
): boolean => {
  const hits = getBoundsTree(mesh).raycast(
    new THREE.Ray(pointLocal, new THREE.Vector3(0.327, 0.519, 0.789).normalize()),
    THREE.DoubleSide,
  ).sort((left, right) => left.distance - right.distance)
  let crossings = 0
  let previousDistance = Number.NEGATIVE_INFINITY
  for (const hit of hits) {
    if (Math.abs(hit.distance - previousDistance) <= 1e-6) continue
    crossings += 1
    previousDistance = hit.distance
  }
  return crossings % 2 === 1
}

const pointInsideWithClearance = (
  pointWorld: THREE.Vector3,
  mesh: PreparedCollisionMesh,
  minimumClearance: number,
): boolean => {
  const pointLocal = pointWorld.clone().applyMatrix4(mesh.inverseWorld)
  const closest = getBoundsTree(mesh).closestPointToPoint(pointLocal)
  if (!closest) return false

  const closestWorld = closest.point.clone().applyMatrix4(mesh.matrixWorld)
  if (closestWorld.distanceTo(pointWorld) <= minimumClearance) return false

  const faceOrientations = getFaceOrientations(mesh)
  if (mesh.requiresParity) return pointInsideByRayParity(pointLocal, mesh)

  const position = mesh.geometry.getAttribute('position')
  const index = mesh.geometry.getIndex()
  const vertexIndex = (corner: number) => index
    ? index.getX(closest.faceIndex * 3 + corner)
    : closest.faceIndex * 3 + corner
  const triangle = new THREE.Triangle(
    new THREE.Vector3().fromBufferAttribute(position, vertexIndex(0)),
    new THREE.Vector3().fromBufferAttribute(position, vertexIndex(1)),
    new THREE.Vector3().fromBufferAttribute(position, vertexIndex(2)),
  )
  const outward = triangle
    .getNormal(new THREE.Vector3())
    .multiplyScalar(faceOrientations[closest.faceIndex] || 1)
  return outward.dot(pointLocal.sub(closest.point)) < 0
}

const hasPenetratingNeighborhood = (
  contactWorld: THREE.Vector3,
  normalAWorld: THREE.Vector3,
  normalBWorld: THREE.Vector3,
  a: PreparedCollisionMesh,
  b: PreparedCollisionMesh,
  minimumPenetrationSceneUnits: number,
  sceneUnitsPerMillimeter: number,
): boolean => {
  // Probe half the requested penetration on either side of the crossing. A
  // mere shared edge/face has no nearby point inside both closed solids, while
  // a genuine crossing does. The small floor avoids classifying floating-point
  // contact as penetration when the UI threshold is zero.
  const offset = Math.max(
    0.025 * sceneUnitsPerMillimeter,
    minimumPenetrationSceneUnits + 0.001 * sceneUnitsPerMillimeter,
  )
  const minimumClearance = Math.max(
    0.01 * sceneUnitsPerMillimeter,
    minimumPenetrationSceneUnits * 0.5,
  )
  const directions = [
    normalAWorld.clone().add(normalBWorld),
    normalAWorld.clone().sub(normalBWorld),
    normalAWorld.clone().multiplyScalar(-1).add(normalBWorld),
    normalAWorld.clone().multiplyScalar(-1).sub(normalBWorld),
  ]

  return directions.some((direction) => {
    if (direction.lengthSq() < 1e-10) return false
    const probe = contactWorld.clone().add(direction.normalize().multiplyScalar(offset))
    return pointInsideWithClearance(probe, a, minimumClearance)
      && pointInsideWithClearance(probe, b, minimumClearance)
  })
}

const meshPenetrationPoint = (
  a: PreparedCollisionMesh,
  b: PreparedCollisionMesh,
  minimumPenetrationSceneUnits: number,
  sceneUnitsPerMillimeter: number,
): THREE.Vector3 | null => {
  const aTree = getBoundsTree(a)
  const bTree = getBoundsTree(b)
  const bToA = a.matrixWorld.clone().invert().multiply(b.matrixWorld)
  const aNormalToWorld = new THREE.Matrix3().getNormalMatrix(a.matrixWorld)
  const line = new THREE.Line3()
  let contact: THREE.Vector3 | null = null

  aTree.bvhcast(bTree, bToA, {
    intersectsTriangles: (triangleA, triangleB) => {
      const normalAInA = triangleA.getNormal(new THREE.Vector3())
      const normalBInA = triangleB.getNormal(new THREE.Vector3())
      if (Math.abs(normalAInA.dot(normalBInA)) > 1 - 1e-8) return false
      if (!triangleA.intersectsTriangle(triangleB, line)) return false
      const candidate = line.start.clone().add(line.end).multiplyScalar(0.5).applyMatrix4(a.matrixWorld)
      const normalA = normalAInA.applyMatrix3(aNormalToWorld).normalize()
      const normalB = normalBInA.applyMatrix3(aNormalToWorld).normalize()
      if (!hasPenetratingNeighborhood(
        candidate,
        normalA,
        normalB,
        a,
        b,
        minimumPenetrationSceneUnits,
        sceneUnitsPerMillimeter,
      )) return false
      contact = candidate
      return true
    },
  })

  return contact
}

export function analyzeCollisionMeshes(
  serializedMeshes: SerializedCollisionMesh[],
  candidates: SerializedCollisionCandidate[],
  onProgress?: (processed: number, total: number) => void,
): CollisionNarrowPhaseMatch[] {
  const analyzer = new CollisionMeshAnalyzer(serializedMeshes)
  const matches: CollisionNarrowPhaseMatch[] = []

  try {
    candidates.forEach((candidate, candidateIndex) => {
      const match = analyzer.analyzeCandidate(candidate)
      if (match) matches.push(match)

      const processed = candidateIndex + 1
      if (processed === candidates.length || processed % 5 === 0) {
        onProgress?.(processed, candidates.length)
      }
    })
  } finally {
    analyzer.dispose()
  }

  return matches
}

export class CollisionMeshAnalyzer {
  private readonly meshes: Map<string, PreparedCollisionMesh>

  constructor(serializedMeshes: SerializedCollisionMesh[]) {
    this.meshes = new Map(
      serializedMeshes.map(mesh => [mesh.id, createPreparedMesh(mesh)] as const),
    )
  }

  analyzeCandidate(candidate: SerializedCollisionCandidate): CollisionNarrowPhaseMatch | null {
    for (const [aMeshId, bMeshId] of candidate.meshPairs) {
      const a = this.meshes.get(aMeshId)
      const b = this.meshes.get(bMeshId)
      if (!a || !b) continue
      const contact = meshPenetrationPoint(
        a,
        b,
        candidate.minimumPenetrationSceneUnits,
        candidate.sceneUnitsPerMillimeter,
      )
      if (!contact) continue
      return {
        pairId: candidate.id,
        contactPoint: [contact.x, contact.y, contact.z],
      }
    }
    return null
  }

  dispose(): void {
    this.meshes.forEach(mesh => mesh.geometry.dispose())
  }
}
