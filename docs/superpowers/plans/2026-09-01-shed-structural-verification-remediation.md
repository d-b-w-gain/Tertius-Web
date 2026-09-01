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

### E. Solver mechanisms exposed by the corrected pin model

The first live solve with exact pins reached PyNite but produced eleven
null-space modes. A scaled SVD of the free-DOF stiffness matrix identified two
physical projection errors rather than overloaded members:

- Six modes were independent out-of-plane translations at the intermediate
  long-wall stud/bottom-track joints. The rendered long-wall X straps were only
  connected at portal-column endpoints even though the reusable strap component
  already supports two-screw intermediate connections. Add the actual two-screw
  strap-to-stud connections at every crossing and pass those ports into both X
  straps.
- The remaining dominant modes rocked each 2.4 m floor-ledger segment about one
  analytical midspan support. The CAD and BoM contain three masonry anchors per
  segment, but the structural graph collapsed them to one point. Project three
  distinct support ports and three pinned ground connections at the rendered
  anchor coordinates, preserving the same anchor quantity.

After recompilation, rerun the scaled null-space diagnostic before accepting
any result. A non-finite displacement, force, reaction, utilisation, or stored
JSON number is a solver failure, never a pass.

### F. False connection failures caused by result selection

One rendered joint can legitimately expose several calculation packs. The
resolver was returning the first pack, so a partial anchored-fixture result
masked a passing 100AC cleat result at non-ground joints. Select the first
applicable complete calculation for ordinary joints while keeping the
anchored-fixture path authoritative at genuinely grounded joints. Also identify
portal base fixtures by their pinned product capacity pack, rather than an old
project-authored status property.

### G. False gusset stiffness failures caused by analytical fragmentation

The knee/apex calculation used the first analytical fragment of each physical
Cee as its full length. A 150 mm fragment therefore produced an artificially
large `EI/L` stiffness demand. Sum every fragment belonging to the physical
component before calculating the required joint rotational stiffness. The
unchanged 3 mm plates then provide `134.207 kNm/rad` against a correctly derived
`86.422 kNm/rad` requirement and pass at `0.644` governing utilisation.

### H. Real screw qualification and floor-ledger detailing defects

The screw resolver deduplicated connected members by section/material identity,
so two different C10012 parts disappeared into one sheet and were reported as
unsupported. Deduplicate by physical component identity. Once calculated,
the existing 10g framing screw genuinely fails the AS/NZS 4600 Clause 5.4.2.5
tested-shear qualification for the C10012/C10019 joints. Replace those framing
joints with exact Buildex `6-311-3038-5C4` 14-20 x 22 Metal Teks backed by the
manufacturer's `11.2 kN` average single-shear result. Remove the two C10019
ledger splices entirely by ordering one 5.1 m ledger per side. The five
secondary 100AC bases must describe the Cee's round 14 mm holes; the separate
100AC product definition continues to describe the catalogue slots.

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
- [x] Correct every currently identified joint whose installed layout or exact
  hardware fails the calculation: qualified 14g framing screws, one-piece side
  ledgers, and correct secondary-base Cee hole geometry.
- [x] Connect every rendered long-wall X strap to the crossed C100 stud with the
  two managed screws already supported by the reusable strap component.
- [x] Project each of the three rendered floor-ledger anchors as a distinct
  analytical support instead of collapsing the group to one midspan node.

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
- [x] Run the focused structural calculation suite in the deployed image
  environment (`30 passed`).
- [ ] Run the authenticated full-stack live flow after deploying this patch.

### 5. Live acceptance

- [x] Deploy the API/worker image containing the calculation changes.
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
- [x] Diagnosed the post-pin singular matrix by scaled SVD: six long-wall
  stud/bottom-track sway modes plus floor-ledger rocking caused by collapsed
  physical connections.
- [x] Updated live `shed/design.py` with strap-to-stud fasteners and distinct
  floor-ledger anchor support nodes; source snapshot SHA-256 changed from
  `fe94c095...` to `c8b4f1b5...` and retained the existing source bundle.
- [x] Removed the residual released-rotation datum mechanism; the patched live
  stiffness matrix now has a zero-dimensional null space and the full solve has
  finite equilibrium residuals.
- [x] Corrected multi-pack connection selection, portal-base fixture detection,
  gusset physical-length stiffness, and same-section screw joint discovery.
- [x] Updated the live source bundle to SHA-256 `0acdc874...` for `design.py`,
  `83fb8768...` for `lysaght_zc.py`, and `835aa718...` for
  `shed_base_bracket.py`; the corrected source is compiling now.
- [x] Focused local/deployed-image validation complete: 30 structural tests pass.
- [ ] Fresh compile, null-space check, structural result counts, BoM digest, and
  viewer validation pending.
- [ ] Live deployment and acceptance complete.
