import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { StructuralWorkbench } from './StructuralWorkbench'
import type { ProjectStructuralCapture, StructuralSnapshot } from './contracts'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  getAccessToken: vi.fn(),
  login: vi.fn(),
}))

vi.mock('../../api/client', () => ({ apiFetch: mocks.apiFetch }))
vi.mock('../../auth/AuthProvider', () => ({
  useAuth: () => ({
    authMode: 'authenticated',
    getAccessToken: mocks.getAccessToken,
    login: mocks.login,
  }),
}))
vi.mock('../extus/ui/ViewerTab', () => ({
  LatestModelViewer: ({
    externalSelectedNodeIds,
    structuralOverlays,
    onStructuralRestraintSelect,
  }: {
    externalSelectedNodeIds?: string[]
    onStructuralRestraintSelect?: (restraintId: string) => void
    structuralOverlays?: Array<{
      mode?: string
      status?: string
      stations: unknown[]
      loadArrows?: unknown[]
      nodes?: unknown[]
      reactions?: unknown[]
      restraintSegments?: Array<{ id: string }>
      restraintMarkers?: Array<{
        requiredForceKN?: number | null
        evidenceStatus: string
      }>
      stageFocus?: {
        order: number
        label: string
        visualDescription: string
        metrics: Array<{ label: string; value: string }>
        legend: Array<{ label: string }>
      }
    }>
  }) => (
    <div>
      Viewer selection: {externalSelectedNodeIds?.join(',')}
      {' · '}
      Ribbon stations: {
        structuralOverlays?.reduce(
          (count, overlay) => count + overlay.stations.length,
          0,
        ) || 0
      }
      {' · '}
      Ribbon mode: {structuralOverlays?.[0]?.mode}
      {' · '}
      Ribbon status: {structuralOverlays?.[0]?.status}
      {' · '}
      Load arrows: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.loadArrows?.length ?? 0),
          0,
        ) || 0
      }
      {' · '}
      Nodes: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.nodes?.length ?? 0),
          0,
        ) || 0
      }
      {' · '}
      Reactions: {
        structuralOverlays?.reduce(
          (count, overlay) => count + (overlay.reactions?.length ?? 0),
          0,
        ) || 0
      }
      {' · '}
      Stage focus: {structuralOverlays?.[0]?.stageFocus
        ? `Stage ${structuralOverlays[0].stageFocus.order} · ${structuralOverlays[0].stageFocus.label}`
        : 'none'}
      {' · '}
      Stage visual: {structuralOverlays?.[0]?.stageFocus?.visualDescription || 'none'}
      {' · '}
      Stage metrics: {structuralOverlays?.[0]?.stageFocus?.metrics
        .map((metric) => `${metric.label} ${metric.value}`)
        .join(', ') || 'none'}
      {' · '}
      Stage legend: {structuralOverlays?.[0]?.stageFocus?.legend
        .map((item) => item.label)
        .join(', ') || 'none'}
      {' · '}
      Demand markers: {structuralOverlays?.reduce(
        (count, overlay) => count + (overlay.restraintMarkers?.length ?? 0),
        0,
      ) || 0}
      {' · '}
      Missing evidence markers: {structuralOverlays?.reduce(
        (count, overlay) => count + (overlay.restraintMarkers
          ?.filter((marker) => marker.evidenceStatus === 'missing').length ?? 0),
        0,
      ) || 0}
      {' · '}
      Maximum marker demand: {Math.max(
        0,
        ...(structuralOverlays?.flatMap((overlay) => overlay.restraintMarkers
          ?.map((marker) => marker.requiredForceKN ?? 0) ?? []) ?? []),
      ).toFixed(4)} kN
      {structuralOverlays?.[0]?.restraintSegments?.[0] && (
        <button
          type="button"
          onClick={() => onStructuralRestraintSelect?.(
            structuralOverlays[0]!.restraintSegments![0]!.id,
          )}
        >
          Select restraint trace
        </button>
      )}
    </div>
  ),
}))

const capture: ProjectStructuralCapture = {
  schema_version: '0.1',
  project_name: 'structural_test',
  design_hash: 'abc123',
  title: 'Structural Workbench — C100 wall connection microcosm',
  authoring_mode: 'generated',
  design_basis: {
    framework_id: 'AU-NCC-2022',
    framework_label: 'NCC 2022 Amendment 2 Australian structural verification',
    framework_reference: 'NCC 2022 Amendment 2, Volume Two Part H1',
    jurisdiction: 'Australia',
    analysis_method: '3D first-order elastic frame analysis',
    building_classification: 'Class 10a',
    importance_level: '2',
    design_life_years: 50,
    compliance_pathway: 'Engineered solution',
    standards: { wind: 'AS/NZS 1170.2 test mapping' },
    supplemental_methods: [{
      id: 'SCI-P399',
      label: 'SCI P399 portal-frame stability workflow',
      reference: 'Sections 4–12',
      role: 'Supplemental analysis guidance; not the Australian compliance basis',
    }],
  },
  wind_action_bases: [],
  components: [
    {
      id: 'sheet',
      label: 'Custom Orb roofing iron',
      kind: 'surface',
      visual_node_id: 'sheet',
      grounded: false,
      part_number: 'CUSTOM-ORB',
    },
    {
      id: 'screws',
      label: 'Roof sheet Tek screws',
      kind: 'connector',
      visual_node_id: 'screws',
      grounded: false,
      part_number: 'TEK',
    },
    {
      id: 'purlin',
      label: 'Lysaght C10019 purlin',
      kind: 'member',
      visual_node_id: 'purlin',
      grounded: false,
      part_number: 'C10019',
    },
    {
      id: 'gpb',
      label: 'Lysaght 100GPB bracket',
      kind: 'support',
      visual_node_id: 'gpb',
      grounded: false,
      part_number: '100GPB',
    },
    {
      id: 'anchors',
      label: 'M12 masonry anchor bolts',
      kind: 'connector',
      visual_node_id: 'anchors',
      grounded: false,
      part_number: 'M12X100',
    },
    {
      id: 'block',
      label: 'Grounded concrete block',
      kind: 'ground',
      visual_node_id: 'block',
      grounded: true,
      part_number: null,
    },
  ],
  connections: [
    {
      id: 'sheet-purlin',
      label: 'Roofing iron fixed to C100 flange',
      from_component_id: 'sheet',
      to_component_id: 'purlin',
      connector_component_ids: ['screws'],
      transfers: ['wind_normal', 'force', 'shear'],
    },
    {
      id: 'purlin-gpb',
      label: 'C100 web bolted to 100GPB',
      from_component_id: 'purlin',
      to_component_id: 'gpb',
      connector_component_ids: [],
      transfers: ['force', 'shear', 'moment'],
    },
    {
      id: 'gpb-ground',
      label: '100GPB anchored to concrete',
      from_component_id: 'gpb',
      to_component_id: 'block',
      connector_component_ids: ['anchors'],
      transfers: ['force', 'shear', 'moment'],
    },
  ],
  loads: [
    {
      id: 'wind',
      label: 'Illustrative inward wind pressure on roofing iron',
      case: 'wind',
      case_id: 'case-wind-inward',
      component_id: 'sheet',
      pressure_kPa: 0.8,
      area_m2: 0.9144,
      direction: { x: 0, y: -1, z: 0 },
      provenance: 'Illustrative parser example',
      wind_basis_id: null,
      net_pressure_coefficient: null,
      coefficient_status: null,
    },
  ],
  load_paths: [
    {
      load_id: 'wind',
      status: 'complete',
      component_ids: ['sheet', 'purlin', 'gpb', 'block'],
      connection_ids: ['sheet-purlin', 'purlin-gpb', 'gpb-ground'],
      grounded_component_id: 'block',
      detail: 'Load reaches grounded component Grounded concrete block.',
    },
  ],
  analysis: null,
  capabilities: [
    {
      id: 'capture',
      label: 'Design capture',
      status: 'online',
      detail: 'Parsed.',
    },
    {
      id: 'checks',
      label: 'Member checks',
      status: 'pending',
      detail: 'Not solved.',
    },
  ],
  warnings: ['LOAD PATH CAPTURE ONLY'],
}

const analysis: StructuralSnapshot = {
  schema_version: '2.0',
  mode: 'design',
  title: capture.title,
  subtitle: 'Active-project first-order elastic member demand',
  source: {
    kind: 'design',
    label: 'structural_test',
    design_id: 'structural_test',
    design_hash: 'abc123',
  },
  design_basis: capture.design_basis,
  wind_action_bases: capture.wind_action_bases,
  units: {
    length: 'm',
    force: 'kN',
    moment: 'kN.m',
    displacement: 'mm',
    render_length: 'mm',
  },
  nodes: [
    {
      id: 'purlin-start',
      label: 'Purlin start',
      position: { x: 0, y: 0, z: 0 },
      restraints: { dx: true, dy: true, dz: true, rx: true, ry: true, rz: true },
      visual_node_id: 'purlin',
    },
    {
      id: 'purlin-end',
      label: 'Purlin end',
      position: { x: 0, y: 0, z: 1.6 },
      restraints: { dx: false, dy: false, dz: false, rx: false, ry: false, rz: false },
      visual_node_id: 'purlin',
    },
  ],
  members: [
    {
      id: 'purlin-axis',
      label: 'Lysaght C10019 purlin',
      start_node_id: 'purlin-start',
      end_node_id: 'purlin-end',
      section_id: 'c10019',
      material_id: 'steel',
      visual_node_id: 'purlin',
    },
  ],
  sections: [
    {
      id: 'c10019',
      label: 'C10019',
      area_m2: 409e-6,
      iy_m4: 142000e-12,
      iz_m4: 673000e-12,
      torsion_j_m4: 492e-12,
      mass_kg_m: 3.29,
      bending_reference_kNm: 5.535,
      bending_reference_axis: 'local_z',
      bending_reference_basis: 'Nominal Zxe × fy yield reference only.',
      catalog: {
        catalog_id: 'lysaght-zc-v2',
        catalog_version: '2.0',
        section_key: 'C10019 (100x1.9)',
        source: 'Lysaght guide p.7-8',
        record_sha256: 'a'.repeat(64),
        axis_mapping: {
          local_y_inertia: 'Iy_mm4',
          local_z_inertia: 'Ix_mm4',
        },
        properties: {
          A_mm2: 409,
          fy_MPa: 450,
          Zxe_mm3: 12300,
        },
      },
    },
  ],
  materials: [
    {
      id: 'steel',
      label: 'Steel',
      elastic_modulus_kN_m2: 200000000,
      shear_modulus_kN_m2: 80000000,
      poisson_ratio: 0.3,
      density_kg_m3: 7850,
    },
  ],
  load_cases: [
    { id: 'case-wind-inward', label: 'Inward wind pressure', category: 'wind' },
    { id: 'case-wind-outward', label: 'Outward wind suction', category: 'wind' },
    { id: 'case-dead', label: 'Dead load', category: 'dead' },
  ],
  load_combinations: [
    {
      id: 'SLS-1.0',
      label: 'Serviceability actions',
      limit_state: 'serviceability',
      factors: { 'case-wind-inward': 1 },
    },
    {
      id: 'SLS-G',
      label: 'Permanent actions',
      limit_state: 'serviceability',
      factors: { 'case-dead': 1 },
    },
    {
      id: 'DEMO-OVERLOAD',
      label: 'Deliberate overload',
      limit_state: 'ultimate',
      factors: { 'case-dead': 1, 'case-wind-inward': 12 },
    },
  ],
  unavailable_load_combinations: [
    {
      id: 'SLS-G+WY+',
      label: 'Permanent plus longitudinal wind +Y',
      limit_state: 'serviceability',
      family: 'action_standard',
      missing_inputs: ['wind_positive_y'],
      reason: 'No longitudinal wind +Y action is generated from the Site basis and compiled structural topology.',
    },
    {
      id: 'ULS-STABILITY+X',
      label: 'P399 global-stability actions +X',
      limit_state: 'ultimate',
      family: 'global_stability',
      missing_inputs: ['p399_equivalent_horizontal_force'],
      reason: 'Tertius has not yet generated the SCI P399 equivalent horizontal force from the compiled frame topology.',
    },
  ],
  loads: [],
  member_loads: [0.35, 0.8, 1.25].map((distance, index) => ({
    id: `wind-${index}`,
    label: `Wind ${index}`,
    member_id: 'purlin-axis',
    case_id: 'case-wind-inward',
    distance_m: distance,
    force: { x: 0, y: -0.24384, z: 0 },
    moment: { x: 0, y: 0, z: 0 },
    source_load_id: 'wind',
    provenance: 'Equal screws',
  })),
  member_distributed_loads: [],
  reactions: [
    {
      node_id: 'purlin-start',
      combination_id: 'SLS-1.0',
      force: { x: 0, y: 0.73152, z: 0 },
      moment: { x: -0.585216, y: 0, z: 0 },
    },
  ],
  member_results: [
    {
      member_id: 'purlin-axis',
      combination_id: 'SLS-1.0',
      max_moment_kNm: 0.585216,
      max_shear_kN: 0.73152,
      max_axial_kN: 0,
      max_displacement_mm: 2.61231263,
    },
  ],
  member_diagrams: [
    {
      member_id: 'purlin-axis',
      visual_node_id: 'purlin',
      stations: [
        {
          distance_m: 0,
          position: { x: 0, y: 0, z: 0 },
          moment_kNm: { x: -0.585216, y: 0, z: 0 },
          major_moment_kNm: { x: -0.585216, y: 0, z: 0 },
          minor_moment_kNm: { x: 0, y: 0, z: 0 },
          shear_kN: { x: 0, y: 0.73152, z: 0 },
          displacement_mm: { x: 0, y: 0, z: 0 },
        },
        {
          distance_m: 1.6,
          position: { x: 0, y: 0, z: 1.6 },
          moment_kNm: { x: 0, y: 0, z: 0 },
          major_moment_kNm: { x: 0, y: 0, z: 0 },
          minor_moment_kNm: { x: 0, y: 0, z: 0 },
          shear_kN: { x: 0, y: 0, z: 0 },
          displacement_mm: { x: 0, y: -2.61231263, z: 0 },
        },
      ],
    },
  ],
  member_checks: [
    {
      member_id: 'purlin-axis',
      label: 'C100 bending demand',
      demand_kNm: 0.585216,
      capacity_kNm: 5.535,
      utilisation: 0.1057301,
      status: 'not_checked',
      basis: 'RENDERER REFERENCE ONLY — nominal Zxe × fy.',
    },
  ],
  connection_checks: [
    {
      connection_id: 'gpb-ground',
      label: '100GPB anchored to concrete',
      status: 'unsupported',
      evidence_status: 'unverified',
      pack_id: 'project-demo-base-v1',
      pack_version: '0.1',
      identity_status: 'pass',
      identity_mismatches: [],
      governing_combination_id: 'DEMO-OVERLOAD',
      governing_member_id: 'purlin-axis',
      axial_demand_kN: 0,
      shear_demand_kN: 8.778,
      moment_demand_kNm: 7.0226,
      design_axial_capacity_kN: null,
      design_shear_capacity_kN: null,
      design_moment_capacity_kNm: null,
      axial_utilisation: null,
      shear_utilisation: null,
      moment_utilisation: null,
      governing_utilisation: null,
      expected_connector_part_numbers: ['M12X100'],
      rendered_connector_part_numbers: ['M12X100'],
      source: 'Project demonstration detail',
      source_sha256: null,
      anchor_group: {
        status: 'pass',
        evidence_status: 'verified',
        pack_id: 'manufacturer_working_load_anchor_group',
        pack_version: '1',
        anchor_part_number: 'AS12100WGM',
        anchor_count: 2,
        effective_anchor_count: 1,
        substrate_type: 'concrete_block',
        substrate_status: 'verified',
        tension_demand_kN: 0.1,
        shear_demand_kN: 0.216,
        tension_capacity_kN: 1.15,
        shear_capacity_kN: 2.1,
        interaction_utilisation: 0.19,
        installed_effective_embedment_mm: 88,
        reference_embedment_mm: 60,
        minimum_edge_distance_mm: 50,
        required_edge_distance_mm: 35,
        minimum_spacing_mm: 35,
        required_spacing_mm: 35,
        embedment_status: 'pass',
        edge_distance_status: 'pass',
        spacing_status: 'pass',
        source: 'Ramset SARB ANZ Edition 3',
        source_sha256: 'b'.repeat(64),
        basis: 'Verified lower-bound anchor group.',
        blockers: [],
      },
      bolted_sheet_interface: {
        status: 'pass',
        evidence_status: 'verified',
        pack_id: 'as_nzs_4600_2005_a1_bolted_sheet_interface',
        pack_version: '1',
        bolt_part_number: 'PB1230HS',
        bolt_count: 4,
        connected_member_id: 'purlin-axis',
        connected_sheet_part_number: 'C10019',
        fixture_part_number: 'SHED-C100-ONS-BASE-6-4B',
        fixture_capacity_status: 'not_checked',
        resultant_shear_demand_kN: 1.04,
        design_bolt_shear_capacity_kN: 125.48,
        design_sheet_bearing_capacity_kN: 78.797,
        design_sheet_tearout_capacity_kN: 67.853,
        governing_capacity_kN: 67.853,
        governing_utilisation: 0.0153,
        nominal_bolt_diameter_mm: 12,
        connected_sheet_thickness_mm: 1.9,
        hole_diameter_mm: 14,
        hole_type: 'standard_round',
        minimum_spacing_mm: 40,
        required_spacing_mm: 36,
        minimum_edge_distance_mm: 31,
        required_edge_distance_mm: 18,
        bolt_shear_status: 'pass',
        sheet_bearing_status: 'pass',
        sheet_tearout_status: 'pass',
        hole_status: 'pass',
        spacing_status: 'pass',
        edge_distance_status: 'pass',
        source: 'Lysaght Zeds and Cees guide',
        source_sha256: 'a'.repeat(64),
        basis: 'AS/NZS 4600 Clause 5.3 checks.',
        blockers: [
          'Fixture SHED-C100-ONS-BASE-6-4B plate resistance remains a separate check.',
        ],
      },
      basis: 'No verified anchor or concrete resistance source is connected.',
      assumptions: ['Demand only.'],
    },
  ],
  serviceability_checks: [
    {
      member_id: 'purlin-axis',
      label: 'C100 deflection',
      combination_id: 'SLS-1.0',
      displacement_mm: 2.61231263,
      limit_mm: 6.4,
      utilisation: 0.408,
      status: 'pass',
      basis: 'Project demonstration criterion L/250.',
    },
  ],
  load_summary: {
    member_mass_kg: 0,
    self_weight_kN: 0,
    additional_dead_load_kN: 0,
    imposed_load_kN: 0,
    wind_load_kN: 0.73152,
  },
  equilibrium: {
    force_residual_kN: { x: 0, y: 0, z: 0 },
    moment_residual_kNm: { x: 0, y: 0, z: 0 },
    tolerance: 1e-8,
    status: 'pass',
  },
  solver: {
    name: 'PyNiteFEA',
    version: '2.4.1',
    analysis: '3D first-order elastic',
    combination_id: 'SLS-1.0',
  },
  verification_stages: [
    {
      id: 'geometry',
      order: 1,
      label: 'Geometry',
      primary_reference: 'NCC H1P1',
      supplemental_references: ['SCI P399 §§3, 6.1'],
      status: 'pass',
      summary: 'One member, two nodes, one support.',
      sheet_ids: ['sheet-au-geometry'],
      blocking_stage_ids: [],
    },
    {
      id: 'stability',
      order: 5,
      label: 'Global stability',
      primary_reference: 'AS/NZS 4600:2018 stability',
      supplemental_references: ['SCI P399 §§7.2–7.8'],
      status: 'blocked',
      summary: 'Imperfections and second-order effects are missing.',
      sheet_ids: [],
      blocking_stage_ids: ['analysis'],
    },
  ],
  calculation_sheets: [
    {
      id: 'sheet-au-geometry',
      stage_id: 'geometry',
      title: 'Geometry and analytical scheme',
      status: 'pass',
      primary_reference: 'NCC H1P1',
      supplemental_references: ['SCI P399 Sections 3 and 6.1'],
      purpose: 'Prove which design.py geometry became nodes, members, and supports.',
      assumptions: ['Fixed base is an authored analysis assumption.'],
      inputs: [
        {
          symbol: 'n_member',
          label: 'Analytical members',
          value: 1,
          unit: null,
          source: 'design.py member_axis',
        },
      ],
      equations: [
        {
          label: 'Purlin length',
          expression: 'L = |x_j - x_i|',
          substitution: '|1.6 - 0|',
          result: 1.6,
          unit: 'm',
        },
      ],
      outputs: [],
      references: ['SCI P399'],
      related_member_ids: ['purlin-axis'],
      related_node_ids: ['purlin-start', 'purlin-end'],
      related_load_case_ids: [],
      related_combination_ids: [],
    },
  ],
  certification_readiness: {
    scheme_id: 'AU-NCC-2022',
    scheme_label: 'Australian structural certification readiness',
    document_status: 'engineering_review_draft',
    draft_document_label: 'DRAFT ENGINEERING REVIEW REPORT — NOT A STRUCTURAL CERTIFICATE',
    ready_for_engineering_review: true,
    ready_for_certificate: false,
    ready_for_order: false,
    conclusion: 'Analysis evidence is available, but certification remains blocked.',
    blocking_gate_ids: ['stability'],
    blocking_reasons: ['System stability: incomplete.'],
    gates: [
      {
        id: 'analysis',
        order: 1,
        label: 'Structural analysis',
        status: 'pass',
        primary_reference: 'AS/NZS 1170.0',
        summary: 'Analysis passes.',
        stage_ids: ['geometry'],
      },
      {
        id: 'stability',
        order: 2,
        label: 'System stability',
        status: 'blocked',
        primary_reference: 'AS/NZS 4600:2018',
        summary: 'Stability remains open.',
        stage_ids: ['stability'],
      },
    ],
    model_coverage: {
      status: 'complete',
      compiled_member_count: 1,
      solved_member_count: 1,
      missing_result_member_ids: [],
      summary: 'PyNite results cover all 1 compiled analytical members.',
    },
    issues: [],
  },
  capabilities: [
    {
      id: 'solver',
      label: 'PyNite demand',
      status: 'online',
      detail: 'Solved.',
    },
  ],
  warnings: ['ELASTIC MEMBER DEMAND ONLY'],
}

const overloadAnalysis: StructuralSnapshot = {
  ...analysis,
  member_results: analysis.member_results.map((result) => ({
    ...result,
    combination_id: 'DEMO-OVERLOAD',
    max_moment_kNm: 7.0226,
  })),
  member_checks: analysis.member_checks.map((check) => ({
    ...check,
    demand_kNm: 7.0226,
    utilisation: 1.2688,
    status: 'not_checked',
  })),
  solver: {
    ...analysis.solver,
    combination_id: 'DEMO-OVERLOAD',
  },
}

const crossSectionAnalysis: StructuralSnapshot = {
  ...analysis,
  verification_stages: [
    ...analysis.verification_stages,
    {
      id: 'cross_section',
      order: 6,
      label: 'Cross-section',
      primary_reference: 'AS/NZS 4600:2018 cross-section resistance',
      supplemental_references: ['SCI P399 §8.1'],
      status: 'pass',
      summary: 'Both-axis resistance is calculated; the collector path remains candidate evidence.',
      sheet_ids: ['sheet-au-cross-section'],
      blocking_stage_ids: [],
    },
    {
      id: 'member_stability',
      order: 7,
      label: 'Member stability',
      primary_reference: 'AS/NZS 4600:2005+A1 member resistance',
      supplemental_references: [],
      status: 'pass',
      summary: 'Both-axis member resistance and interaction pass.',
      sheet_ids: [],
      blocking_stage_ids: [],
    },
  ],
  cross_section_checks: [
    {
      member_id: 'purlin-axis',
      label: 'C100 purlin cross-section',
      pack_id: 'as_nzs_4600_2005_a1_ewm',
      status: 'pass',
      governing_combination_id: 'ULS-WIND',
      governing_station_m: 0.8,
      axial_kN: 0,
      major_moment_kNm: 0.3321,
      minor_moment_kNm: 0.1209,
      web_shear_kN: 0.499,
      off_axis_shear_kN: 0.186,
      torsion_kNm: 0,
      design_compression_capacity_kN: 31.2,
      design_major_bending_capacity_kNm: 4.4,
      design_minor_bending_capacity_kNm: 0.8,
      design_web_shear_capacity_kN: 22.1,
      design_off_axis_shear_capacity_kN: 15.7,
      design_st_venant_torsion_capacity_kNm: 0.04,
      axial_bending_utilisation: 0.0755,
      biaxial_axial_bending_utilisation: 0.2266,
      bending_shear_utilisation: 0.0788,
      minor_bending_shear_utilisation: 0.1516,
      torsion_utilisation: 0,
      governing_utilisation: 0.2266,
      section_record_sha256: 'a'.repeat(64),
      capacity_factors: { phi_c: 0.85, phi_b: 0.9, phi_v: 0.9 },
      web_slenderness: 48.7,
      shear_regime: 'stocky',
      standard_reference: 'AS/NZS 4600:2005 incorporating Amendment No. 1',
      standard_status: 'accepted_project_basis_2005_a1_with_developments_supplement',
      standard_source_sha256: 'b'.repeat(64),
      developments_supplement_sha256: 'c'.repeat(64),
      off_axis_load_path_status: 'candidate',
      off_axis_required_reaction_kN: 0.186,
      off_axis_source_component_ids: ['sheet'],
      off_axis_source_connection_ids: ['sheet-purlin'],
      off_axis_collector_component_ids: ['purlin', 'gpb', 'anchors', 'block'],
      off_axis_collector_connection_ids: ['purlin-gpb', 'gpb-ground'],
      off_axis_grounded_component_id: 'block',
      off_axis_load_path_basis: 'Authored force/shear path reaches grounded block.',
      basis: 'Versioned catalogue effective-width capacity pack.',
      assumptions: ['Collector resistance and stiffness remain unverified.'],
    },
  ],
  member_stability_checks: [
    {
      segment_id: 'purlin-segment-01',
      member_id: 'purlin-axis',
      label: 'C100 purlin member stability',
      pack_id: 'as_nzs_4600_2005_a1_member',
      status: 'pass',
      governing_combination_id: 'ULS-WIND',
      governing_station_m: 0.8,
      segment_start_m: 0,
      segment_end_m: 1.6,
      unbraced_length_m: 1.6,
      axial_kN: 0.4,
      major_moment_kNm: 0.3321,
      minor_moment_kNm: 0.1209,
      web_shear_kN: 0.499,
      off_axis_shear_kN: 0.186,
      torsion_kNm: 0.002,
      elastic_flexural_buckling_stress_MPa: 210,
      elastic_torsional_buckling_stress_MPa: 175,
      elastic_flexural_torsional_buckling_stress_MPa: 128,
      elastic_distortional_compression_stress_MPa: 245,
      elastic_distortional_bending_stress_MPa: 370,
      elastic_lateral_torsional_buckling_moment_kNm: 1.2,
      elastic_minor_lateral_torsional_buckling_moment_kNm: 0.4,
      elastic_major_axis_flexural_buckling_load_kN: 95,
      elastic_minor_axis_flexural_buckling_load_kN: 19.5,
      nominal_global_buckling_stress_MPa: 180,
      nominal_global_compression_capacity_kN: 18,
      nominal_distortional_compression_capacity_kN: 22,
      nominal_lateral_torsional_bending_capacity_kNm: 0.9,
      nominal_distortional_bending_capacity_kNm: 1.1,
      nominal_minor_lateral_torsional_bending_capacity_kNm: 0.24,
      design_member_compression_capacity_kN: 15.3,
      design_major_bending_capacity_kNm: 0.81,
      design_minor_bending_capacity_kNm: 0.216,
      design_global_compression_capacity_kN: 15.3,
      design_distortional_compression_capacity_kN: 18.7,
      design_lateral_torsional_bending_capacity_kNm: 0.81,
      design_distortional_bending_capacity_kNm: 0.99,
      design_section_minor_bending_capacity_kNm: 0.73,
      design_minor_lateral_torsional_bending_capacity_kNm: 0.216,
      design_web_shear_capacity_kN: 22.1,
      design_off_axis_shear_capacity_kN: 15.7,
      design_st_venant_torsion_capacity_kNm: 0.04,
      governing_compression_mode: 'global',
      governing_bending_mode: 'lateral_torsional',
      governing_minor_bending_mode: 'lateral_torsional',
      axial_utilisation: 0.026,
      axial_bending_utilisation: 0.66,
      major_bending_utilisation: 0.41,
      minor_bending_utilisation: 0.56,
      web_shear_utilisation: 0.023,
      off_axis_shear_utilisation: 0.012,
      torsion_utilisation: 0.05,
      major_axis_amplification_factor: 1.004,
      minor_axis_amplification_factor: 1.021,
      biaxial_member_interaction_utilisation: 0.61,
      major_bending_shear_utilisation: 0.411,
      minor_bending_shear_utilisation: 0.56,
      governing_utilisation: 0.66,
      lateral_bending_restraint: 'unverified',
      restraint_status: 'candidate',
      compression_flange: 'positive_local_y',
      restraint_candidate_ids: ['restraint-purlin'],
      distortional_buckling_status: 'verified',
      section_record_sha256: 'a'.repeat(64),
      standard_reference: 'AS/NZS 4600:2005 incorporating Amendment No. 1',
      standard_status: 'accepted_project_basis_2005_a1_with_developments_supplement',
      standard_source_sha256: 'b'.repeat(64),
      developments_supplement_sha256: 'c'.repeat(64),
      basis: 'Conservative both-axis unbraced member resistance.',
      assumptions: ['No physical restraint benefit credited.'],
    },
  ],
}

const restraintAnalysis: StructuralSnapshot = {
  ...analysis,
  member_restraint_candidate_checks: [
    {
      id: 'candidate-check-restraint-purlin-SLS-1.0',
      candidate_id: 'restraint-purlin',
      member_id: 'purlin-axis',
      connection_id: 'sheet-purlin',
      combination_id: 'SLS-1.0',
      contact_flange: 'positive_local_y',
      status: 'candidate',
      demand_model: 'as_nzs_4600_2005_4_3_2_flange_force',
      transferred_load_kN: 0.73152,
      load_eccentricity_m: 0.05,
      member_depth_m: 0.1,
      required_force_kN: 0.54864,
      required_moment_kNm: 0.054864,
      available_force_kN: null,
      available_moment_kNm: null,
      force_utilisation: null,
      moment_utilisation: null,
      stiffness_status: 'unverified',
      evidence_pack_id: 'lysaght-zc-test-pack',
      evidence_pack_version: '1.0',
      identity_status: 'pass',
      identity_mismatches: [],
      evidence_references: ['Test evidence reference'],
      anchorage_status: 'unverified',
      anchorage_component_ids: ['purlin', 'roof-diaphragm'],
      anchorage_connection_ids: ['purlin-diaphragm'],
      anchorage_grounded_component_id: null,
      anchorage_basis: 'The test diaphragm has no grounded collector.',
      mechanism: 'AS/NZS 4600 critical-flange restraint force.',
      provenance: 'Builder-derived purlin and registered connection.',
      basis: 'Working AISI D3.2.2 adaptation; AS/NZS verification remains open.',
    },
  ],
  member_restraint_traces: [
    {
      id: 'restraint-trace-purlin-SLS-1.0',
      member_id: 'purlin-axis',
      combination_id: 'SLS-1.0',
      segment_start_m: 0,
      segment_end_m: 1.6,
      start_position: { x: 0, y: 0, z: 0 },
      end_position: { x: 0, y: 0, z: 1.6 },
      compression_flange: 'positive_local_y',
      status: 'candidate',
      start_restraint_candidate_ids: ['restraint-purlin'],
      end_restraint_candidate_ids: ['restraint-purlin'],
      effective_restraint_candidate_ids: ['restraint-purlin'],
      governing_candidate_check_ids: ['candidate-check-restraint-purlin-SLS-1.0'],
      required_restraint_force_kN: 0.54864,
      available_restraint_force_kN: null,
      restraint_force_utilisation: null,
      basis: 'Signed local moment requires the positive local-y flange restraint.',
    },
  ],
}

const stage8Analysis: StructuralSnapshot = {
  ...analysis,
  members: analysis.members.map((member) => ({
    ...member,
    tension_only: true,
  })),
  tension_member_checks: [
    {
      member_id: 'purlin-axis',
      label: '30 x 1 mm G450 wall strap',
      status: 'unsupported',
      capacity_status: 'candidate',
      member_capacity_status: 'verified',
      connection_capacity_status: 'candidate',
      pack_id: 'as_nzs_4600_2005_a1_tension',
      governing_combination_id: 'DEMO-OVERLOAD',
      tension_demand_kN: 4.2,
      tension_capacity_kN: 6.9768,
      end_connection_capacity_kN: null,
      governing_capacity_kN: 6.9768,
      member_utilisation: 0.602,
      connection_utilisation: null,
      governing_utilisation: 0.602,
      end_fastener_count: 2,
      rendered_end_connection_count: 2,
      rendered_end_fastener_counts: [2, 2],
      required_force_per_end_fastener_kN: 2.1,
      gross_area_mm2: 30,
      net_area_mm2: 19,
      gross_yield_capacity_kN: 12.15,
      net_fracture_capacity_kN: 6.9768,
      connected_part_net_capacity_kN: 5.928,
      end_bearing_capacity_kN: 4.8,
      end_tearout_capacity_kN: 11.52,
      end_fastener_part_numbers: ['6-311-0695-5MP'],
      end_fastener_product_keys: ['buildex:smooth-top-tek:6-311-0695-5mp'],
      end_fastener_product_definition_digests: ['d'.repeat(64)],
      fastener_tested_single_shear_strength_kN: 5.75,
      fastener_required_single_shear_strength_kN: 2.5,
      fastener_shear_qualification_status: 'pass',
      fastener_evidence_status: 'verified',
      fastener_evidence_source: 'Buildex Product Data Sheet 31195-PDS, Issue 2',
      fastener_evidence_revision: 'Issue 2, 5 July 2017',
      fastener_evidence_url: 'https://www.buildex.com.au/products/steel-frame-housing/6-on-site/132-smooth-top-wafer-hex-teks',
      spacing_status: 'pass',
      edge_distance_status: 'pass',
      standard_reference: 'AS/NZS 4600:2005 incorporating Amendment No. 1',
      standard_status: 'accepted_project_basis_2005_a1_with_developments_supplement',
      standard_source_sha256: 'b'.repeat(64),
      developments_supplement_sha256: 'c'.repeat(64),
      basis: 'Tertius-owned tension and connected-part resistance.',
      assumptions: [
        'The fastener product has no Section 8 tested screw shear resistance.',
      ],
    },
  ],
  bracing_load_path_traces: [
    {
      id: 'bracing-path:purlin-axis',
      member_id: 'purlin-axis',
      component_id: 'purlin',
      governing_combination_id: 'DEMO-OVERLOAD',
      status: 'candidate',
      tension_demand_kN: 4.2,
      component_ids: ['block', 'anchors', 'gpb', 'purlin', 'sheet'],
      connection_ids: ['gpb-ground', 'purlin-gpb', 'sheet-purlin'],
      grounded_component_ids: ['block'],
      blockers: ['The rendered end connection has no complete verified resistance.'],
      basis: 'Both physical brace ends were traversed through the compiled connection graph.',
    },
  ],
}

const analysisCache = {
  status: 'hit' as const,
  key_digest: 'a'.repeat(64),
  engine_version: 'test-engine',
  calculated_at: '2026-08-19T00:00:00Z',
  calculation_duration_seconds: 105.5,
}

function structuralResponse(
  url: string,
  captureValue: ProjectStructuralCapture,
  analysisValue: StructuralSnapshot,
) {
  const body = url.includes('/active/workbench')
    ? {
        capture: captureValue,
        analysis: analysisValue,
        analysis_error: null,
        cache: analysisCache,
      }
    : analysisValue
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'X-Tertius-Structural-Cache': 'HIT',
      'X-Tertius-Structural-Cache-Key': analysisCache.key_digest.slice(0, 12),
      'X-Tertius-Structural-Engine': analysisCache.engine_version,
      'X-Tertius-Structural-Calculated-At': analysisCache.calculated_at,
      'X-Tertius-Structural-Calculation-Seconds': String(
        analysisCache.calculation_duration_seconds,
      ),
    },
  })
}

async function openDetailedAnalysis() {
  const toggle = await screen.findByRole('button', { name: 'Show detailed analysis' })
  expect(toggle).toHaveAttribute('aria-expanded', 'false')
  fireEvent.click(toggle)
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Hide detailed analysis' }))
      .toHaveAttribute('aria-expanded', 'true')
  })
}

describe('StructuralWorkbench', () => {
  afterEach(cleanup)

  beforeEach(() => {
    mocks.apiFetch.mockReset()
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, analysis),
    ))
  })

  it('uses the active project capture and never presents connectivity as a capacity check', async () => {
    render(<StructuralWorkbench isActive />)

    await waitFor(() => {
      expect(screen.getByText('structural_test')).toBeInTheDocument()
    })
    expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/structural/active/workbench',
      mocks.getAccessToken,
    )
    expect(mocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(screen.getByText('SAVED ANALYSIS')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Structural verification summary' }))
      .toBeInTheDocument()
    expect(screen.queryByText('Custom Orb roofing iron')).not.toBeInTheDocument()

    await openDetailedAnalysis()

    expect(screen.getAllByText('Custom Orb roofing iron').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Lysaght C10019 purlin').length).toBeGreaterThan(0)
    expect(screen.getByText('Reaches ground')).toBeInTheDocument()
    expect(screen.getByText('0.732 kN')).toBeInTheDocument()
    expect(screen.getByText('HANDLE-AUTHORED')).toBeInTheDocument()
    expect(screen.getByText('Inward wind pressure')).toBeInTheDocument()
    expect(screen.getByText('Outward wind suction')).toBeInTheDocument()
    expect(screen.getByText('Cross-section status: NOT CHECKED')).toBeInTheDocument()
    expect(screen.getByText(/Ribbon stations: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Ribbon mode: moment/)).toBeInTheDocument()
    expect(screen.getByText(/Ribbon status: not_checked/)).toBeInTheDocument()
    expect(screen.getByText(/Load arrows: 3/)).toBeInTheDocument()
    expect(screen.getByText(/Nodes: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Reactions: 1/)).toBeInTheDocument()
    expect(screen.getByText('Australian verification detail')).toBeInTheDocument()
    expect(screen.getByText('Australian certification readiness')).toBeInTheDocument()
    expect(screen.getByText(/DRAFT ENGINEERING REVIEW REPORT/)).toBeInTheDocument()
    expect(screen.getByText(/Supplemental method: SCI-P399/)).toBeInTheDocument()
    expect(screen.getByText('Geometry and analytical scheme')).toBeInTheDocument()
    expect(screen.getByText(/Global stability/)).toBeInTheDocument()
    expect(screen.getByText('0.5852 kN·m')).toBeInTheDocument()
    expect(screen.getByText('Equilibrium pass')).toBeInTheDocument()
    expect(screen.getByText('Validated catalogue section')).toBeInTheDocument()
    expect(screen.getByText('C10019 (100x1.9)')).toBeInTheDocument()
    expect(screen.getByText('project-demo-base-v1 v0.1')).toBeInTheDocument()
    expect(screen.getByText('Identity pass')).toBeInTheDocument()
    expect(screen.getByText(/Resistance unavailable/)).toBeInTheDocument()
    expect(screen.getByText('2× AS12100WGM')).toBeInTheDocument()
    expect(screen.getByText('Anchor pass')).toBeInTheDocument()
    expect(screen.getByText('Interaction 0.190')).toBeInTheDocument()
    expect(screen.getByText('C10019 / 4x PB1230HS')).toBeInTheDocument()
    expect(screen.getByText('Web interface pass')).toBeInTheDocument()
    expect(screen.getByText(/Sheet bearing 78\.797 kN · pass/)).toBeInTheDocument()
    expect(screen.getByText(/Fixture SHED-C100-ONS-BASE-6-4B · plate not checked/)).toBeInTheDocument()
    expect(screen.getByText('2 unavailable')).toBeInTheDocument()
    expect(screen.getByRole('option', {
      name: /SLS-G\+WY\+ · unavailable — No longitudinal wind \+Y action/,
    })).toBeDisabled()
    expect(screen.getByRole('option', {
      name: /ULS-STABILITY\+X · unavailable — Tertius has not yet generated/,
    })).toBeDisabled()

    fireEvent.click(screen.getByText('2 unavailable'))
    expect(screen.getByText('Combinations waiting for required actions')).toBeInTheDocument()
    expect(screen.getAllByText(/SCI P399 equivalent horizontal force/).length).toBeGreaterThan(0)

    fireEvent.click(screen.getAllByRole('button', { name: /Grounded concrete block/ })[0]!)
    expect(screen.getByText(/Viewer selection: block/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'displacement' }))
    expect(screen.getByText(/Ribbon mode: displacement/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'moment' }))
    expect(screen.getByText(/Ribbon mode: moment/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Load combination'), {
      target: { value: 'SLS-G' },
    })
    await waitFor(() => {
      expect(mocks.apiFetch).toHaveBeenCalledWith(
        '/api/structural/active/analysis?combination_id=SLS-G',
        mocks.getAccessToken,
      )
    })
  })

  it('retries a gateway timeout after the backend stores a fresh analysis', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(new Response(null, { status: 524 }))
      .mockImplementation((url: string) => Promise.resolve(
        structuralResponse(url, capture, analysis),
      ))

    render(<StructuralWorkbench isActive />)

    await waitFor(() => {
      expect(screen.getByText('structural_test')).toBeInTheDocument()
    })
    expect(mocks.apiFetch).toHaveBeenCalledTimes(2)
    expect(screen.getByText('SAVED ANALYSIS')).toBeInTheDocument()
  })

  it('distinguishes complete PyNite coverage from a calculated design failure', async () => {
    const diagnosedAnalysis: StructuralSnapshot = {
      ...analysis,
      certification_readiness: {
        ...analysis.certification_readiness!,
        issues: [
          {
            id: 'cross-section-design-failures',
            stage_id: 'cross_section',
            kind: 'design_failure',
            owner: 'design',
            count: 12,
            title: 'Member cross-sections exceed calculated resistance',
            detail: 'These are numerical failures, not missing PyNite definitions.',
            next_action: 'Revise the affected members and rerun the capacity pack.',
            affected_ids: ['purlin-axis'],
          },
        ],
      },
    }
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, diagnosedAnalysis),
    ))

    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    expect(await screen.findByText('PyNite model coverage')).toBeInTheDocument()
    expect(screen.getByText(/PyNite results cover all 1 compiled analytical members/))
      .toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: /Member cross-sections exceed calculated resistance/,
    })).toHaveTextContent('12 · design')
    expect(screen.getByText(/not missing PyNite definitions/)).toBeInTheDocument()
  })

  it('reloads the structural declaration when the shared active project changes', async () => {
    render(<StructuralWorkbench isActive />)
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalled())
    const requestCountBeforeChange = mocks.apiFetch.mock.calls.length

    window.dispatchEvent(
      new CustomEvent('tertius:active-project-changed', {
        detail: { activeProject: 'another-project' },
      }),
    )

    await waitFor(() => {
      expect(mocks.apiFetch.mock.calls.length).toBeGreaterThan(requestCountBeforeChange)
    })
  })

  it('shows and highlights the authored off-axis action and collector path', async () => {
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, crossSectionAnalysis),
    ))

    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    await waitFor(() => {
      expect(screen.getByText('AS/NZS 4600 Stage 7 member stability')).toBeInTheDocument()
    })
    const memberStageSelector = screen.getByRole('group', {
      name: 'Selected member verification stage',
    })
    const crossSectionButton = within(memberStageSelector).getByRole('button', {
      name: /6\. Cross-section/i,
    })
    const memberStabilityButton = within(memberStageSelector).getByRole('button', {
      name: /7\. Member stability/i,
    })
    expect(crossSectionButton).toBeInTheDocument()
    expect(memberStabilityButton).toBeInTheDocument()
    expect(screen.getByText('Interaction / torsion')).toBeInTheDocument()
    expect(screen.getByText('Axial amplification Mz / My')).toBeInTheDocument()

    fireEvent.click(crossSectionButton)

    expect(screen.getByText('AS/NZS 4600 Stage 6 cross-section')).toBeInTheDocument()
    expect(screen.getByText('Minor-axis My / resistance')).toBeInTheDocument()
    expect(screen.getByText('Torque / St-Venant resistance')).toBeInTheDocument()
    expect(screen.getByText('Off-axis load path')).toBeInTheDocument()
    expect(screen.getByText('Required support reaction: 0.1860 kN')).toBeInTheDocument()
    expect(screen.getByText('Action source: sheet')).toBeInTheDocument()
    expect(screen.getByText(
      'Collector to ground: purlin → gpb → anchors → block',
    )).toBeInTheDocument()
    expect(screen.getByText(/Viewer selection:.*sheet.*purlin.*gpb.*anchors.*block/))
      .toBeInTheDocument()
    expect(screen.getByText('Cross-section status: PASS')).toBeInTheDocument()

    fireEvent.click(memberStabilityButton)
    expect(screen.getByText('AS/NZS 4600 Stage 7 member stability')).toBeInTheDocument()
  })

  it('shows the governing working envelope and coefficient basis explicitly', async () => {
    const workingCapture: ProjectStructuralCapture = {
      ...capture,
      loads: capture.loads.map((load) => ({
        ...load,
        net_pressure_coefficient: 0.8,
        coefficient_status: 'working_conservative',
      })),
    }
    const workingAnalysis: StructuralSnapshot = {
      ...analysis,
      solver: {
        ...analysis.solver,
        combination_selection: 'governing_working_envelope',
      },
    }
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, workingCapture, workingAnalysis),
    ))

    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    expect(await screen.findByText('Governing working envelope')).toBeInTheDocument()
    expect(screen.getByText('Net coefficient Cnet')).toBeInTheDocument()
    expect(screen.getByText('working conservative')).toBeInTheDocument()
  })

  it('shows the Tertius-owned wind surface coefficient trace', async () => {
    const verifiedCapture: ProjectStructuralCapture = {
      ...capture,
      loads: capture.loads.map((load) => ({
        ...load,
        net_pressure_coefficient: -1.3,
        coefficient_status: 'verified',
        surface_action_pack_id: 'as_nzs_1170_2_rectangular_enclosed_main_frame_v1',
        external_pressure_coefficient: -0.6,
        internal_pressure_coefficient: 0.7,
        area_reduction_factor: 1,
      })),
    }
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, verifiedCapture, analysis),
    ))

    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    expect(await screen.findByText('Tertius surface-action pack')).toBeInTheDocument()
    expect(screen.getByText(
      'as_nzs_1170_2_rectangular_enclosed_main_frame_v1',
    )).toBeInTheDocument()
    expect(screen.getByText('External Cp,e')).toBeInTheDocument()
    expect(screen.getByText('Internal Cp,i')).toBeInTheDocument()
    expect(screen.getByText('Area factor Ka')).toBeInTheDocument()
    expect(screen.getByText('verified')).toBeInTheDocument()
  })

  it('keeps an exceeded renderer reference not-checked until Australian gates pass', async () => {
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(
        url,
        capture,
        url.includes('combination_id=DEMO-OVERLOAD') ? overloadAnalysis : analysis,
      ),
    ))
    render(<StructuralWorkbench isActive />)
    await waitFor(() => {
      expect(screen.getAllByText('structural_test').length).toBeGreaterThan(0)
    })
    await openDetailedAnalysis()

    fireEvent.change(screen.getByLabelText('Load combination'), {
      target: { value: 'DEMO-OVERLOAD' },
    })

    expect(await screen.findByText(/Ribbon status: not_checked/)).toBeInTheDocument()
    expect(screen.getByText('126.9% reference utilisation')).toBeInTheDocument()
  })

  it('opens auditable demand, capacity, and provenance from a selected 3D trace', async () => {
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, restraintAnalysis),
    ))
    render(<StructuralWorkbench isActive />)

    fireEvent.click(await screen.findByRole('button', { name: 'Select restraint trace' }))

    expect(screen.getByText('Selected 3D restraint trace')).toBeInTheDocument()
    expect(screen.getByText('0.5486 kN')).toBeInTheDocument()
    expect(screen.getByText('not verified')).toBeInTheDocument()
    expect(screen.getByText('restraint-purlin')).toBeInTheDocument()
    expect(screen.getAllByText('Identity pass')).toHaveLength(2)
    expect(screen.getByText(/2.5% critical flange force/)).toBeInTheDocument()
    expect(screen.getByText('Anchorage unverified')).toBeInTheDocument()
    expect(screen.getByText('The test diaphragm has no grounded collector.')).toBeInTheDocument()
    expect(screen.getByText(
      'Builder-derived purlin and registered connection.',
    )).toBeInTheDocument()
  })

  it('opens the governing restraint trace when Stage 8 is selected', async () => {
    const stage8RestraintAnalysis: StructuralSnapshot = {
      ...restraintAnalysis,
      verification_stages: [
        ...restraintAnalysis.verification_stages,
        {
          id: 'bracing',
          order: 8,
          label: 'Bracing/restraint',
          primary_reference: 'AS/NZS 4600:2005+A1',
          supplemental_references: [],
          status: 'warning',
          summary: 'One physical restraint location; stiffness remains unverified.',
          sheet_ids: ['sheet-au-bracing'],
          blocking_stage_ids: ['member_stability'],
        },
      ],
      calculation_sheets: [
        ...restraintAnalysis.calculation_sheets,
        {
          id: 'sheet-au-bracing',
          stage_id: 'bracing',
          title: 'Bracing and restraint',
          status: 'warning',
          primary_reference: 'AS/NZS 4600:2005+A1',
          supplemental_references: [],
          purpose: 'Verify physical member restraint.',
          assumptions: ['Support-side bolt stiffness remains unverified.'],
          inputs: [],
          equations: [],
          outputs: [],
          references: [],
          related_member_ids: ['purlin-axis'],
          related_node_ids: [],
          related_load_case_ids: [],
          related_combination_ids: ['SLS-1.0'],
        },
      ],
    }
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, stage8RestraintAnalysis),
    ))
    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    fireEvent.click(await screen.findByRole('button', { name: /8\. Bracing\/restraint/ }))

    expect(screen.getByText('Selected 3D restraint trace')).toBeInTheDocument()
    expect(screen.getByText(/2.5% critical flange force/)).toBeInTheDocument()
    expect(screen.getByText('Bracing and restraint')).toBeInTheDocument()
    expect(screen.getByText(/Stage focus: Stage 8 · Bracing\/restraint/)).toBeInTheDocument()
    expect(screen.getByText(/Compression-flange restraint segments/)).toBeInTheDocument()
    expect(screen.getByText(/Physical locations 1/)).toBeInTheDocument()
    expect(screen.getByText(/Exact products 1/)).toBeInTheDocument()
    expect(screen.getByText(/AS\/NZS demand 1/)).toBeInTheDocument()
    expect(screen.getByText(/Missing stiffness \/ anchorage ring/)).toBeInTheDocument()
    expect(screen.getByText(/Demand markers: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Missing evidence markers: 2/)).toBeInTheDocument()
    expect(screen.getByText(/Maximum marker demand: 0\.5486 kN/)).toBeInTheDocument()
  })

  it('shows mechanically located restraint candidates that are not yet credited', async () => {
    const locatedOnlyAnalysis: StructuralSnapshot = {
      ...restraintAnalysis,
      member_restraint_traces: (restraintAnalysis.member_restraint_traces ?? []).map(
        (trace) => ({
          ...trace,
          status: 'missing',
          effective_restraint_candidate_ids: [],
          governing_candidate_check_ids: [],
          required_restraint_force_kN: null,
          available_restraint_force_kN: null,
          restraint_force_utilisation: null,
        }),
      ),
    }
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, locatedOnlyAnalysis),
    ))
    render(<StructuralWorkbench isActive />)

    fireEvent.click(await screen.findByRole('button', { name: 'Select restraint trace' }))

    expect(screen.getByText('restraint-purlin')).toBeInTheDocument()
    expect(screen.getByText('located — not credited')).toBeInTheDocument()
    expect(screen.getByText('The test diaphragm has no grounded collector.')).toBeInTheDocument()
  })

  it('shows Stage 8 strap resistance and the real bracing path blocker', async () => {
    mocks.apiFetch.mockImplementation((url: string) => Promise.resolve(
      structuralResponse(url, capture, stage8Analysis),
    ))

    render(<StructuralWorkbench isActive />)

    await openDetailedAnalysis()

    expect(await screen.findByText('Stage 8 strap check')).toBeInTheDocument()
    expect(screen.getByText('Strap design capacity')).toBeInTheDocument()
    expect(screen.getByText('Gross / net area')).toBeInTheDocument()
    expect(screen.getByText('30.00 / 19.00 mm²')).toBeInTheDocument()
    expect(screen.getByText('Spacing / edge distance')).toBeInTheDocument()
    expect(screen.getByText('Spacing / edge distance').parentElement)
      .toHaveTextContent('pass / pass')
    expect(screen.getByText('Rendered end fasteners').parentElement)
      .toHaveTextContent('2 / 2 across 2 ends')
    expect(screen.getByText('Selected global bracing trace')).toBeInTheDocument()
    expect(screen.getByText('Grounded components')).toBeInTheDocument()
    expect(screen.getByText(
      'The rendered end connection has no complete verified resistance.',
    )).toBeInTheDocument()
    expect(screen.getByText(/Viewer selection:.*block.*anchors.*gpb.*purlin.*sheet/))
      .toBeInTheDocument()
  })
})
