# FBD Structural Design Workbench Implementation Plan

> **For agentic workers:** Keep these checkboxes current as work lands. Use
> focused tasks/PRs under epic #330; do not treat this kickoff PR as authority to
> port the entire legacy application in one change.

**Goal:** Make structural consequences of `design.py` changes visible,
traceable, and verifiable in a Tertius Structural Design Workbench, beginning
with the current FBD shed and its imminent ordering decisions.

**Architecture:** Compile one explicit project source closure into linked
Build123D physical geometry and a versioned analytical structural graph. Run the
legacy structural package behind a solver adapter and publish versioned model
and result artifacts consumed by the workbench and calculation reports.

**Primary design:** `docs/structural/fbd-structural-workbench.md`

**Tracking epic:** [#330](https://github.com/d-b-w-gain/Tertius-Web/issues/330)

---

### Task 1: Kick off the epic and inventory the real source boundary

**Files:**

- Add: `server/core/structural/__init__.py`
- Add: `server/core/structural/source_inventory.py`
- Add: `scripts/spikes/structural_source_inventory.py`
- Add: `server/tests/structural/test_source_inventory.py`
- Add: `docs/structural/fbd-structural-workbench.md`
- Add: `docs/structural/fbd-source-inventory.md`

- [x] Create #330, add it to GitHub Project 1, and set it to In Progress.
- [x] Create an isolated `codex/fbd-structural-workbench` branch/worktree from
  current `origin/master`.
- [x] Document the physical/analytical split, node placement, traceability gate,
  solver boundary, and current-order risk items.
- [x] Add a non-executing AST inventory for `design.py`, transitive local
  imports, external imports, literal runtime-file references, and out-of-closure
  Python files.
- [x] Add focused inventory tests proving local/package/relative/dynamic import
  handling, non-execution, diagnostics, and deterministic hashes.
- [x] Run the inventory against the actual legacy entrypoint,
  `portal_frame_fbd_server.py`, under
  `W:\ben\ContextUI\default\workflows\shed\FBD`.
- [x] Record the redacted source/runtime/model summary and closure digest in
  `docs/structural/fbd-source-inventory.md`.
- [ ] Confirm the preserved source of truth with #55.

### Task 2: Capture the current-order baseline

**Expected outputs:**

- Add: representative structural input fixtures under
  `server/tests/fixtures/structural/fbd/`
- Add: a changed-detail coverage checklist linked from the workbench design

- [ ] Preserve the intended current FBD source state via #55 before modifying
  or migrating it.
- [ ] Capture the relevant job/site/wind inputs without committing secrets,
  personal data, or local-only generated artifacts.
- [ ] Capture trusted node/member/load/result/report outputs with the exact
  legacy package and runtime versions.
- [ ] Map internal cladding, C100 battens, wall changes, window, doors,
  headers/jambs, bracing interruptions, and selected connections to expected
  structural effects.
- [ ] Establish independent equilibrium, member, and opening/load-path hand
  checks for the imminent order.

### Task 3: Clear the legacy solver compatibility hurdle

**Expected outputs:**

- Add: solver decision record under `docs/structural/`
- Add: a minimal compatibility runner and deterministic portal-frame fixture
- Modify: API compile image dependencies only after the spike identifies them

- [x] Identify the exact structural package, version, Python constraint, native
  dependencies, licence, units, axes, releases, and result conventions.
- [x] Run a minimal PyNiteFEA 2.4.1 cantilever in the legacy Python 3.12
  environment and confirm force/moment reactions.
- [x] Prove install/import in the canonical Tertius API/compile image via the
  branch smoke image workflow and isolated Helm deployment.
- [ ] Run a minimal stable frame and export nodes, member forces, reactions, and
  diagram stations deterministically.
- [x] Compare equilibrium and one independently calculated case.
- [ ] Decide with evidence whether to adapt, isolate, patch, or replace the
  package.
- [ ] Consider all new runtime configuration across Helm, Compose dev, Compose
  parity, and `scripts/check-runtime-parity.sh`.

### Task 4: Define and validate the structural artifact contract

**Expected outputs:**

- Add: versioned structural model/result schemas and validation
- Add: project authoring helpers usable from `design.py` imports
- Add: focused schema, graph, units, and stability tests

- [x] Define the first stable IDs shared by Build123D fixture components,
  structural entities, viewer selection, and fixture results.
- [ ] Extend stable IDs across source, procurement, and reports.
- [ ] Define nodes, members, sections, materials, supports, releases, offsets,
  rigid links, loads, cases, combinations, results, warnings, and provenance.
- [ ] Define analytical centre-line to physical geometry mapping.
- [ ] Reject duplicate/dangling/ambiguous nodes, zero-length/disconnected
  members, missing references, unsupported degrees of freedom, and stale
  results.
- [ ] Add source/model/result hashes and deterministic serialization.
- [ ] Keep the solver's private types behind the adapter.
- [x] Add a strict version `1.0` fixture contract and keep PyNite private types
  behind the fixture adapter.
- [x] Reject duplicate IDs, dangling references, and zero-length fixture
  members at the contract boundary.
- [x] Add structural-aware Build123D authoring helpers that generate the
  `TERTIUS_STRUCTURAL` manifest from registered object handles.
- [x] Resolve normalized section/material data from versioned project catalogue
  imports during the same sandbox execution that generates the model.
- [x] Persist a source-hashed structural sidecar beside the compiled model and
  reject it when the active project source closure has changed.
- [x] Fail capture for unregistered assembly handles, omitted or unconnected
  components, unused connectors, and duplicate viewer identities.
- [x] Fail compilation when generated projects export raw shapes outside the
  registered structural assembly or declared viewer nodes do not match the
  Build123D tree.

### Task 5: Deliver the first Structural Design Workbench slice

**Expected outputs:**

- Add: structural model/result API endpoints and artifact retrieval
- Add: workbench route/shell and focused UI tests
- Reuse: shared Extus viewer/tree selection primitives from #57

- [x] Add the authenticated workbench shell alongside the Procurement
  Workbench, with an explicit fixture/not-for-ordering banner.
- [x] Mount a structural API and expose a deterministic PyNite cantilever plus
  a Build123D GLB carrying the same member/node IDs.
- [x] Reuse the Extus viewer, link tree selection to GLB entities, and show the
  fixture load, reactions, equilibrium, moment, shear, displacement,
  utilisation, solver version, and capability states.
- [x] Replace the static fixture with an active-project `design.py` load-path
  capture while keeping unsolved capacities visibly not checked.
- [x] Render the `structural_test` concrete/100GPB/C10019/Custom Orb microcosm
  with stable component and fastener IDs in the active Extus model.
- [x] Migrate `structural_test` from a handwritten structural dictionary to
  handle-authored components, connections, loads, assembly, and generated
  manifest output.
- [ ] Add nodes, supports, local axes, releases, loads, tributary regions, and
  connectivity inspection.
- [ ] Link tree/viewer selection to member inputs, results, warnings, and report
  evidence.
- [ ] Add reaction, axial, shear, moment, torsion, deflection, capacity, and
  utilisation overlays with explicit units/signs.
- [ ] Use distinct blocking states for overloaded, unstable, disconnected,
  unmapped, stale, unsupported, and not checked.
- [ ] Preserve guest/auth isolation, tenant boundaries, and bounded telemetry.

### Task 6: Migrate and reconcile the current shed

- [ ] Port site capture and job-specific wind inputs with provenance.
- [ ] Rebuild current member/opening/cladding geometry in Build123D.
- [ ] Author the full analytical connectivity, supports, releases, offsets,
  gravity/wind loads, and combinations.
- [ ] Reconcile internal cladding, C100 battens, wall layouts, window, doors,
  headers/jambs, bracing, and connections against the baseline.
- [ ] Reproduce calculation sheets with inputs, assumptions, formulas,
  governing combinations, warnings, and hashes.
- [ ] Resolve every baseline difference explicitly; do not tune the new model
  silently to match a legacy number.

### Task 7: Validate the ordering gate and production path

- [ ] Pass source-to-report coverage for every changed design detail.
- [ ] Pass global force/moment equilibrium, reaction, stability, units, signs,
  and independent comparison checks.
- [x] Run backend, frontend, schema, and golden fixture tests.
- [ ] Run full authenticated `live-flow` in an isolated local-values k3s smoke
  release because this work changes model-viewer behaviour.
- [ ] Verify Helm/Compose parity for every runtime dependency/configuration
  change.
- [ ] Confirm reports distinguish pass, fail, warning, unsupported, stale, and
  not checked, and do not imply engineering certification.

Validation evidence (2026-07-27): `smoke-8057697` ran in the isolated
`tertius-fbd-smoke` Helm release. Authenticated browser validation confirmed the
fixture contract, PyNite results, Build123D GLB, and named load/member selection
without console errors. Full `live-flow` remains pending because this
fixture-only release intentionally has Pi/LLM workers and provider credentials
disabled.

Runtime recovery evidence (2026-07-27): the isolated release was upgraded to
the branch image `smoke-6526603` with KEDA compile Jobs enabled. Internal
Keycloak refresh now uses the cluster-local JWKS-derived endpoint, and compile
execution runs off the async NATS event loop so long Build123D work does not
starve JetStream keepalives. An authenticated upload of the current ten-file
`shed` project compiled as GLB/`sketch` in 89 seconds and rendered in Extus as
artifact `aa3e6199-3d7f-4571-b41e-cbebeda91b0a`; the Structural fixture also
remained healthy after the rollout.

The same source completed full-quality CAD generation, but its serialized model
result was about 62.6 MB. That is not a safe inline NATS/JetStream artifact
payload, so the isolated release retains a 32 MiB application guardrail and
uses `sketch` as the interactive default. Moving large model bytes through a
separate constrained blob/object-storage path, with NATS carrying only bounded
metadata, is now a required hardening slice before full-quality output from this
design can be returned reliably.

Active-project capture evidence (2026-07-27): the local `structural_test`
project reuses the shed's unchanged Lysaght, Custom Orb, flange-fastener, and
Buildex modules. Its `design.py` produces a 436,772-byte sketch GLB containing
stable names for the concrete block, four anchors, 100GPB, two web bolts,
C10019, Custom Orb sheet, and three Tek screws. Static capture detects seven
components, three directed connections, and an illustrative 0.8 kPa wind load
over 0.9144 square metres (0.73152 kN resultant), then traces the load from the
sheet through the C100 and 100GPB to the grounded concrete block. No strength or
serviceability result is implied.

Single-source authoring evidence (2026-07-27): `structural_test` now registers
the actual Build123D sheet, fastener, C100, 100GPB, anchor, and concrete handles
with `StructuralModel`, connects those handles directly, and generates
`TERTIUS_STRUCTURAL` via `structure.manifest()`. Focused tests prove static
capture rejects unknown assembly handles and registered-but-unconnected
members. Runtime tests reject raw Build123D shapes in the structural assembly,
and compile-sandbox tests reject extra raw global shapes plus unregistered
shapes exposed inside design containers outside the marked assembly. The
migrated model compiles successfully and every declared viewer identity occurs
in the GLB.

Active-project PyNite evidence (2026-07-27): `structural_test` now authors the
C10019 analytical centreline, gross elastic section properties, G450 elastic
material, idealised fixed-base restraints, and three screw locations through
the same handle API as its Build123D assembly. The 0.8 kPa surface pressure over
0.9144 square metres is distributed from the registered surface-load handle,
so its three PyNite member loads sum to 0.73152 kN without a second hand-entered
resultant. PyNite returns 0.73152 kN base shear, 0.585216 kN.m base moment, and
2.612313 mm free-end displacement. Independent force and moment sums give the
same reactions with zero reported global residual. The workbench publishes the
solver stations as a signed moment ribbon and demand colour ramp while keeping
C100 capacity, local buckling, restraint adequacy, screws, bolts, GPB, anchors,
and concrete explicitly `NOT CHECKED`.

Catalogue resolution evidence (2026-07-28): `structural_test/design.py` now
selects `C10019` through `lysaght_zc_structural_section(...)` and contains no
copied `A`, `Iy`, `Iz`, or `J` literals. The compile sandbox executes the full
project import closure, emits a deterministic structural sidecar, and records
catalogue ID/version/key, the selected row hash, axis mapping, complete source
properties, and SI-normalized PyNite properties. A sketch compile produced the
same 436,772-byte GLB and resolved `A=0.000409 m2`, `Iy=1.42e-7 m4`,
`Iz=6.73e-7 m4`, `J=4.92e-10 m4`, `fy=450 MPa`, and `Zxe=12300 mm3` from
`lysaght-zc-v2@2.0`. The API consumes the sidecar only when its full source
bundle hash matches the active project; the workbench exposes the catalogue
identity and record hash without reading manufacturer-specific files.
