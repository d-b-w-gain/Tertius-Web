# Architectural Drawing Sets Epic Implementation Plan

> **For agentic workers:** Keep these checkboxes current as work lands. Split
> implementation into focused slices; this kickoff PR defines the epic boundary
> and lands layout/export parity plus safe review defaults, but does not
> authorise one monolithic rewrite.

**Goal:** Evolve Timus from a fixed four-view part-sheet exporter into a
revision-safe architectural documentation workbench that generates a
coordinated concept-review drawing set from the canonical Tertius project.

**Architecture:** Persist a versioned drawing-definition artifact linked to the
same compile revision as physical geometry and semantic manifests. Generate
true plan/elevation/section/detail linework, associative annotations, schedules,
and multi-sheet vector outputs without independently executing project source.

**Primary design:** `docs/drafting/architectural-drawing-sets.md`

**First acceptance project:** Current Class 10a shed.

---

### Task 1: Freeze the existing Timus boundary and export defects

**Files:**

- Modify: `server/tests/test_timus_drafting_e2e.py`
- Modify: `ui/src/workflows/timus/ui/DraftingTab*.test.tsx`
- Add: focused PDF/preview parity fixtures

- [x] Capture the current top/front/side/isometric hidden-line output as a
  deterministic compatibility fixture.
- [x] Add a regression test proving that the selected preview layout is sent to
  and honoured by the PDF endpoint.
- [ ] Add failing tests for the fixed drawing number, sheet count, revision,
  `NTS`, applicant, checker, and approval-like defaults.
- [ ] Record current unit, axis, scale, camera, clipping, and tessellation
  conventions.
- [ ] Inventory every Timus path that executes or recompiles project source.
- [ ] Preserve authenticated/guest, tenant, project, and stale-artifact
  isolation coverage.

### Task 2: Define the versioned drawing contract

**Expected outputs:**

- Add: `server/core/drafting/` contract, validation, and serialization
- Add: JSON schema/fixtures under `server/tests/fixtures/drafting/`
- Add: contract and deterministic-hash tests

- [ ] Define drawing-set, sheet, viewport, view-definition, schedule,
  annotation, style, issue, source, and override records.
- [ ] Define stable IDs shared with physical geometry, semantic authoring,
  procurement, structural entities, site placement, and viewer selection.
- [ ] Define millimetre model/paper units, coordinate frames, project north,
  datums, levels, scale, cut planes, view ranges, crops, and visibility rules.
- [ ] Define associative references and explicit orphaned/stale/unsupported
  states.
- [ ] Reject duplicate/dangling IDs, invalid scales/crops, unknown categories,
  inconsistent sheet numbering, and unsupported view definitions.
- [ ] Add contract and renderer versions plus deterministic source/input/output
  digests.

### Task 3: Consume one canonical compile revision

**Files:**

- Modify: compile artifact bundle and Timus build orchestration
- Modify: `server/workflows/timus/timus_server.py`
- Add: current/stale drawing-input integration tests

- [ ] Publish drawing inputs from the same successful compile session as the
  current model and semantic sidecars.
- [ ] Remove Timus source execution and separate view compiles.
- [ ] Persist the drawing input and generated vector artifacts with tenant,
  project, compile-job, source-closure, and renderer provenance.
- [ ] Mark drawing data stale when any required model, semantic, site, or saved
  drawing-definition input changes.
- [ ] Block current-issue download for missing, stale, partial, or mismatched
  inputs with actionable user messages.
- [ ] Retain bounded telemetry without drawing content, addresses, coordinates,
  raw IDs, prompts, or source.

### Task 4: Replace project-wide settings with sheet management

**Expected outputs:**

- Add: drawing-set tree and sheet CRUD APIs
- Add: sheet manager UI and focused tests
- Migrate: current Timus settings into one legacy-compatible initial sheet

- [ ] Create, duplicate, reorder, rename, and remove sheets with stable drawing
  numbers and sheet indexes.
- [ ] Store size, orientation, discipline, revision, status, dates,
  author/checker fields, project data, notes, and revision history.
- [ ] Replace fixed title-block values with validated project/sheet metadata.
- [x] Default new sets to `PRELIMINARY - NOT FOR CONSTRUCTION`; do not default
  to `APPROVED`, a named checker, or certification language.
- [ ] Support drawing-register generation and accurate `sheet N of M` values.
- [ ] Version title-block and drawing templates independently of project data.

### Task 5: Unify preview and vector export around viewport definitions

**Files:**

- Modify: `ui/src/workflows/timus/ui/DraftingTab.tsx`
- Replace: fixed PDF layout in `server/workflows/timus/timus_server.py`
- Add: shared renderer fixtures and parity tests

- [ ] Save viewport type, position, crop, orientation, scale, visibility, and
  display style in the drawing contract.
- [ ] Make browser preview and PDF consume the same drawing definition.
- [x] Honour individual/combined layout selections in both preview and export.
- [ ] Support multiple independently scaled viewports per sheet.
- [ ] Verify printed scale numerically in generated PDF coordinates and display
  the actual scale in each viewport/title block.
- [ ] Preserve distinct visible, hidden, cut, projected, overhead/beyond,
  hatch, annotation, and redline line classes.

### Task 6: Add architectural semantic authoring

**Expected outputs:**

- Add: versioned architectural semantic manifest and authoring helpers
- Add: representative shed semantic fixtures and validation tests

- [ ] Define project north, datum, levels, finished floor/ground levels, and
  site placement.
- [ ] Define slabs, envelope/walls/cladding, roofs, ceilings, framing,
  foundations, doors, windows, openings, gutters, downpipes, flashings, spaces,
  materials, and finishes.
- [ ] Associate semantic objects with stable Build123D handles and viewer IDs.
- [ ] Generate door/window/material schedule inputs from semantics rather than
  object-name heuristics.
- [ ] Fail visibly when required meaning is absent; do not guess architectural
  categories from anonymous solids.
- [ ] Add source-to-model-to-drawing coverage diagnostics.

### Task 7: Implement plans, roof plans, elevations, and sections

**Expected outputs:**

- Add: reusable cut/projection engine operating on canonical compiled geometry
- Add: view-specific linework tests for the shed fixture

- [ ] Implement true horizontal floor-plan cuts with configurable level, cut
  height, view range, and overhead/beyond representation.
- [ ] Implement filtered roof plans with ridges, edges, overhangs, slopes,
  gutters, downpipes, penetrations, and drainage directions.
- [ ] Implement named elevations relative to project north/building faces with
  ground and level lines.
- [ ] Implement author-positioned vertical sections with bounded depth, cut
  fills/hatches, and projected context.
- [ ] Implement details derived from parent views or explicit model-space crops.
- [ ] Maintain stable object/edge references where the modelling kernel permits
  them and report lost associations explicitly where it does not.

### Task 8: Add associative annotations and cross-references

**Expected outputs:**

- Add: annotation engine, symbols, styles, and author overrides
- Add: collision/legibility and orphan-reference tests

- [ ] Add linear, angular, radial, ordinate, level, overall, opening, and grid
  dimensions derived from referenced model objects.
- [ ] Add grids, levels, north point, scale bars, section/elevation/detail
  symbols, opening tags, space labels, material callouts, leaders, and notes.
- [ ] Add model-linked section/detail/elevation cross-references.
- [ ] Keep calculated content separate from bounded author placement/text
  overrides and show when an override masks changed model data.
- [ ] Detect annotation clipping, overlap, unreadable sizes, duplicate tags, and
  orphaned references before issue.

### Task 9: Generate schedules and the shed drawing-set template

**Expected outputs:**

- Add: deterministic schedule generator
- Add: `Class 10a shed concept review` drawing-set template

- [ ] Generate drawing, revision, door, window, material, and finish schedules
  with stable object links.
- [ ] Create A000, A100, A101, A102, A200-A203, A300, A301, and A600 sheets as
  defined in the primary design.
- [ ] Populate the site plan from saved parcel/placement/orientation evidence
  while distinguishing public/GIS context from survey information.
- [ ] Surface geometry/height/placement-coordinate mismatches and missing site
  levels before issue.
- [ ] Populate A000 unresolved items from structural/site/drawing capability
  states, including middle-portal serviceability, provisional bracing,
  unresolved connections/foundations, and missing evidence.
- [ ] Link sheet and viewport selection to the corresponding model objects and
  workbench evidence.

### Task 10: Multi-sheet output and downstream exchange

**Expected outputs:**

- Replace: single-sheet `drafting.pdf` generation with drawing-set output
- Add: render-and-inspect QA plus a documented interchange decision

- [ ] Generate a deterministic multi-page vector PDF with bookmarks, correct
  page sizes/orientations, fonts, title blocks, revisions, and sheet order.
- [ ] Render every generated page and reject clipping, overlaps, missing glyphs,
  broken hatches, unreadable annotations, and blank viewports.
- [ ] Decide and document the first downstream exchange format (DXF, SVG, or
  another bounded vector contract) without weakening the canonical model.
- [ ] Preserve issue packages by revision and make superseded/current state
  explicit.
- [ ] Prevent draft, stale, unsupported, or incomplete output from appearing
  certified or construction-ready.

### Task 11: Validate the first architect-review set

- [ ] Generate the complete shed set from one current project revision.
- [ ] Verify floor/roof geometry, four elevations, both sections, schedules,
  dimensions, levels, view titles, scales, sheet numbers, and cross-references.
- [ ] Reconcile wind/site footprint, reference height, placement coordinates,
  structural frame extents, roof overhangs, and model axes explicitly.
- [ ] Change representative model inputs and prove associated views,
  dimensions, tags, schedules, and unresolved-item states update together.
- [ ] Prove a stale or incomplete input blocks a misleading issue package.
- [ ] Run backend, frontend, schema, deterministic fixture, PDF render, auth,
  tenant-isolation, and accessibility tests.
- [ ] Run authenticated browser validation against the canonical local k3s
  runtime; run full `live-flow` if implementation touches Generate Design, AI
  edit, its conversation history, or AI-edit-linked viewer behaviour.
- [ ] Record any intentional Helm/Compose differences and pass runtime parity
  checks for new services, dependencies, or environment variables.

### Epic completion gate

- [ ] An architect can review the generated shed set for siting, dimensions,
  openings, roof form, appearance, drainage intent, and coordination without
  interpreting raw model views or procurement data.
- [ ] Every sheet and annotation identifies its project revision, evidence
  status, scale, issue status, and unresolved dependencies.
- [ ] Preview, saved definition, vector output, schedules, and source model are
  demonstrably coordinated.
- [ ] Timus remains a model-linked documentation workbench rather than a second,
  divergent source of design geometry.
