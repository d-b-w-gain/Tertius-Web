# Demo Shed Structural Verification Remediation Plan

**Goal:** Replace every unexplained `unsupported` structural result in the demo
shed with a traceable calculation result, correct the physical detail where a
calculation does not pass, and verify the deployed workbench against a fresh
compile of the current source snapshot.

**Document type:** Implementation and live-remediation plan

**Plan date:** 2026-09-01

## Non-negotiable rules

- Do not turn an unsupported result into a pass by changing UI colours, relaxing
  status gates, trusting project-authored capacity numbers, or assuming that
  visible contact proves restraint.
- Resolve resistance from exact compiled product identities, installed fastener
  counts/layouts, material and section facts, and immutable calculation/source
  evidence.
- Keep demand, resistance, utilisation, stiffness, and route-to-ground evidence
  separately visible.
- If a calculated joint fails, change the physical detail and recompile it; do
  not reduce the demand or safety factors to force a pass.
- Anchor subgroup calculations are already passing. Do not spend this work item
  re-litigating anchor pull-out or shear; only retain their result in the full
  base-joint load path.

## Reproduced live baseline

Source: latest cached live result for demo project `shed` on 2026-09-01.

| Verification family | Pass | Fail | Unsupported/not checked | Baseline defect |
| --- | ---: | ---: | ---: | --- |
| Cross-section | 201 | 0 | 0 | None reproduced |
| Serviceability | 103 | 0 | 22 | Non-applicable/unchecked rows require classification review |
| Member stability | 18 | 0 | 183 | 183 segments report missing credited restraints |
| Connections | 28 | 0 | 187 | 24 rendered joint families lack one or more resistance prerequisites |
| Tension members | 14 | 0 | 16 | Member resistance passes; one or both end connections are not credited |
| Restraint candidates | 0 | 0 | 5,376 candidate checks | Exact physical candidates exist, but capacity/stiffness/anchorage is withheld |

The largest restraint demand is approximately `0.318 kN`, with approximately
`0.0324 kNm` eccentric restraint moment. This is an evidence-chain problem,
not a reproduced numerical overload.

## Root-cause map

### A. PB1230HS / Cee / 100AC bolted joints

Tertius already calculates AS/NZS 4600 bolt shear, Cee-sheet bearing, tear-out,
hole size, spacing, and edge distance from managed PB1230HS and Cee facts. It
then correctly withholds the whole-joint pass because the 100AC fixture plate,
fixture-side bolt interface, joint stiffness, and anchorage route are not yet
calculated.

Affected families include roof-purlin supports, solid bridges, floor ledgers,
door/header seats, and the 100AC portions of restraint routes.

### B. Screw-fastened thin-sheet joints

The tension-member pack verifies the strap member and some exact screw end
details. Other wall-track, strap-to-purlin, noggin, sill, and mullion joints are
still reported as generic rendered connections. They need the same exact-product
AS/NZS 4600 screw bearing, tear-out, net-section, screw qualification, and
installed-count calculation instead of a definition-name allow-list.

### C. Fabricated knee and apex plates

The rendered 3 mm closed-web plates have the intended geometry and hole
alignment but currently carry no material grade, plate resistance, bolt-group
resistance, or stiffness calculation. Their governing live demands are about
`5.824 kN` axial, `5.118 kN` shear, and `1.269 kNm` moment. The calculation must
check plate gross/net section, bearing/tear-out/block shear, bolt-group shear and
moment distribution, and the declared rigid-zone stiffness basis. A failed
check requires a larger/thicker plate or revised bolt layout in the model.

### D. Route-to-ground gating

Member-restraint candidates are only creditable when both local connection
resistance/stiffness and a verified physical route to grounded components pass.
One unsupported joint currently poisons many otherwise small restraint checks.
The graph traversal must retain this fail-closed rule while exposing the first
blocking joint, and it must accept a route once every joint on that route has a
passing calculation.

## Implementation checklist

### 1. Evidence contracts and reusable calculations

- [x] Add a calculation result for a complete bolted cleat/fixture joint rather
  than treating the existing Cee-sheet interface as the whole connection.
- [x] Calculate bolt-group direct shear plus eccentric moment demand per
  installed bolt; do not divide moment by count alone.
- [x] Check both connected sheets/fixture legs, bolt shear, bearing, tear-out,
  spacing, edge distance, and net/block section as applicable.
- [x] Derive a documented minimum translational/torsional restraint stiffness
  from the exact fastener layout and connected plate/Cee geometry.
- [x] Generalise exact screw-joint verification to the rendered thin-sheet joint
  families used by the shed.
- [x] Add fabricated 3 mm gusset resistance and stiffness calculations for the
  knee/apex configurations.
- [x] Keep source hashes, standard clauses, equations, inputs, capacities,
  utilisation, assumptions, and blockers in the calculation sheets.

### 2. Project product and installation facts

- [x] Replace placeholder 100AC/PB1230HS connector products with the managed
  Lysaght definitions everywhere in the current shed source.
- [x] Add verified 100AC geometry/material/source facts required by the central
  calculation pack.
- [x] Add explicit knee/apex plate grade, thickness, outline, hole diameter,
  bolt coordinates, edge distances, and connected-member thicknesses.
- [x] Ensure every structural connection lists the exact rendered fasteners and
  fixture components once, with no orphan or duplicate connector identities.
- [ ] Correct any joint whose installed layout fails the calculation.

### 3. Result integration

- [x] Promote a connection to `pass` only when identity, calculation, and every
  applicable interaction/geometry prerequisite pass.
- [x] Feed passing complete-joint force/moment capacity and verified stiffness
  into matching restraint candidates.
- [ ] Traverse passing joints to ground and report the first blocker when no
  verified route exists.
- [ ] Re-run stability segmentation using the now-creditable restraint
  boundaries.
- [ ] Keep genuinely non-applicable serviceability rows distinct from missing
  implementation/evidence.

### 4. Automated validation

- [x] Unit-test pass, fail, identity mismatch, missing fact, inadequate edge
  distance, excessive eccentric moment, and inadequate stiffness cases.
- [ ] Regression-test all 24 live connection definition families through a
  deterministic compiled-design fixture.
- [ ] Assert that no calculation uses a project-authored `verified` flag or
  unreferenced numeric capacity as authority.
- [ ] Run the structural test suite and the authenticated full-stack live flow.

### 5. Live acceptance

- [ ] Deploy the API/worker image containing the calculation changes.
- [x] Update the demo `shed` project imports/details without replacing unrelated
  user source.
- [ ] Compile a fresh GLB plus BoM, procurement, and structural artifacts.
- [ ] Confirm the BoM/model digest match and the viewer loads without browser
  memory failure.
- [ ] Run a fresh structural solve and capture counts by status and family.
- [ ] Acceptance is zero explicit structural failures and zero unexplained
  `unsupported` connection, tension-end, restraint, or member-stability rows.
  Any remaining `not_checked` row must be demonstrably non-applicable and named
  as such; otherwise this plan remains incomplete.

## Evidence matrix to complete during implementation

| Joint mechanism | Required authority | Required project facts | Acceptance output |
| --- | --- | --- | --- |
| PB1230HS through Cee sheet | AS/NZS 4600 Clause 5.3 calculation plus pinned bolt/product source | bolt grade/area/diameter, Cee thickness and strengths, holes, spacing, edges | bolt, bearing, tear-out, geometry, interaction pass/fail |
| 100AC fixture/cleat | Pinned Lysaght product geometry/material source plus applicable steel connection calculation | leg dimensions/thickness, grade, holes, bolt coordinates, connected faces | complete cleat force/moment capacity and stiffness |
| Screw-fastened strap/track | AS/NZS 4600 screw connection calculation plus tested screw source | exact screws/count, sheet strengths/thickness, edge/spacing/hole layout | both-end connection capacity and route status |
| 3 mm knee/apex gusset | Applicable plate/connection calculation with declared material and exact fabricated geometry | plate grade/outline/thickness, bolt groups, Cee section facts | axial/shear/moment interaction and rigid-zone stiffness |
| Restraint route | Passing local connection checks and compiled physical graph | exact incidence from primary member to brace and onward to ground | credited restraint at both segment boundaries |

## Progress log

- [x] Reproduced the live result and counted every unsupported verification
  family.
- [x] Confirmed that the restraint candidates are physically detected and the
  governing restraint demand is small.
- [x] Confirmed that the existing bolted-sheet calculation passes the Cee side
  where managed PB1230HS facts are present, while the fixture/complete-joint
  calculation is the blocking prerequisite.
- [x] Calculation implementation complete.
- [x] Project-detail migration complete.
- [ ] Local validation complete.
- [ ] Live deployment and acceptance complete.
