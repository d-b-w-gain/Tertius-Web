# FBD Source and Compatibility Inventory

## Purpose

This is the first evidence capture for
[epic #330](https://github.com/d-b-w-gain/Tertius-Web/issues/330). It records
the legacy execution boundary, solver/runtime versions, reusable modules, and
known structural coverage gaps without copying the legacy source into Tertius
or executing the workflow during discovery.

The inventory was captured on 2026-07-27 from:

`W:\ben\ContextUI\default\workflows\shed\FBD`

## Entrypoint correction

The legacy workflow does **not** contain `design.py`. Its executable Python
entrypoint is:

`portal_frame_fbd_server.py`

The target Tertius boundary remains `design.py` plus its transitive local
imports. Migration therefore needs to create that project boundary around the
reusable domain/solver logic. Tertius should not import the legacy FastAPI
server as a design entrypoint.

The non-executing inventory command was:

```powershell
uv run python scripts/spikes/structural_source_inventory.py `
  W:\ben\ContextUI\default\workflows\shed\FBD `
  --entrypoint portal_frame_fbd_server.py
```

Inventory result:

- schema version: `1`;
- closure digest:
  `f45076bfd8136bba9425e868226b7418217ea70dd21de3c55a56d35883f47baf`;
- local source files: 12;
- import records: 88;
- syntax diagnostics: none;
- Python files outside the closure: `server_library_patch.py`.

The digest identifies this exact closure snapshot. A changed digest requires a
new comparison before a legacy result is accepted as a golden fixture.

## Local source closure

| File | Migration treatment |
| --- | --- |
| `portal_frame_fbd_server.py` | Do not port as project code. Keep only as API/input/output reference. |
| `unified_frame_analysis.py` | Extract the PyNite model builder/result adapter, then replace fixed shed topology with the versioned structural graph. |
| `member_calc.py` | Preserve formulas and report evidence as candidates; independently verify each check and remove fallback-to-stale behaviour. |
| `capacity_checks.py` | Candidate structural-pack domain logic after standards/formula verification. |
| `sections.py` | Candidate section/material data adapter; reconcile one section identity across geometry, FEA, checks, and procurement. |
| `purlins.py` | Preserve calculation intent, but separate domain math from FastAPI and connect reactions/load paths to the main graph. |
| `wind_pressure.py` | Preserve job-specific wind calculation intent; separate Pydantic/FastAPI transport from domain calculation. |
| `wind_region.py` | Preserve site lookup behaviour and shapefile provenance behind a Tertius extension boundary. |
| `units.py` | Preserve explicit units; remove import-time `pip install` and legacy plain-float ambiguity. |
| `_utils.py` | Reuse only narrowly needed deterministic serialization helpers. |
| `paths.py` | Do not port; replace workflow-local cache paths with Tertius artifacts/persistence. |
| `routes_projects.py` | Do not port; Tertius already owns project persistence and tenant isolation. |

`server_library_patch.py` is outside the executable import closure. It remains a
review/reference file rather than migration input.

The TypeScript/React UI, Three.js cube/prism viewer, client-only cladding
tables, BOM logic, images, generated cache files, and job list are not part of
the Python closure. They are reference evidence for the new workbench,
Procurement Workbench, reports, and golden fixtures—not code to copy blindly.

## External runtime

The legacy `shed` virtual environment records:

| Runtime/package | Version |
| --- | --- |
| Python | 3.12.4 |
| PyNiteFEA | 2.4.1 |
| NumPy | 2.4.4 |
| SciPy | 1.17.1 |
| Pint | 0.25.3 |
| pyshp | 3.0.3 |
| Shapely | 2.1.2 |
| FastAPI | 0.136.1 |
| Pydantic | 2.13.3 |
| Uvicorn | 0.46.0 |

PyNiteFEA 2.4.1 metadata declares Python `>=3.11`, NumPy `>=2.4.0`,
PrettyTable, SciPy, and Matplotlib. Its classifiers explicitly list Python
3.11–3.13. The current Tertius lock uses Python 3.14, NumPy 2.4.6, and SciPy
1.17.1. The branch smoke image workflow and isolated
`tertius-fbd-smoke` Helm release proved installation, import, and the
deterministic cantilever solve in the canonical Python 3.14 API/compile image.

A minimal cantilever was run directly against the legacy environment, without
importing FBD. For a 1 kN tip load on a 2 m cantilever, PyNiteFEA 2.4.1 returned:

- fixed reaction `FX = -1.0 kN`;
- fixed reaction `MY = -2.0 kN·m`;
- tip displacement `DX = 0.013333... m` for the spike properties.

This proves the package and the FBD-used API (`FEModel3D`, materials, sections,
nodes, supports, members, nodal loads, combinations, and linear solve) in both
the legacy Python 3.12 environment and the Tertius Python 3.14 image.

## Current analytical model

The unified FEA is not generated from a general shed layout.

- `MEMBER_TOPOLOGY` is a fixed list of 22 members.
- Node creation is a fixed list of 15 nodes: one gable frame plus two interior
  portal frames.
- Six fixed base-node IDs receive supports.
- `n_bays` is echoed into result geometry, but it does not generate additional
  FEA frames, members, or braced bays.
- Columns, rafters, eave/ridge struts, and four straps are the only FEA member
  roles.
- Strap end moments are released, but the linear solve does not implement
  tension-only removal. Legacy job 11 records this as pending.
- Purlins and girts are analysed separately as idealised simply supported or
  two-span beams. They are not nodes/members in the unified 22-member model, and
  their reactions do not form an explicit load-transfer chain into it.

This confirms that the new wall layouts cannot be represented faithfully by
changing `n_bays` or a few scalar inputs. The Tertius structural graph must be
generated from actual design layout entities.

## Confirmed current-order coverage gaps

### Internal cladding

`dead_kPa` is one scalar applied as rafter point loads at computed purlin
positions. Section self-weight is also applied. The client-side cladding tab
knows external cladding product mass in kg/m² for quantities, but that mass does
not feed the structural model. There is no internal-cladding input, area,
orientation, or load mapping.

Result: internal cladding weight is not traceably included.

### C100 battens, purlins, and girts

The saved `Porter.json` analysis uses:

- C10019 columns and rafters;
- C10012 eave/ridge struts;
- TH64x0.95 purlins, side-wall girts, and gable girts in the separate purlin
  result.

It does not contain the newly selected C100 battens. Purlin/girt stiffness,
continuity, restraint, connection assumptions, and reactions are separate from
the unified frame graph.

Result: the new C100 batten selection is not in the captured analysis state.

### Walls, window, and doors

The structural input schemas contain no wall-segment, opening, window, door,
header/lintel, jamb, interrupted-stud, or interrupted-bracing entities. Wall and
gable areas are treated as complete rectangles/triangles for wind and
quantities. Calculation-sheet text assumes `Cpi = 0` for a fully enclosed
building or one with no dominant opening.

Result: revised wall layouts and the window/doors do not alter geometry,
tributary areas, internal pressure, framing, stiffness, bracing, or load paths.

### Trust and stale-result handling

The current code contains several paths that are unsuitable for an ordering
gate:

- if calculation-sheet rebuilding fails, analysis intentionally keeps earlier
  summary numbers instead of returning a blocking state;
- the global exception handler returns HTTP 200 with an error body;
- section lookup has previously diverged between FEA, cover sheet, BOM, and
  calculation sheets; legacy job 14 remains pending and marks this as a
  correctness blocker;
- the analysis writes debug snapshots to hard-coded
  `C:\Users\ben\AppData\Local\Temp` paths;
- `units.py` installs Pint with `pip` at import time if missing;
- server documentation says PyNite uses millimetres internally while the active
  unified analysis explicitly uses metres.

Tertius must use explicit `pass`, `fail`, `warning`, `unsupported`,
`not_checked`, and `stale` states and must never convert an analysis exception
into a successful result.

## Saved-state comparison

The cache does not identify one safe current-order baseline:

| File | Timestamp | Structural state |
| --- | --- | --- |
| `Porter.json` | 2026-05-18 | 3.1 m × 2.4 m, C10019 columns/rafters, C10012 struts, TH64x0.95 separate purlins/girts; overall frame utilisation 0.774; gable girt utilisation 0.998 |
| `last.json` | 2026-06-04 | 3.0 m × 3.0 m, C20024 columns, Z15019 rafters, C10012 struts; longitudinal result fails two wall straps at utilisation 1.128 |

Neither state contains the requested internal cladding, new C100 battens,
revised wall layout, window, or doors. Neither should be treated as the order
baseline without identifying the current Tertius design and reconciling every
changed detail.

## Next compatibility slice

1. Add PyNiteFEA 2.4.1 to an isolated Tertius dependency spike and prove
   install/import on Python 3.14.
2. Reproduce the minimal cantilever in the canonical image.
3. Convert one small `design.py` fixture into linked Build123D physical members
   and a structural graph.
4. Solve the graph through a PyNite adapter and validate equilibrium, local
   axes, releases, reactions, and deterministic JSON.
5. Do not migrate FBD topology until the graph supports arbitrary frames, wall
   segments, openings, battens/girts/purlins, braces, offsets, and connections.
