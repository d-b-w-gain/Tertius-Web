import { describe, expect, it } from 'vitest'

import { structureFootprintCoordinates } from './WindRegionMap'


describe('structureFootprintCoordinates', () => {
  it('rotates the nominated front edge clockwise from true north', () => {
    const northFacing = structureFootprintCoordinates(-34, 151, 12, 6, 0)
    const eastFacing = structureFootprintCoordinates(-34, 151, 12, 6, 90)

    const northFrontLatitude = (northFacing[0]![0] + northFacing[1]![0]) / 2
    const eastFrontLongitude = (eastFacing[0]![1] + eastFacing[1]![1]) / 2

    expect(northFrontLatitude).toBeGreaterThan(-34)
    expect(eastFrontLongitude).toBeGreaterThan(151)
    expect(northFacing).toHaveLength(4)
    expect(eastFacing).toHaveLength(4)
  })
})
