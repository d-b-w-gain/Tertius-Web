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
- [ ] Prove install/import in the canonical Tertius compile image.
- [ ] Run a minimal stable frame and export nodes, member forces, reactions, and
  diagram stations deterministically.
- [ ] Compare equilibrium and one independently calculated case.
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
- [ ] Replace the static fixture with a project `design.py` capture.
- [ ] Render actual Build123D profiles and placements with stable component IDs.
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
- [ ] Run backend, frontend, schema, and golden fixture tests.
- [ ] Run full authenticated `live-flow` in an isolated local-values k3s smoke
  release because this work changes model-viewer behaviour.
- [ ] Verify Helm/Compose parity for every runtime dependency/configuration
  change.
- [ ] Confirm reports distinguish pass, fail, warning, unsupported, stale, and
  not checked, and do not imply engineering certification.
