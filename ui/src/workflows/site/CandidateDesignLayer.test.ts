import { Box3, BoxGeometry, Mesh, MeshBasicMaterial, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'

import { placeCandidateModelOnSite } from './CandidateDesignLayer'


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
