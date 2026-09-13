import { Box3, BoxGeometry, Group, Mesh, MeshBasicMaterial, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'

import { applyCandidateRepresentation, placeCandidateModelOnSite } from './CandidateDesignLayer'


describe('placeCandidateModelOnSite', () => {
  it('converts a glTF Y-up shed to map Z-up without changing its native scale', () => {
    const model = new Mesh(
      new BoxGeometry(3.804, 3.231, 5.9),
      new MeshBasicMaterial(),
    )

    const placement = placeCandidateModelOnSite(model, 12, 6)

    const bounds = new Box3().setFromObject(model)
    const centre = bounds.getCenter(new Vector3())
    const size = bounds.getSize(new Vector3())
    expect(centre.x).toBeCloseTo(0)
    expect(centre.y).toBeCloseTo(0)
    expect(bounds.min.z).toBeCloseTo(0)
    expect(size.x).toBeCloseTo(5.9)
    expect(size.y).toBeCloseTo(3.804)
    expect(size.z).toBeCloseTo(3.231)
    expect(placement.upAxis).toBe('y')
    expect(placement.sizeM).toEqual(size)
  })

  it('aligns the model long side with a portrait footprint', () => {
    const model = new Mesh(
      new BoxGeometry(3.804, 3.231, 5.9),
      new MeshBasicMaterial(),
    )

    placeCandidateModelOnSite(model, 4, 8)

    const bounds = new Box3().setFromObject(model)
    expect(bounds.min.z).toBeCloseTo(0)
    const size = bounds.getSize(new Vector3())
    expect(size.y).toBeCloseTo(5.9)
    expect(size.z).toBeCloseTo(3.231)
  })
})

describe('applyCandidateRepresentation', () => {
  it('retains envelope-labelled meshes and hides structural internals', () => {
    const root = new Group()
    const cladding = new Mesh(new BoxGeometry(1, 1, 1))
    cladding.name = 'wall_cladding_sheet'
    const frame = new Mesh(new BoxGeometry(1, 1, 1))
    frame.name = 'portal_frame_member'
    root.add(cladding, frame)

    expect(applyCandidateRepresentation(root, 'envelope')).toBe(1)
    expect(cladding.visible).toBe(true)
    expect(frame.visible).toBe(false)
  })

  it('honours explicit site-envelope metadata independently of node names', () => {
    const root = new Group()
    const flashing = new Mesh(new BoxGeometry(1, 1, 1))
    flashing.userData.tertius_representation = 'site_envelope'
    root.add(flashing)

    expect(applyCandidateRepresentation(root, 'envelope')).toBe(1)
    expect(flashing.visible).toBe(true)
  })
})
