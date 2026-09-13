import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { StructuralWindBasisPanel } from './StructuralWindBasisPanel'
import type { StructuralWindActionBasis } from './contracts'

afterEach(cleanup)

function basis(
  face: 'front' | 'right' | 'back' | 'left',
  direction: '+X' | '-X' | '+Y' | '-Y',
  qz: number,
  event: 'serviceability' | 'ultimate' = 'ultimate',
): StructuralWindActionBasis {
  return {
    id: `project-site-wind-${face}`,
    site_address: '14 Porter St',
    latitude: -34.4,
    longitude: 150.8,
    region: 'A2',
    region_area: 'NSW',
    region_source: 'test',
    region_approximate: false,
    region_status: 'verified',
    standard: 'AS/NZS 1170.2:2021',
    table_version: '2021',
    table_status: 'verified',
    importance_level: '2',
    annual_recurrence_interval_years: event === 'serviceability' ? 25 : 500,
    design_event: event,
    terrain_category: '3',
    reference_height_m: 3,
    regional_wind_speed_m_s: 45,
    climate_change_multiplier: 1,
    direction_multiplier: 0.9,
    terrain_height_multiplier: 0.75,
    shielding_multiplier: 1,
    topographic_multiplier: 1,
    site_wind_speed_m_s: 30.375,
    q_z_kPa: qz,
    building_face: face,
    face_bearing_degrees: 20,
    structural_action_direction: direction,
    governing_cardinal_direction: 'N',
    contributing_cardinal_directions: ['N', 'NE'],
    verifier_hash: 'test',
    provenance: 'test',
  }
}

describe('StructuralWindBasisPanel', () => {
  it('shows the four face pressures against the structural axes', () => {
    render(<StructuralWindBasisPanel bases={[
      basis('front', '+Y', 0.554),
      basis('right', '-X', 0.494),
      basis('back', '-Y', 0.683),
      basis('left', '+X', 0.554),
    ]} />)

    expect(screen.getByText('+Y · front face')).toBeInTheDocument()
    expect(screen.getByText('-X · right face')).toBeInTheDocument()
    expect(screen.getByText('-Y · back face')).toBeInTheDocument()
    expect(screen.getByText('+X · left face')).toBeInTheDocument()
    expect(screen.getByText('0.683 kPa')).toBeInTheDocument()
    expect(screen.getAllByText('N / NE → N')).toHaveLength(4)
    expect(screen.getByText('ULS ultimate · 1-in-500 years')).toBeInTheDocument()
  })

  it('keeps serviceability and ultimate directional pressures visible', () => {
    render(<StructuralWindBasisPanel bases={[
      basis('front', '+Y', 0.320, 'serviceability'),
      basis('right', '-X', 0.290, 'serviceability'),
      basis('back', '-Y', 0.380, 'serviceability'),
      basis('left', '+X', 0.320, 'serviceability'),
      basis('front', '+Y', 0.554),
      basis('right', '-X', 0.494),
      basis('back', '-Y', 0.683),
      basis('left', '+X', 0.554),
    ]} />)

    expect(screen.getByText('SLS serviceability · 1-in-25 years')).toBeInTheDocument()
    expect(screen.getByText('ULS ultimate · 1-in-500 years')).toBeInTheDocument()
    expect(screen.getAllByText('+Y · front face')).toHaveLength(2)
  })
})
