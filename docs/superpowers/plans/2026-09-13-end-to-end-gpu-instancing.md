# End-to-end GPU instancing

## Goal

Render repeated CAD primitives with `THREE.InstancedMesh` from an explicit
identity that survives the complete compiler-to-viewer path, without changing
component selection, assembly-tree identity, authored materials, collision
analysis, or mirrored and transparent geometry behavior.

## Correctness invariants

- The GLB optimiser may deduplicate binary accessors and mesh definitions, but
  must retain every component node and its transforms and extras.
- The viewer may instance only primitives that share a GLTF mesh and primitive
  association and the same source material identity.
- Transparent and mirrored primitives remain on the established rendering path.
- Source component meshes remain available, hidden, for collision analysis,
  selection overlays, assembly-tree appearance changes, and provenance.
- Viewer-only batches must never enter collision analysis or selection bounds.
- Shared geometry and materials are disposed once when a model is replaced.

## Delivery slices

- [x] Deduplicate identical GLB mesh/accessor payloads on the server.
- [x] Preserve GLTF node, mesh, and primitive associations in the loaded scene.
- [x] Convert eligible repeated opaque primitives to GPU instance batches.
- [x] Keep compatibility rendering for unique, mirrored, and transparent meshes.
- [x] Fall back to source meshes for selection, collision, and appearance edits.
- [x] Expose instance/batch/fallback counts in the viewer for verification.
- [ ] Validate a representative shed GLB in the browser and record draw-call,
  load-time, memory, selection, appearance, and collision results.
- [ ] Replace retained source render meshes with lightweight component records
  after collision and selection can consume those records directly.
- [ ] Evaluate emitting `EXT_mesh_gpu_instancing` once virtual component records
  can preserve the assembly tree and provenance without physical scene nodes.

## Acceptance checks for this slice

- Repeated explicit GLTF identities form one instance batch with correct matrices.
- Missing identities, material mismatches, and mirrored transforms do not batch.
- Transparent meshes preserve their authored opacity path.
- Viewer instance meshes are excluded from collision and picker traversal.
- Shared resources are not double-disposed.
- The UI test suite, production build, and authenticated live-flow pass.

## Representative fixture measurement

`ui/scripts/measure-glb-instancing.mjs` measured the existing shed wall fixture
after `server.core.glb_dedup.deduplicate_glb_bytes`:

- GLB size: 15,548,076 bytes to 3,027,980 bytes.
- Rendered primitives: 12,800.
- GPU-instanced primitives: 10,136 across 1,364 batches.
- Compatibility-path primitives: 2,664.
- Estimated viewer draw objects: 4,028, a 68.5% reduction.
