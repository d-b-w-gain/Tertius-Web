# Collision regression gate

Checkpoint: `mesh-bvh-shed-v1`

The collision count is diagnostic output, not a release criterion. A detector
change is acceptable only when the fixed expectations below continue to pass.

| Expectation | Source | Required result |
| --- | --- | --- |
| Crossing closed solids | Synthetic deterministic geometry | Must detect |
| OSB ceiling cutout versus internal skylight diffuser | Exact meshes extracted from live `shed` GLB at design commit `dab69a2` | Must reject |
| Matching left-side Custom Orb roof sheets | Exact labels extracted from the same GLB | Must skip by pair policy |
| Flexible strap or insulation batt | Exported `collision_check: false` metadata | Must skip component |
| Custom Orb sheet versus a structural member | Pair policy | Must still check |

## Layer contract

- Geometry decides whether rendered triangle meshes penetrate beyond the
  millimetre threshold.
- Policy decides which components or same-group pairs are intentionally outside
  the geometry check.
- Presentation searches and pages the complete confirmed result set; it does
  not change geometry or policy decisions.

## Release checklist

- [x] Keep collision policy in its own module and test it directly.
- [x] Check in a lightweight fixture extracted from the actual shed export.
- [x] Lock must-detect, must-reject, and must-ignore expectations in tests.
- [x] Show the checkpoint name in the inspector.
- [x] Keep all confirmed pairs searchable without mounting thousands of cards.
- [x] Run focused tests, typecheck, production build, and smoke browser check.
