# Mechanical Authoring And Structural Workbench Realignment Plan

**Goal:** Make `design.py` describe only the physical/mechanical design while
Tertius owns component registration, data reconciliation, structural topology,
BoM projection, rendering metadata, drawing inputs, validation, finalisation,
and artifact persistence.

**Architecture:** Execute one `design.py` once inside one Tertius compile
session. Workbench-enabled product factories create Build123D geometry and
register immutable product facts, component-instance facts, and physical
connection ports with that session. After execution, the Tertius runner requires
one `model` root, reconciles the registered components against that root, builds
one canonical compiled-design graph, and projects every workbench artifact from
that graph. No manifest or registry object is authored or passed through
`design.py`.

**Cutover policy:** This is a clean break. Do not read, translate, prefer, or
fall back to `TERTIUS_STRUCTURAL`, explicit project-authored BoM manifests,
shape-label inference, or parallel structural/BoM definitions. Migrate the
worked example as part of the cutover, reject the removed contracts with clear
errors, and regenerate old artifacts with the new runner.

**Document type:** Implementation plan

**Plan date:** 2026-08-13

## 1. Executive Decision

Tertius will own the design-data lifecycle. A mechanical design selects and
connects real components; it does not assemble workbench datasets.

The target `design.py` has this shape:

```python
import build123d as bd

from lysaght_zc import cee_member, bolted_knee

column = cee_member(
    "C10019",
    start_mm=(-2500, 0, 0),
    end_mm=(-2500, 0, 2400),
    mark="C1",
)

rafter = cee_member(
    "C10019",
    start_mm=(-2500, 0, 2400),
    end_mm=(0, 0, 3000),
    mark="R1",
)

knee = bolted_knee(
    column.ports.end,
    rafter.ports.start,
    bracket="KB01",
    fastener="M12-8.8-40",
)

model = bd.Compound(children=[column, rafter, knee])
```

It does not contain:

```python
structure = StructuralModel(...)
TERTIUS_STRUCTURAL = ...
bom_component(...)
requirement(...)
register_component(...)
```

The facts in the first example are mechanical facts: selected products,
physical endpoints, orientation, fabrication marks, and the real connection.
The calls in the second example are Tertius data plumbing and are removed from
the authoring surface.

## 2. Current-State Findings

The current repository and worked example have the right pieces, but they are
joined at the wrong boundary.

| Current behavior | Problem created | Target behavior |
|---|---|---|
| `server/core/compile_sandbox.py` executes `design.py`, then scans every value in the execution environment for shapes. | Helper variables and the final assembly can both be exported; `model` is not the authoritative root. | Require exactly one `model` root and ignore unrelated globals. |
| The sandbox injects `tertius_bom.py` into the project directory. | A project can shadow or replace Tertius data-management behavior. | Ship one installed, reserved Tertius runtime package in the compile image. |
| GLTF labels and `tertius_bom` attributes are patched after export. | Label/path heuristics become identity authority. | Export stable component and product identities directly from the compiled-design graph. |
| Procurement is reconstructed in Extus from Python AST, GLTF hierarchy, and an optional explicit manifest. | The workbench is reverse-engineering the design after execution. | Compile an authoritative procurement projection beside the rendering artifact. |
| `CompileResultPayload` carries one artifact. | Model, BoM, structural, drawing, and graph outputs cannot be committed as one revision. | Return and persist an atomic compile bundle containing all projections. |
| Timus can execute the same source again for bounds and drawing views. | Different workbenches can observe different executions of mutable Python. | Derive bounds and drawing inputs from the same compile session and revision. |
| The worked example exports `TERTIUS_STRUCTURAL = structure.manifest()`. | The mechanical design owns a Tertius serialization lifecycle. | Tertius finalises structural data after `design.py` returns. |
| `lysaght_zc_purlin()` accepts catalogue-profile overrides while structural properties are read from the named catalogue row. | Rendered, ordered, and analysed sections can silently differ. | Catalogue products are immutable; custom products require a new identity and complete facets. |
| Some example members use the combined `lysaght_zc_member()` path while portal members still create CAD and analytical geometry separately. | Instance part number, length, axis, rotation, and connections can drift. | One product-factory call creates one registered component instance and all derived projections. |

## 3. Ownership Model

| Concern | Owner | Authoring location |
|---|---|---|
| Product identity, catalogue revision, material, section geometry, mass, structural properties, allowed fabrication operations, connection ports | Product library | Reusable import such as `lysaght_zc.py` plus catalogue data |
| Selected product, physical placement, cut/end treatment, role/mark, and selected real connection | Mechanical design | `design.py` |
| Stable runtime IDs, graph reconciliation, geometry-to-component mapping, digests, schemas, diagnostics, and artifact lifecycle | Tertius core | Compile runtime |
| Site, wind region, importance level, applicable standards, load cases, serviceability criteria, and approval policy | Structural workbench | Tertius-managed project workbench configuration, not `design.py` |
| Supplier mappings, stock lengths, packs, pricing, substitutions, and order approval | Procurement workbench | Tertius-managed procurement state |
| Solver idealisation, load distribution, checks, combinations, and evidence status | Structural engine | Projection from the compiled graph plus structural workbench configuration |
| GLB nodes, selection IDs, colours, exploded views, dimensions, marks, and callouts | Rendering/drawing engines | Projection from the compiled graph |

Mechanical marks such as `C1` and `R1` remain valid authoring inputs because
they are real fabrication and drawing concepts. Internal database IDs, schema
versions, workbench registrations, and manifest assembly are not.

## 4. Canonical Compiled-Design Graph

Tertius will build one in-memory graph during finalisation and emit it as
`compiled_design.json`. This graph is the authority for a compile revision.
BoM, structural, rendering, and drawing artifacts are generated projections,
not independently authored datasets.

### 4.1 Product definitions

A workbench-enabled product library registers an immutable `ProductDefinition`
when a product is resolved. It contains:

- canonical product key, manufacturer part number, catalogue ID/revision, and
  a digest of the exact catalogue row;
- material and finish choices allowed by the product;
- parametric profile/solid construction inputs;
- procurement identity and ordering rules;
- structural material, gross/effective section properties, axis mapping,
  capacities, and evidence status;
- named physical connection ports and compatibility rules;
- drawing names, nominal dimensions, and section symbols.

The definition is authored once in the reusable product library. Each facet
references the same product key and catalogue-row digest.

Catalogue-backed profile dimensions are immutable. A call cannot override
`thickness`, `depth`, `flange`, or `lip` while retaining the catalogue product
identity. A custom section must have a distinct product key and must provide
complete procurement and structural facts before it can be considered ready.

### 4.2 Component instances

Each product-factory call creates one `ComponentInstance` containing values
that vary per installed part:

- runtime instance token;
- optional mechanical mark/role;
- product-definition reference;
- placement and local frame;
- cut/fabricated length and ordered length;
- end treatments, holes, and other fabrication operations;
- the Build123D shape reference;
- named connection-port instances;
- source provenance for navigation, never for identity inference.

The factory returns a Build123D-compatible shape carrying an opaque Tertius
runtime token and typed `ports`. It can be used directly in a Build123D
compound. The mechanical designer does not call a registry.

Runtime tokens use object identity during execution. On finalisation, Tertius
assigns persistent component IDs from an explicit mechanical mark when present,
or from deterministic source/assembly identity when no mark is required. Graph
relationships use object/port handles, so a design does not pass string IDs to
connect parts.

### 4.3 Connections

A physical connection is a first-class graph object, not merely two analytical
nodes with equal coordinates. It contains:

- the connected member-port handles;
- rendered brackets, plates, bolts, nuts, washers, welds, or screws;
- hole and bolt-line geometry derived from the same detail;
- procurement identities for every physical connection item;
- supported action transfers: axial force, shear, moment, wind-normal action;
- analytical idealisation: pinned, rigid-zone, semi-rigid, spring, or another
  validated connection model;
- capacity/evidence status and applicability limits.

Touching or intersecting shapes do not create a connection. A connection
factory must consume compatible ports. This makes the physical connection the
source for both assembly instructions and analytical topology.

### 4.4 Projections and cross-links

Every emitted projection includes:

- compile revision ID and compiled-design digest;
- component instance ID;
- product key and product-definition digest;
- physical connection ID where applicable;
- projection-specific facts and diagnostics.

Values may be copied into a workbench artifact for performance, but no
workbench accepts independently supplied product identity, length, section, or
placement. Digest and reference checks make projection drift a compile error.

## 5. Automatic Compile Lifecycle

```text
Tertius compile worker
  1. Validate source bundle and reserve the `tertius` package namespace
  2. Load Tertius-managed workbench configuration
  3. Start one CompileSession
  4. Execute design.py once
  5. Require env["model"] as the sole Build123D root
  6. Reconcile registered component shapes against the model tree
  7. Resolve physical connection graph and structural nodes
  8. Validate product, fabrication, topology, and workbench completeness
  9. Build the canonical compiled-design graph
 10. Project render, procurement, structural, bounds, and drawing artifacts
 11. Return one atomic artifact bundle
 12. Persist the entire bundle or none of it
```

The runtime session is implicit because a compile worker runs one design in an
isolated process. Implement it with a scoped runtime context, not a design
object passed to component functions. The runner starts and closes the context;
product libraries access the current session internally.

The package cannot finalise itself safely from a member call because it does
not yet know the final model root or whether execution will fail. The compile
runner owns the definite end-of-execution hook and calls finalisation there.

For local development, provide the same lifecycle through a Tertius CLI. A
managed component factory invoked without an active Tertius compile session
must fail clearly instead of silently producing geometry without workbench
registration.

## 6. Structural Topology From Physical Connections

Structural nodes are generated after all members and connections exist.

For each connected port pair, the connection definition determines whether the
solver receives:

- one shared node with member-end releases;
- one shared rigid node;
- distinct member-end nodes joined through rigid offsets;
- distinct nodes joined through translational/rotational springs;
- a validated connection submodel.

Bolt locations and bracket geometry can determine physical engagement points
and rigid-zone extents. They do not, by themselves, prove moment capacity or
stiffness. Those behaviors must come from a validated connection definition or
remain explicitly unverified in the structural workbench.

The structural engine will separate three inputs:

1. **Mechanical topology:** members, surfaces, supports, and real connections
   from the compiled-design graph.
2. **Product engineering facts:** material, section, capacity, connection
   behavior, and evidence from product definitions.
3. **Project analysis context:** site, regulations, load cases, combinations,
   serviceability criteria, and approval policy managed by Tertius.

No project analysis context is stored in `design.py`. Missing required context
does not trigger guessed final-design values; it produces a configuration-
required status and prevents structural approval.

## 7. Readiness And Failure Semantics

Tertius must distinguish a useful draft from an approved output without
pretending that incomplete data is verified.

| Gate | Required conditions | Effect when false |
|---|---|---|
| `mechanical_graph_valid` | One model root; all managed instances appear exactly once; no duplicate marks; fabrication dimensions valid; connection ports compatible | Compile bundle rejected because all projections would be unreliable |
| `procurement_complete` | Every orderable visible component has product identity, quantity/unit, fabrication dimensions, and ordering rule | Draft BoM may be shown, but ordering/export is blocked |
| `structural_model_complete` | Structural members have section/material facets, required connections/supports, and resolvable topology | Structural solve is blocked with component-level diagnostics |
| `structural_verified` | Workbench context complete; load cases/check packs run; required evidence and utilization limits pass | Design cannot receive structural approval |
| `release_ready` | Project policy's required procurement, structural, drawing, and review gates pass for the same compile/config revision | Purchase-order and issued-for-construction outputs remain blocked |

Unmanaged raw Build123D geometry can render, but it is reported as unmanaged
and cannot silently contribute to BoM, structural, or drawing completeness.
Reference-only geometry must come from a workbench-enabled reference/site
factory whose non-orderable and non-structural role is defined by its product
library, not from a label heuristic.

## 8. Clean-Break Removal List

The following paths are removed rather than supported in parallel:

- `TERTIUS_STRUCTURAL` globals and any compiler lookup for them;
- project-local `tertius_structural.py` as an authoring/runtime manifest helper;
- explicit BoM registration in `design.py` through `bom_scope`,
  `bom_component`, and `requirement`;
- project-injected `tertius_bom.py` runtime helpers;
- optional explicit BoM manifests as procurement authority;
- AST resolution of part numbers, quantities, or dimensions as procurement
  authority;
- GLTF label/hierarchy inference as component or quantity authority;
- scanning all execution globals for shapes;
- independently re-executing `design.py` for model, bounds, drawings, or
  workbench artifacts;
- catalogue part-number calls that also accept section-dimension overrides;
- manual selection of a structural catalogue record after a component factory
  already selected the product.

Source provenance remains useful for navigation, diagnostics, and cache
invalidation. It is not a substitute for runtime component facts.

The compile runner will fail with a focused cutover diagnostic if removed
`TERTIUS_*` exports or reserved project-local `tertius` runtime modules are
present. There is no legacy reader, translation layer, precedence rule,
deprecation period, or dual artifact schema.

Tertius supports one current runtime/schema contract. Schema changes require a
coordinated product-library/runtime deployment and regeneration of derived
artifacts; `design.py` does not select or manage schema versions.

## 9. Relationship To Incremental Component Replay

This plan reuses the useful component-versus-instance and scene-manifest ideas
from the incremental component contract, but changes its authoring boundary:

- `register_component(...)` is not called from `design.py`;
- product factories register themselves with the active compile session;
- explicit product/runtime registration is authoritative;
- source/GLTF inference may help cache unmanaged render-only geometry, but it
  cannot create BoM or structural facts;
- an incremental path must still produce and validate the same complete
  compiled-design graph before a workbench artifact is considered current.

Do not productionize a competing design-level component registry before this
runtime contract is settled. There must be one component identity system shared
by incremental rendering and every engineering workbench.

## 10. File And Package Direction

### Create

| Path or package | Responsibility |
|---|---|
| `tertius/` | Installed, reserved public runtime SDK used by product libraries and the compile runner |
| `tertius/session.py` | Scoped `CompileSession`, current-session access, lifecycle, and bounded registration |
| `tertius/products.py` | Immutable product, facet, fabrication, and catalogue-reference contracts |
| `tertius/components.py` | Component instances, Build123D shape binding, marks, roles, and ports |
| `tertius/connections.py` | Physical connection instances, port compatibility, transfers, and analytical behavior contracts |
| `tertius/graph.py` | Canonical compiled-design graph, reconciliation, digests, and diagnostics |
| `tertius/runner.py` | One-shot design execution, `model` extraction, finalisation, and bundle writing |
| `server/core/compile_artifacts.py` | Strict multi-artifact result schemas, size/digest checks, required-kind validation |
| `server/core/structural/` | Structural projection, node generation, workbench input validation, solve/check orchestration |
| `server/core/workbench_config.py` | Tertius-managed, revisioned project workbench configuration contracts |
| focused unit/integration fixtures | Minimal Cee members, physical connections, raw unmanaged geometry, and failure cases |

The exact module split may change during implementation, but the installed
`tertius` namespace and its ownership must not be implemented as project files
injected at compile time.

### Modify

| Path group | Required change |
|---|---|
| `pyproject.toml`, `uv.lock`, `Dockerfile`, `Dockerfile.api` | Package and install the Tertius runtime/runner in every compile environment |
| `server/core/compile_sandbox.py` | Replace the embedded environment scanner with the installed runner; collect a bundle directory/result |
| `server/core/compile_runtime.py` | Reserve the Tertius namespace and hydrate workbench context separately from project source |
| `server/core/compile_messages.py` | Replace the single artifact fields with a bounded list of typed artifact payloads and bundle metadata |
| `server/workflows/intus/compile_job.py` | Execute one runner and publish the complete bundle |
| `server/workflows/intus/compile_result_consumer.py` | Validate and persist every artifact atomically; fail the job if any required artifact is invalid |
| `server/core/models.py`, migrations, repositories, artifact helpers | Support longer typed artifact kinds, uniqueness per compile/kind, bundle queries, and revision/digest metadata |
| `server/workflows/extus/extus_server.py` | Serve compile-produced procurement and structural projections directly; remove request-time AST/GLTF reconstruction |
| `server/workflows/timus/timus_server.py` | Consume bounds/drawing artifacts from the current bundle; remove direct source execution and separate view compiles |
| Octavus Procurement UI | Display authoritative projection/readiness, graph-linked selection, and release gates; remove metadata-recovery authoring flow |
| Structural UI/workflow | Manage project analysis context, run/display checks, and trace every result to graph/product/config digests |
| Viewer/drawing UI | Select by component instance ID and consume marks/ports/drawing data from the same graph |
| harness/runtime/deployment files | Carry the new runtime and bundle limits through Helm, Compose dev, Compose parity, and runtime parity validation |

### Delete after cutover

| Path or behavior | Reason |
|---|---|
| `server/core/tertius_bom_runtime.py` | Replaced by the installed product/runtime graph contract |
| Procurement AST/static identity resolution used for production rows | It reverse-engineers facts already supplied by product factories |
| GLTF component/quantity inference used for production rows | Render hierarchy is a projection, not procurement authority |
| BoM metadata recovery prompt/action in `BomReviewTab.tsx` | It edits data plumbing into mechanical source |
| `get_compound_from_code` and other direct Timus execution paths | All workbenches consume one compiled revision |
| External worked-example `tertius_structural.py` | Structural finalisation moves into Tertius; reusable product facts move into product libraries |

Static analysis code may remain only if a separately approved use case still
needs source diagnostics or cache planning. It must not emit orderable or
structurally verified facts.

## 11. Implementation Sequence

### Task 1: Lock the clean authoring and graph contracts

**Files:** new `tertius/` contract modules, schema documentation, focused unit
tests.

- [x] Write tests for product immutability, catalogue-row digests, component
  instance registration, port handles, and graph serialization.
- [x] Define the product, procurement, structural, drawing, component, port,
  connection, and fabrication contracts.
- [x] Define one current compiled-design schema and canonical digest rules.
- [x] Prove that projection code cannot accept an independently entered product
  number, length, or section record.
- [x] Document the mechanical-only `design.py` contract and reserved `tertius`
  namespace.

### Task 2: Add the scoped Tertius compile session

**Files:** `tertius/session.py`, `tertius/runner.py`, runtime tests,
`compile_sandbox.py`.

- [x] Start a fresh session before executing `design.py` and always close it in
  `finally`.
- [x] Require a `model` Build123D root; remove environment-wide shape
  collection.
- [x] Fail if managed component factories run without an active session.
- [x] Fail if removed `TERTIUS_*` globals or project-local reserved runtime
  modules exist.
- [x] Reconcile each registered managed shape exactly once against the final
  model tree and identify unmanaged geometry.
- [x] Provide `python -m tertius.runner <project>` for the same local lifecycle.

### Task 3: Prove automatic product registration with Lysaght Cee members

**Files:** worked-example `lysaght_zc.py`, catalogue fixture/library tests,
minimal Tertius integration fixture.

- [x] Refactor catalogue lookup into immutable `ProductDefinition` values.
- [x] Make one Cee/Zed member call generate geometry, procurement facts,
  structural section/material facts, ports, drawing facts, and one component
  instance registration.
- [ ] Remove catalogue-profile overrides from catalogue-product calls.
- [x] Make endpoints determine placement and physical length; treat ordered
  length and explicit fabrication cuts as separate validated facts.
- [x] Return a Build123D-compatible managed shape usable directly in the final
  compound.
- [x] Demonstrate that changing `C10019` to another product changes every
  projection digest from the same call.

The project-owned `lysaght_zc.py` library now provides this path for all 16 Cee
and 16 Zed rows in the worked catalogue. It imports only the installed generic
`tertius` SDK, and is compiled and revisioned with `design.py`. Manufacturer
catalogues and component factories are not built into the Tertius runtime. The
remaining unchecked item is the clean-break migration of the external worked
example away from its old override-capable local factory.

### Task 4: Make physical connections generate structural topology

**Files:** `tertius/connections.py`, `server/core/structural/topology.py`,
Lysaght bracket/fastener builders, tests.

- [x] Define typed member ports with position, frame, supported connection
  families, and engagement region.
- [x] Refactor bracket and fastener factories into physical connection
  definitions with procurement and structural facets.
- [x] Generate solver nodes and releases from the selected
  connection definition.
- [x] Reject incompatible ports, unexplained endpoint offsets, missing physical
  connectors, and unused connector geometry.
- [x] Ensure coincident/touching shapes remain structurally unconnected unless a
  connection object joins them.
- [x] Keep unvalidated stiffness/capacity visibly unverified rather than
  promoting it from bolt geometry alone.

### Task 5: Build and validate all workbench projections

**Files:** graph finaliser, procurement projection, structural projection,
drawing projection, renderer metadata adapter, tests.

- [x] Generate `compiled_design.json` first, then project every other artifact
  from that frozen graph.
- [x] Generate procurement rows from component/product/fabrication facts; keep
  supplier packaging and pricing in Procurement state.
- [ ] Generate structural members, sections, materials, surfaces, supports,
  connections, and readiness diagnostics from graph facets.
- [x] Generate GLB/scene nodes containing stable component and product
  references without label matching.
- [x] Generate bounds and drawing-input artifacts from the same model instance.
- [x] Validate matching graph/product/component digests across every
  projection.

### Task 6: Move structural design context into Tertius workbench state

**Files:** workbench configuration models/repository/API, structural engine,
Structural UI, tests.

- [x] Add revisioned structural configuration for site, standards, importance,
  semantic action cases, action-standard pack selection, serviceability, and
  approval rules.
- [x] Include the selected configuration revision/digest in the compile or
  analysis revision without exposing it as a Python project import.
- [x] Port reusable solver/check orchestration out of the worked-example helper
  into Tertius structural services.
- [x] Keep catalogue engineering properties in product definitions and project
  analysis inputs in workbench state.
- [x] Block structural approval when required context/evidence is missing.
- [ ] Trace every result and utilization back to component, connection,
  product, graph, and configuration digests.

Completed vertical slice: new projects now carry a revisioned Structural
workbench configuration for semantic actions, a standards-pack selection, self-weight,
serviceability criteria, and approval policy. The default project-owned
`structural_connections.py` import creates a rendered/procured fixed-base
assembly and explicit physical connection; the structural projection adapter
derives its member, support nodes, loads, reactions, and demand diagrams from
the compiled graph. The demo base stiffness and capacity remain deliberately
unverified, so regulatory approval and procurement release stay blocked until
verified connection and capacity packs are added.

Completed physical-topology slice: member ports now carry an explicit local
frame, supported connection families, and engagement length. A project-owned
bolted knee builder creates its gusset and bolts once for render, procurement,
drawing, and the physical connection graph. The solver receives stable node
keys from that graph: rigid connections share nodes, pinned connections create
rotational end releases, and unconnected coincident endpoints stay separate.
Port reuse, incompatible families, unexplained gaps, reused connectors, and
unused connector geometry are rejected before analysis. Rigid-zone offsets,
semi-rigid springs, and verified resistance remain later verification work.

Completed engineering-evidence slice: each managed member now carries the
exact immutable catalogue row and digest into the structural capture. Tertius
aligns the PyNite local frame with the rendered section frame, runs the pinned
AS/NZS 4600 section-resistance pack for the configured ULS combination, and
traces section demand, capacity, utilisation, pack version, product digest,
graph digest, and configuration digest in the calculation evidence. Member
stability is evaluated separately. The original slice left distortional and
unrestrained lateral-torsional resistance unsupported; the later project-basis
AS/NZS 4600:2005+A1 member-capacity slice below closes those two calculation
gaps without trusting project-authored verification flags. Physical knee and base
connections now declare versioned resistance-evidence slots and exact expected
connector identities; the workbench reports their ULS axial, shear, and moment
demands while refusing a pass when published bracket, bolt, anchor, or base
capacity evidence is absent. This gives fit, procurement identity, structural
demand, and evidence status one shared graph without inventing capacity for
the demo connection parts.

Completed Site/action integrity slice: the active Structural capture now reads
the saved `tertius_site.py` at analysis time, so Site pressure and standards can
change without recompiling Build123D geometry. A revisioned transverse
portal-frame strip action model selects compiled mechanical roles, reconciles
the Site footprint with frame spacing, derives +X/-X wind cases and SLS/ULS
combinations, distributes traceable line actions to every analytical segment,
and traces each action through physical connections to ground. Missing Site
data, incomplete roles, footprint mismatch, missing wind receivers, or a broken
load path blocks the Actions stage instead of allowing self-weight alone to
pass. The later surface-action-pack slice removes the three provisional
project coefficients: Tertius now derives low-rise rectangular enclosed gable
wall/roof coefficients, area factors, potential-opening internal-pressure
cases, net coefficients, and pressure directions from Site plus compiled
mechanical geometry. Unsupported roof pitch, enclosure type, or verified-only
opening policy fails closed.
Serviceability criteria are now evaluated once per physical member using its
full compiled span, while retaining the governing analytical segment for UI
selection and evidence tracing.

Completed action-standard ownership slice: Structural configuration schema 2.0
contains semantic action identities but no combination formulae. Directional
Site adapters may add semantic wind cases, but cannot create SLS/ULS factors or
choose a subset for capacity checks. The selected versioned Tertius action pack
generates the scoped AS/NZS 1170.0 envelope and automatically sends every ULS
combination to cross-section and member-stability verification. Its evidence
records the exact source digest, clauses, applicability, and exclusions. SLS
wind is now generated from a separate 1-in-25-year Site event while ULS wind
uses the project ultimate event; all-other-roofs imposed action uses the
verified 0.7 short-term factor. Schema 1.0 is rejected at runtime; a one-time
migration creates new v2 revisions while discarding project-authored formulae
and per-check combination selections.

Completed concentrated roof-action slice: Tertius locates the compiled
`roof/ceiling purlin` members and creates one alternative 1.4 kN midspan action
case for each physical member. The AS/NZS 1170.0 pack generates separate SLS
and ULS combinations using the concentrated-action factors, never combines two
receiver cases, and never combines a concentrated case with the distributed
R2 roof action. The live shed now contains 12 concentrated receiver cases, 24
corresponding combinations, 30 action cases and 49 total combinations. Its
complete solve confirms that SLS transverse wind, not a new concentrated case,
governs the remaining portal-rafter deflection failure.

Completed restraint-location ownership slice: Tertius now derives possible
rafter/purlin, rafter/roof-brace, column/longitudinal-track, and
column/wall-brace restraint locations from compiled physical joints and split
analytical axes. It records the exact primary, bracing, and connector product
identities plus an alternate physical topology path to ground. These records
remain deliberately candidate-only: no twist control, effective-flange
engagement, stiffness, anchorage resistance, or connection resistance is
credited until matching evidence is attached. The Structural UI shows located
but uncredited candidates instead of hiding them or treating geometry as proof.

Completed connection-demand coverage slice: every compiled physical joint now
receives an envelope of the ULS actions it declares that it transfers. Tertius
uses the compiled component-port identities to take demand from true member
ends at branch joints, preserves combined-node connection membership, and
records every rendered connector part number. Joints with no resistance pack
are reported as unsupported demand checks rather than disappearing; no
capacity, identity match, stiffness, or foundation resistance is inferred.

Completed cold-formed member-stability slice: Tertius now records the
full AS/NZS 4600:2005 incorporating Amendment No. 1 source digest and the
developments paper as the accepted project supplement. For prequalified simple
lipped C sections it calculates global flexural/flexural-torsional compression,
Appendix D2 distortional compression, full-length unbraced lateral-torsional
bending with `Cb=1`, and Appendix D3 distortional bending. Governing design
compression and bending resistances, modes, formula substitutions, source
hashes, and combined utilisation are emitted in Structural evidence. Candidate
cladding/bridging restraint is displayed but not credited, so `design.py` no
longer has to assert restraint or distortional verification.

Completed off-axis member-resistance slice: the same Tertius pack now derives
conservative effective minor-axis modulus, both-axis unbraced member bending,
both-axis shear, full St-Venant torsion, Clause 3.5.1 biaxial axial-bending
interaction with calculated Euler amplification, and Clause 3.3.5
bending-shear interaction. No warping or physical-restraint benefit is
credited. The selected-member UI explicitly switches between Stage 6 section
resistance and Stage 7 member stability so neither evidence set masks the
other. The live shed now evaluates all 183 selected sections and member
segments as pass/fail rather than unsupported; physical restraint-system and
connection resistance remain separate certification gates.

Completed Stage 8 tension-bracing slice: Tertius now envelopes every
tension-only brace across the generated ULS design combinations and calculates
AS/NZS 4600:2005+A1 gross-yield and net-fracture resistance from product
geometry and material facts. It checks the rendered two-end screw layout,
connected-part net tension, bearing, tear-out, and required tested screw shear,
then traverses the compiled physical connection graph independently from both
brace ends to grounded components. Project-authored strap or end-connection
capacity numbers are not consumed. Missing Section 8 screw test resistance
remains an explicit blocker rather than a hidden assumption.

Completed Stage 8 restraint-identity/demand slice: project-owned Lysaght
imports now preserve each PB1230HS M12 x 30 grade 8.8 bolt/nut/washer kit as
one orderable and structural connector identity. Tertius matches the compiled
C10019/C10012/100AC/(100CP)/PB1230HS configuration to an immutable evidence
pack and calculates the AS/NZS 4600:2005+A1 Clauses 4.3.2.2-4.3.2.3 restraint
demand as 2.5% of the maximum critical-flange force. Combination-expanded
checks remain in exported evidence, while the calculation sheet and Stage 8
summary envelope them by physical location. The live gate remains open until
the separately rendered shared support-side bolts are members of the same
physical connection and their resistance, stiffness, and anchored collector
path are verified; nearby geometry is not credited implicitly.

Completed Stage Focus visual-feedback slice: each verification-stage selection
now becomes explicit viewer state with its purpose, status, governing
combination, summary, metrics, and legend shown in the Extus HUD. Stage 8 uses
a dedicated focused overlay rather than the whole-building moment-ribbon set:
the governing compression-flange segment is highlighted, physical restraint
boundaries are clickable, cyan arrows scale with calculated AS/NZS restraint
demand, exact-product candidates are amber, and missing stiffness or anchorage
evidence is ringed red. The demand-arrow direction is labelled schematic while
its magnitude remains calculated evidence. The overlay contract is reusable by
the remaining stage-specific visual modes.

Completed persistent Structural analysis-result slice: the compiled design
digest, Structural configuration digest, canonical site definition, requested
combination, Structural snapshot schema, and deployed Tertius source revision
now form one content-addressed cache identity. Completed snapshots are stored
as PostgreSQL JSONB and protected by a transaction-scoped advisory lock, so
unchanged workbench visits reuse the same validated result and concurrent
requests cannot launch duplicate PyNite solves. The initial workbench request
returns capture and analysis together, while the UI distinguishes saved-result
loading from a first calculation and shows whether the result was reused or
calculated and saved.

### Task 7: Add atomic multi-artifact compile bundles

**Files:** compile messages, worker, result consumer, artifact models/migration,
repositories, tests.

- [x] Define a bounded `CompileArtifactPayload` with kind, content type,
  encoding/compression, original size, and SHA-256 digest.
- [ ] Define required artifacts for a managed GLB compile: compiled design,
  model/scene, procurement projection, structural projection, bounds, drawing
  input, and compile diagnostics.
- [x] Enforce per-artifact and total-result size limits before publish and after
  decode.
- [x] Persist all artifacts and finish the job in one database transaction.
- [x] Add uniqueness for one artifact kind per compile revision and expand the
  artifact-kind column for descriptive names.
- [x] Prune complete bundles, never individual artifacts that would leave a
  current revision inconsistent.
- [ ] Reconcile this bundle with the incremental scene/component artifact work
  so binary components can be referenced rather than duplicated in NATS.

### Task 8: Rewire workbench APIs and UIs to the bundle

**Files:** Extus, Timus, relevant React workbenches and tests.

- [ ] Serve procurement and structural artifacts only from the latest complete
  bundle/config revision.
- [x] Remove request-time procurement AST/GLTF reconstruction and its cache.
- [x] Remove the Procurement action that asks AI to add `tertius_bom` plumbing
  to `design.py`.
- [ ] Make viewer selection, BoM rows, structural results, and drawing callouts
  navigate through the same component instance IDs.
- [x] Surface readiness gates and exact component/connection diagnostics.
- [ ] Make Timus consume current bounds/drawing inputs instead of executing
  project Python again.
- [ ] Expose draft outputs while preventing order/approval/issued drawing
  actions when required gates fail.

### Task 9: Perform the flag-day example migration and delete legacy paths

**Files:** structural worked example, old runtime helpers/analyzers, docs and
tests referencing removed contracts.

- [ ] Rewrite the worked example so `design.py` contains only mechanical
  composition and `model`.
- [x] Replace every direct `lysaght_zc_purlin()`, separate structural geometry,
  and separate catalogue-selection sequence with the canonical factory.
- [x] Replace portal, purlin/girt, stud/track, and bracket/bolt connections with
  typed physical port connections.
- [ ] Delete the external project structural manifest helper after reusable
  engine behavior has moved into Tertius.
- [ ] Delete compiler and UI support for explicit structural/BoM manifests and
  recovery metadata.
- [ ] Delete production procurement identity inference and update documentation
  to describe compiled projections.
- [ ] Invalidate/regenerate old artifacts; do not migrate them through a legacy
  reader.

The external `3x5shed` design now exercises the complete structural skeleton:
95 installed Cee members, 147 physical joints, 171 derived analytical segments,
and no unconnected structural component. Portal, roof, wall, gable, opening, and
floor topology is compiled from final CAD placements and fabricated connection
workpoints. Raw nonstructural furnishings and finishes remain visible and keep
procurement release blocked until their legacy BoM decorators are migrated.

### Task 9.5: Cut over to Australian certification readiness

**Files:** structural contracts and analysis, site overlay, starter project
configuration, Structural workbench UI and tests.

- [x] Make NCC 2022 Amendment 2 and the selected AS/NZS editions the primary
  verification framework; retain SCI P399 only as named supplemental
  portal-frame guidance.
- [x] Replace P399-named stage/sheet API fields and artifact IDs with primary
  Australian references and generic Australian evidence identifiers.
- [x] Derive conservative project-basis, actions, analysis, stability, member,
  load-path, serviceability, and documentation certification gates from the
  live calculation evidence.
- [x] Permit an explicitly non-certifying engineering-review draft when the
  solver/equilibrium gate passes while blocking a certificate and ordering.
- [x] Surface the Australian gate status and supplemental P399 role in the
  Structural workbench and engineering-review JSON export.
- [x] Replace the working action combinations with a source-digested AS/NZS
  1170.0 roof/wind pack, separate SLS/ULS wind events, and explicit
  applicability/exclusions.
- [x] Verify and implement the scoped AS/NZS 1170.2 low-rise rectangular
  enclosed-gable surface-zone/internal-pressure coefficient envelope, with
  source hashes, compiled opening locations, explicit factors, and fail-closed
  applicability boundaries.
- [x] Add the separate concentrated roof-action check as mutually exclusive
  physical roof-member alternatives owned by the Tertius action pack.
- [x] Derive member-restraint candidate locations, product identities, and
  topology-to-ground traces from compiled mechanical joints without adding
  restraint bookkeeping to `design.py`.
- [x] Envelope declared ULS force/shear/moment transfers for every compiled
  physical joint even when resistance evidence is absent.
- [x] Add conservative full-length unbraced lateral-torsional and Appendix D
  distortional member resistance from the source-digested AS/NZS
  4600:2005+A1 Tertius pack; do not require project-authored verification flags.
- [x] Complete conservative off-axis member resistance for minor-axis bending,
  both shear axes, torsion, and biaxial member interaction.
- [x] Complete Tertius-owned tension-strap member resistance, rendered end-layout
  checks, and two-ended brace-to-ground load-path evidence.
- [ ] Attach tested screw resistance, complete general connection/base/foundation
  resistance, and close the remaining live-shed serviceability evidence.

Stage 9 connection evidence (2026-08-19): Tertius now promotes each exact
rendered tension-strap end from the existing AS/NZS 4600 screw qualification
into the general physical-connection demand/resistance register. Every Stage 8
brace and restraint route must pass every Stage 9 connection on its path to
ground; topology alone can no longer verify anchorage. General brackets, base
anchors, substrate interaction, and concrete/masonry resistance remain
fail-closed with the missing evidence named in the calculation sheet, so this
item intentionally remains open.

### Task 10: Validate the real workflow and release gates

**Files:** unit/integration/e2e tests, harness scripts, runtime parity docs,
observability.

- [x] Run focused Python and UI test suites from the test matrix below.
- [ ] Run Compose live-flow during development and canonical isolated local-k3s
  full live-flow before finalising.
- [ ] Compile the complete structural worked example and compare rendering,
  procurement, structural, and drawing component sets/digests.
- [ ] Exercise Structural and Procurement workbenches in the browser, including
  component cross-selection and failed readiness gates.
- [ ] Add bounded metrics for compile phase, registered/unmanaged counts,
  projection status, and readiness state without source or raw identifiers.
- [ ] Update runtime parity checks for any new settings, artifact limits, worker
  package, or workbench configuration transport.

## 12. Test Matrix

| ID | Scenario | Expected result |
|---|---|---|
| U-001 | Catalogue Cee product resolved | Geometry, procurement, structural, and drawing facets share one product digest |
| U-002 | Catalogue product receives depth/thickness override | Factory rejects it; no component is registered |
| U-003 | Custom section lacks structural properties | It may remain a draft managed component, but structural readiness is blocked |
| U-004 | Member endpoints change | CAD placement, cut length, structural axis, and drawing dimensions change together |
| U-005 | Ordered length is shorter than fabricated length | Mechanical graph finalisation fails |
| U-006 | Two members geometrically touch without a connection | They render; structural topology reports them unconnected |
| U-007 | Valid bracket/bolt connection joins member ports | Render, separate connection BoM rows, and analytical joint are emitted together |
| U-008 | Bracket holes do not align with member ports | Mechanical graph finalisation fails with a connection diagnostic |
| U-009 | Connection has no verified stiffness model | Structural artifact marks it unverified and approval remains blocked |
| U-010 | Managed component omitted from `model` | Compile fails reconciliation |
| U-011 | Same managed component appears twice in `model` | Compile fails reconciliation |
| U-012 | Raw Build123D reference appears in `model` | Model renders; unmanaged diagnostic blocks affected completeness gates |
| U-013 | `model` is missing | Compile fails with one focused authoring error |
| U-014 | `TERTIUS_STRUCTURAL` is exported | Compile fails with the clean-cutover diagnostic |
| U-015 | Project contains a reserved `tertius` runtime module | Source hydration rejects it |
| U-016 | One workbench projection digest differs | Entire bundle is rejected; no partial artifacts persist |
| U-017 | Structural site/load context is missing | Mechanical/BoM projections exist; structural approval is configuration-required |
| U-018 | Product part number changes in one factory call | GLB node, BoM row, section/material selection, and drawing mark all point to the new product digest |
| U-019 | Compile result contains duplicate artifact kinds | Consumer rejects the bundle atomically |
| U-020 | Managed factory runs outside Tertius runner | Clear runtime error directs the user to the Tertius CLI/compiler |
| I-001 | Minimal two-member frame compile | One execution produces a complete graph and linked workbench bundle |
| I-002 | Real Lysaght portal connection | Bracket/bolts visible and orderable; structural node/release model comes from the same connection |
| I-003 | Result persistence failure on one artifact | Transaction rolls back all artifacts and job success |
| I-004 | Extus Procurement fetch | Returns stored authoritative projection; no AST/GLTF analysis runs |
| I-005 | Timus drawing fetch | Uses the same compile revision and does not execute `design.py` |
| I-006 | Viewer/BoM/Structural cross-selection | One component ID selects the same installed part in all workbenches |
| E-001 | Full authenticated AI edit of selected Cee product | One source edit compiles; all workbench projections update together |
| E-002 | Full authenticated connection edit | Changed bracket/bolts update render, BoM, topology, checks, and drawings together |
| E-003 | Deliberate mismatch/unmanaged part | UI shows draft geometry but blocks ordering and structural approval |

## 13. Release Sequence

This is a coordinated cutover, not a compatibility rollout.

1. Land the runtime contracts, runner, and graph tests behind development-only
   deployment configuration while the production authoring path is unchanged.
2. Complete the Lysaght member and physical connection proof against the real
   worked example.
3. Complete atomic bundles and make all workbench consumers use the new
   artifacts in one integration branch.
4. Migrate the worked example and any seeded/default Tertius projects.
5. Delete the old authoring, inference, manifest, and re-execution paths in the
   same release candidate.
6. Run focused tests, Compose live-flow, isolated local-k3s full live-flow, and
   browser cross-workbench verification.
7. Deploy the new runtime and migrated projects together; invalidate old
   derived artifacts and queue clean recompiles.

Do not ship a state in which users choose between old and new design contracts,
or in which a workbench silently falls back to inferred data.

## 14. Definition Of Done

- `design.py` contains mechanical composition and a single `model` root only;
  no Tertius manifest, registry, BoM, solver, or workbench plumbing is required.
- Workbench-enabled imports automatically register product/component/connection
  facts inside the runner-owned compile session.
- One `design.py` execution produces one canonical compiled-design graph and an
  atomic set of rendering, procurement, structural, bounds, and drawing
  projections.
- A physical bracket/fastener connection is visible in CAD, represented in the
  BoM, and responsible for analytical topology through one connection object.
- Changing one selected product or connection updates every projection from the
  same source fact.
- Catalogue geometry cannot diverge from catalogue structural properties under
  the same product identity.
- Procurement and structural workbenches never infer authoritative facts from
  labels, source syntax, or coincident shapes.
- Missing or unverified data fails closed through readiness gates; draft
  geometry remains inspectable without being presented as approved.
- `TERTIUS_STRUCTURAL`, explicit BoM authoring, project runtime helpers, and
  workbench-specific re-execution are deleted, not retained as compatibility
  paths.
- The full structural worked example passes focused tests, Compose validation,
  isolated local-k3s live-flow, and browser cross-workbench verification.
