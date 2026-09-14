import * as THREE from 'three'

export type CollisionPolicy = {
  check: boolean
  group?: string
  groups?: string[]
  ignoreReason?: string
}

export type CollisionPolicySubject = {
  collisionGroup?: string
  collisionGroups?: readonly string[]
}

const COLLISION_IGNORE_LABEL_PREFIX = '__TERTIUS_COLLISION_IGNORE__ '
const COLLISION_GROUP_LABEL_PREFIX = '__TERTIUS_COLLISION_GROUP__ '
const COLLISION_GROUP_LABEL_SEPARATOR = ' :: '
const NORMALIZED_COLLISION_IGNORE_LABEL_PREFIX = '__TERTIUS_COLLISION_IGNORE___'
const NORMALIZED_COLLISION_GROUP_LABEL_PREFIX = '__TERTIUS_COLLISION_GROUP___'
const NORMALIZED_COLLISION_GROUP_LABEL_SEPARATOR = '__'

type CollisionGroupLabel = {
  group: string
  label: string
}

const parseCollisionGroupLabel = (name: string): CollisionGroupLabel | undefined => {
  const parse = (prefix: string, separator: string): CollisionGroupLabel | undefined => {
    if (!name.startsWith(prefix)) return undefined
    const marker = name.slice(prefix.length)
    const separatorIndex = marker.indexOf(separator)
    if (separatorIndex <= 0) return undefined
    const group = marker.slice(0, separatorIndex).trim()
    const label = marker.slice(separatorIndex + separator.length).trim()
    return group && label ? { group, label } : undefined
  }
  return parse(COLLISION_GROUP_LABEL_PREFIX, COLLISION_GROUP_LABEL_SEPARATOR)
    ?? parse(NORMALIZED_COLLISION_GROUP_LABEL_PREFIX, NORMALIZED_COLLISION_GROUP_LABEL_SEPARATOR)
}

export const collisionGroupFromLabel = (name: string): string | undefined => {
  return parseCollisionGroupLabel(name)?.group
}

export const collisionDisplayLabel = (name: string): string => {
  if (name.startsWith(COLLISION_IGNORE_LABEL_PREFIX)) {
    return name.slice(COLLISION_IGNORE_LABEL_PREFIX.length)
  }
  if (name.startsWith(NORMALIZED_COLLISION_IGNORE_LABEL_PREFIX)) {
    return name.slice(NORMALIZED_COLLISION_IGNORE_LABEL_PREFIX.length)
  }
  return parseCollisionGroupLabel(name)?.label ?? name
}

export const collisionPolicyForNode = (
  node: THREE.Object3D,
  root: THREE.Object3D,
): CollisionPolicy => {
  let current: THREE.Object3D | null = node
  const groups = new Set<string>()

  const addGroup = (value: unknown) => {
    if (typeof value === 'string' && value.trim()) groups.add(value.trim())
  }

  const result = (check: boolean, ignoreReason?: string): CollisionPolicy => {
    const values = [...groups]
    return {
      check,
      group: values[0],
      ...(values.length > 0 ? { groups: values } : {}),
      ...(ignoreReason ? { ignoreReason } : {}),
    }
  }

  while (current) {
    const directGroup = current.userData?.tertiusCollisionGroup
    addGroup(directGroup)

    const metadata = current.userData?.tertiusBom
    if (metadata && typeof metadata === 'object') {
      const collisionMetadata = metadata as {
        collision_check?: unknown
        collision_group?: unknown
        collision_groups?: unknown
        collision_ignore_reason?: unknown
      }
      addGroup(collisionMetadata.collision_group)
      if (Array.isArray(collisionMetadata.collision_groups)) {
        collisionMetadata.collision_groups.forEach(addGroup)
      }
      if (collisionMetadata.collision_check === false) {
        return result(
          false,
          typeof collisionMetadata.collision_ignore_reason === 'string'
            ? collisionMetadata.collision_ignore_reason
            : 'excluded by design metadata',
        )
      }
    }

    if (current.userData?.tertiusCollisionCheckDisabled === true) {
      return result(false, 'excluded by exported collision marker')
    }
    if (
      current.name.startsWith(COLLISION_IGNORE_LABEL_PREFIX)
      || current.name.startsWith(NORMALIZED_COLLISION_IGNORE_LABEL_PREFIX)
    ) {
      return result(false, 'excluded by design label marker')
    }
    addGroup(collisionGroupFromLabel(current.name))
    if (current === root) break
    current = current.parent
  }

  return result(true)
}

export const shouldAnalyzeCollisionPair = (
  a: CollisionPolicySubject,
  b: CollisionPolicySubject,
): boolean => {
  const aGroups = new Set(a.collisionGroups ?? (a.collisionGroup ? [a.collisionGroup] : []))
  const bGroups = b.collisionGroups ?? (b.collisionGroup ? [b.collisionGroup] : [])
  return !bGroups.some(group => aGroups.has(group))
}
