import * as THREE from 'three'

export type CollisionPolicy = {
  check: boolean
  group?: string
  ignoreReason?: string
}

export type CollisionPolicySubject = {
  collisionGroup?: string
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
  let group: string | undefined

  while (current) {
    const directGroup = current.userData?.tertiusCollisionGroup
    if (!group && typeof directGroup === 'string' && directGroup.trim()) {
      group = directGroup.trim()
    }

    const metadata = current.userData?.tertiusBom
    if (metadata && typeof metadata === 'object') {
      const collisionMetadata = metadata as {
        collision_check?: unknown
        collision_group?: unknown
        collision_ignore_reason?: unknown
      }
      if (!group && typeof collisionMetadata.collision_group === 'string' && collisionMetadata.collision_group.trim()) {
        group = collisionMetadata.collision_group.trim()
      }
      if (collisionMetadata.collision_check === false) {
        return {
          check: false,
          group,
          ignoreReason: typeof collisionMetadata.collision_ignore_reason === 'string'
            ? collisionMetadata.collision_ignore_reason
            : 'excluded by design metadata',
        }
      }
    }

    if (current.userData?.tertiusCollisionCheckDisabled === true) {
      return { check: false, group, ignoreReason: 'excluded by exported collision marker' }
    }
    if (
      current.name.startsWith(COLLISION_IGNORE_LABEL_PREFIX)
      || current.name.startsWith(NORMALIZED_COLLISION_IGNORE_LABEL_PREFIX)
    ) {
      return { check: false, group, ignoreReason: 'excluded by design label marker' }
    }
    if (!group) group = collisionGroupFromLabel(current.name)
    if (current === root) break
    current = current.parent
  }

  return { check: true, group }
}

export const shouldAnalyzeCollisionPair = (
  a: CollisionPolicySubject,
  b: CollisionPolicySubject,
): boolean => !(a.collisionGroup && a.collisionGroup === b.collisionGroup)
