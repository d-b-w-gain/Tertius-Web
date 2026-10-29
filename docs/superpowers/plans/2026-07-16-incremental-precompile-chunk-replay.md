# Incremental Pre-Compile Chunk Replay Plan

**Goal:** Turn the #309 spike into a production path where unchanged design
components can be skipped before full Build123D shape construction, then served
through cached component artifacts and scene manifests.

**Spike outcome:** #309 proved the central thesis on the real reference shed:
selected visual-producing functions can be replayed from provenance-resolved
arguments without running the final top-level `building = shed_building(...)`
construction. A roof-only cache key stayed stable for unrelated wall colour
changes and changed for roof colour changes.

## Sequence

- [x] #309: Prove pre-compile chunk replay is feasible on the real reference
  design.
- [x] #311: Define the deterministic chunk/component contract, including
  explicit registration where inference is unsafe, provenance source-map inputs,
  pre-export cache keys, and fallback rules.
- [x] #312: Productionize selected chunk replay so a changed chunk can be
  rebuilt without constructing the whole design. Delivered through explicit
  registration (cadcache `@part`) rather than definition-only loading; see
  `docs/superpowers/specs/2026-10-26-incremental-component-cache.md`.
- [x] #313: Persist immutable component artifacts (exact BREP plus viewer
  tessellation) in tenant-scoped NATS Object Store entries and use pre-export
  cache hits to skip replay and meshing for unchanged chunks.
- [ ] #314: Publish bounded scene manifests instead of sending large monolithic
  GLB payloads through the compile result stream.
- [ ] #315: Teach Extus to load manifests, fetch changed component assets, and
  preserve selection, visibility, BoM/source navigation, and colours.
- [ ] #316: Harden cache lifecycle, invalidation, tenant isolation,
  observability, and runtime parity.
- [ ] #310: Keep shared-mesh monolithic GLB compaction as an optional fallback,
  not the primary incremental compile strategy.

## Architecture

The production path should decide cacheability before expensive geometry work:

1. Full compile with provenance records visual-producing calls and source maps.
2. Subsequent compile computes changed source/input regions and candidate
   affected chunks.
3. For replayable chunks, Tertius loads the design in definition-only mode:
   imports, constants, helper functions, and classes are kept; final top-level
   shape construction is skipped.
4. A pre-export cache key is computed from the selected function, resolved
   provenance arguments, declared/direct dependencies, quality/export settings,
   runtime versions, and schema version.
5. Cache hits skip replay and export.
6. Cache misses replay only the selected function and export/persist that
   component artifact.
7. The compile result publishes a small scene manifest referencing immutable
   component artifacts.
8. Unsafe designs fall back to full compile plus post-build artifact chunking.

## Design Rules To Settle

- Definition-only mode must never silently skip required setup. It should report
  skipped statements and fall back when a selected function depends on them.
- Arbitrary Python cannot be perfectly sliced. The production API should support
  explicit chunk registration so design authors can declare stable component
  IDs, inputs, dependencies, and replay functions.
- Cache keys must distinguish geometry identity from instance identity,
  placement, material changes, quality/tessellation settings, and runtime
  versions.
- Post-build chunk manifests remain useful for transport and viewer loading, but
  they are not evidence of compile-time savings.

## Validation Gate

Before calling the feature production-ready:

- [x] Unit tests cover cache-key stability/invalidation for related and
  unrelated edits.
- [x] Integration tests compare full compile vs selected replay on the reference
  shed (`scripts/spikes/profile_incremental_component_compile.py`).
- [x] Runtime tests verify cache hits skip replay/export and cache misses rebuild
  only selected chunks (`server/tests/test_component_cache_sandbox.py`).
- [ ] Authenticated live-flow covers manifest compile, artifact fetch, and Extus
  viewer behavior.
- [ ] Telemetry uses bounded labels and never emits source, prompts, raw IDs, or
  component hashes.

## Scene Manifests And Component Assets (#314/#315)

**Why now:** with component caching, a one-part edit on the reference shed
spends about 21 s exporting, patching and deduplicating a GLB of the whole
model, although only the changed component's geometry is new. The viewer then
downloads and rebuilds the whole scene.

**Approach:** keep the scene the viewer builds identical; change only how it
arrives.

1. **Component assets (worker).** For each cached `@part` that is a leaf of the
   cache tree, export its own GLB (same tessellation, names, colours and
   `tertiusComponent`/`tertiusPart` extras as the monolithic export) when the
   part is built, and keep it with its cache entry. Geometry built directly by
   an outer part goes into that part's "remainder" asset. Unchanged parts reuse
   their stored asset, so an edit exports only changed parts.
2. **Transport.** The worker uploads new assets to a tenant-scoped JetStream
   Object Store bucket, named by content digest (the path compile sidecars and
   the component cache already use). The compile result carries a bounded,
   versioned `scene` manifest artifact: per node, asset digest, transform,
   hierarchy position, and the metadata the viewer reads today.
3. **Storage and API.** The result consumer copies any assets it doesn't hold
   yet into a tenant-scoped, content-addressed table and stores the manifest
   only after all its assets are present, so no viewer sees a manifest with
   missing parts. `GET /api/extus/scene` returns the active manifest;
   `GET /api/extus/scene-assets/{digest}` returns an immutable asset with
   long-lived cache headers.
4. **Viewer.** Extus loads the manifest, fetches assets it doesn't have (one
   request per unique digest), and assembles the same Object3D tree the
   monolithic GLB produced, so selection, isolation, visibility, colours,
   batching, BoM and Artus links keep working. On the next compile it fetches
   only new digests and swaps the nodes whose digest changed.
5. **Fallback.** The monolithic GLB stays in the bundle until the manifest path
   is validated; the viewer uses it when no manifest is present.

**Phases:**

- [ ] Phase 1: per-component GLB export and the `tertius.scene.v1` manifest in
  the sandbox, checked against the monolithic GLB (same nodes, names, extras,
  world transforms and triangle counts).
- [ ] Phase 2: asset upload, consumer storage (migration), atomic manifest
  activation and the two API routes, with tenant-isolation tests.
- [ ] Phase 3: Extus manifest loading and incremental replacement, with browser
  validation of selection, isolation, colours and BoM/Artus links.
- [ ] Phase 4: full `live-flow` on an isolated k3s smoke release, then remove
  the monolithic GLB from the default bundle.
