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
- [ ] #312: Productionize definition-only loading and selected chunk replay so a
  changed chunk can be rebuilt without constructing the whole design.
- [ ] #313: Persist immutable component artifacts and use pre-export cache hits
  to skip replay/export for unchanged chunks.
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

- [ ] Unit tests cover cache-key stability/invalidation for related and
  unrelated edits.
- [ ] Integration tests compare full compile vs selected replay on the reference
  shed.
- [ ] Runtime tests verify cache hits skip replay/export and cache misses rebuild
  only selected chunks.
- [ ] Authenticated live-flow covers manifest compile, artifact fetch, and Extus
  viewer behavior.
- [ ] Telemetry uses bounded labels and never emits source, prompts, raw IDs, or
  component hashes.
