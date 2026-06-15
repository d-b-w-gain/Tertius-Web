# Build123D Colours In Viewer (Implementation)

## 1. Scope

Preserve colours assigned by Build123D scripts through the Intus GLB artifact path and render those colours in Extus.

References:

| Topic | Location | Anchor |
|-------|----------|--------|
| Issue | `https://github.com/d-b-w-gain/Tertius-Web/issues/142` | issue-142 |
| Sandbox export path | `server/core/compile_sandbox.py` | `SANDBOX_SCRIPT` |
| Viewer GLTF loader | `ui/src/workflows/extus/ui/ViewerTab.tsx` | GLTF parse effect |
| Sandbox tests | `server/tests/test_compile_sandbox.py` | compile sandbox tests |
| Viewer tests | `ui/src/workflows/extus/ui/ViewerTab.active.test.tsx` | active viewer tests |

## 2. Implementation Decisions

| Decision | Implementation |
|----------|----------------|
| Primary colored artifact format | GLB/GLTF, because Extus already loads GLTF artifacts. |
| Source of truth for colour | Build123D `Shape.color` / part material exported by `bd.export_gltf`. |
| Uncoloured fallback | Existing steel-blue default material remains for meshes without authored GLTF material colour. |
| Viewer batching | Preserve source meshes and their authored materials when any mesh has an explicit GLTF material colour; keep current merged default batch for uncoloured models. |
| Selection/isolation | Continue using source meshes for raycast and overlays; selected meshes may use highlight material. |

## 3. Anti-Patterns (DO NOT)

| Don't | Do Instead | Why |
|-------|------------|-----|
| Do not force every GLTF mesh to the shared steel-blue material. | Preserve authored mesh material when it has an explicit colour. | This discards Build123D colours. |
| Do not disable batching for every model. | Disable the merged batch only when authored colours are present. | Large uncoloured assemblies still need the current performance path. |
| Do not infer colour from labels or names. | Use GLTF material colour exported from Build123D. | Names are not a reliable rendering contract. |
| Do not change STL/STEP behaviour. | Scope colour preservation to viewer artifact formats. | STL/STEP do not drive the Extus material path. |
| Do not make uncoloured meshes white/black because GLTF created a default material. | Treat missing material colour as uncoloured and use the existing default appearance. | Acceptance requires existing uncoloured models to render normally. |

## 4. Test Case Specifications

### Unit Tests

| Test ID | Component | Setup | Expected Output | Edge Cases |
|---------|-----------|-------|-----------------|------------|
| TC-001 | `run_compile_sandbox` | Build123D red cube script, export `glb` | GLB JSON has a material with red base colour | Alpha may be present. |
| TC-002 | `ViewerTab` loader | GLTF scene with a red material | Red mesh remains visible with its source material | No merged default batch for colored model. |
| TC-003 | `ViewerTab` loader | GLTF scene with no authored material colour | Existing `TertiusBatchedMesh` default path is used | Source meshes hidden as before. |
| TC-004 | `ViewerTab` quality toggle | Colored source mesh loaded | Cast/receive shadows update without replacing material colour | High/low quality. |
| TC-005 | `ViewerTab` cleanup | Colored source mesh loaded then replaced | Source materials/geometries are disposed | Array and single material. |

### Integration Tests

| Test ID | Flow | Setup | Verification | Teardown |
|---------|------|-------|--------------|----------|
| IT-001 | Intus minimal issue case | Red Build123D cube, compile GLB | Artifact contains red material and viewer loader keeps it | Temporary project dir. |
| IT-002 | Existing uncoloured model | Default purlin compile GLB | Viewer uses existing default appearance path | Temporary project dir. |
| IT-003 | Normal compile job path | Async compile result artifact | Stored artifact bytes are unchanged except preserving GLB material data | Existing compile fixtures. |

## 5. Error Handling Matrix

| Error Type | Detection | Response | Fallback | Logging |
|------------|-----------|----------|----------|---------|
| GLB has no parseable material colour | Viewer material inspection finds no explicit colour | Use existing merged default batch | Steel-blue default render | none |
| GLB parse fails | `GLTFLoader.parse` error callback | Keep current error logging | No model update | `console.error` |
| Build123D GLB export fails | Sandbox process exits non-zero | Existing compile failure path | No artifact | existing stderr |
| Material cloning/disposal unsupported in mock/test object | Type guard fails | Leave source material unchanged or dispose if available | Avoid runtime crash | none |

## 6. Clarity Gate

| Check | Status |
|-------|--------|
| Actionable/current/single source | Pass |
| Decisions, not wishes | Pass |
| Prompt-ready/no future-state/no fluff | Pass |
| Type identified | Implementation |
| Anti-patterns/test cases/error matrix placed here | Pass |
| Deep links present | Pass |

AI coder understandability score: 9/10. Remaining implementation detail is the exact GLTF material shape from Three.js; resolve by inspecting Three material fields in tests and code.
