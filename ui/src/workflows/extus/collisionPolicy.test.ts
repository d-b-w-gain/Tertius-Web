import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  collisionPolicyForNode,
  shouldAnalyzeCollisionPair,
} from './collisionPolicy'

describe('Extus collision policy', () => {
  it('keeps matching Custom Orb roof sheets out of geometric verification', () => {
    const root = new THREE.Group()
    const first = new THREE.Group()
    first.name = '__TERTIUS_COLLISION_GROUP__ roof-sheet-left :: Left Roof Sheet 1'
    const second = new THREE.Group()
    second.name = '__TERTIUS_COLLISION_GROUP__ roof-sheet-left :: Left Roof Sheet 2'
    root.add(first, second)

    const firstPolicy = collisionPolicyForNode(first, root)
    const secondPolicy = collisionPolicyForNode(second, root)

    expect(firstPolicy).toMatchObject({ check: true, group: 'roof-sheet-left' })
    expect(secondPolicy).toMatchObject({ check: true, group: 'roof-sheet-left' })
    expect(shouldAnalyzeCollisionPair(
      { collisionGroup: firstPolicy.group },
      { collisionGroup: secondPolicy.group },
    )).toBe(false)
  })

  it('still checks a Custom Orb sheet against a structural member', () => {
    expect(shouldAnalyzeCollisionPair(
      { collisionGroup: 'roof-sheet-left' },
      {},
    )).toBe(true)
  })

  it('suppresses only components sharing one of several local joint groups', () => {
    expect(shouldAnalyzeCollisionPair(
      { collisionGroups: ['stud-head-1', 'stud-base-1'] },
      { collisionGroups: ['stud-base-1'] },
    )).toBe(false)
    expect(shouldAnalyzeCollisionPair(
      { collisionGroups: ['stud-head-1', 'stud-base-1'] },
      { collisionGroups: ['stud-base-2'] },
    )).toBe(true)
  })

  it('excludes flexible components through design metadata with a reason', () => {
    const root = new THREE.Group()
    const batt = new THREE.Mesh()
    batt.userData.tertiusBom = {
      collision_check: false,
      collision_ignore_reason: 'flexible insulation batt',
    }
    root.add(batt)

    expect(collisionPolicyForNode(batt, root)).toEqual({
      check: false,
      ignoreReason: 'flexible insulation batt',
      group: undefined,
    })
  })
})
