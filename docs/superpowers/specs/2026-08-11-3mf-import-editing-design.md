# 3MF Import And Faceted Editing Design

**Document type:** Implementation design

**Status:** Approved for implementation

## 1. Strategic Blueprint

| Question | Decision | Implementation implication |
|---|---|---|
| What exact problem is being solved? | An authenticated user with a 3MF model cannot create a Tertius project from it or edit its mesh objects through the existing Build123D, Generate Design, Intus, compile, and Extus viewer workflow. | Add a shared 3MF import flow that creates a project, converts the source once into unit-normalized faceted BREP geometry, and exposes that geometry to user-authored and AI-authored Build123D code. |
| What are the success metrics? | The supplied `falcon9_200mm.3mf` creates and activates a new project from Generate Design and Intus; conversion reaches a terminal success; the generated project compiles to GLB; Extus displays it; transforms and a simple boolean on a manifold imported solid compile; an AI edit can change the generated Python without receiving binary assets; focused, full, runtime-parity, authenticated live-flow, and browser checks pass. | Tests and live evidence must cover the real sample, not only synthetic cubes. |
| Why will this implementation remain coherent? | The original 3MF stays immutable, one conversion job owns the derived BREP and manifest, and compile jobs snapshot the exact derived asset digest. | Source provenance, conversion, editing, and compilation have separate explicit records instead of treating binary data as source code. |
| What is the core architecture decision? | Convert 3MF to faceted OpenCascade BREP once in an isolated worker during import, retain both original and derived assets, then load the derived BREP on every compile. | Do not parse 3MF in the API process or on every normal compile. |
| What is the stack rationale? | Keep React/Vite, FastAPI, PostgreSQL, NATS JetStream, the existing one-shot worker pattern, Build123D 0.8.0, py-lib3mf 2.3.1, Helm, and Compose. | Extend existing job, repository, worker, and polling patterns; add no CAD framework. |
| What are the MVP features? | Authenticated import from both Generate Design and Intus, collision-safe new-project naming, immutable source asset, asynchronous conversion, millimetre normalization, BREP plus manifest, generated `design.py`, GLB preview, part transforms/assembly, booleans for valid solids, retryable failure UI, and safe AI metadata context. | One imported 3MF source per newly created project; both UI entry points share the same component and API. |
| What is explicitly not being built? | No guest import, feature-history recovery, sketch/extrusion reconstruction, mesh healing, remeshing, texture preservation, per-triangle material editing, arbitrary project attachments, 3MF export, or guarantees for booleans on shells/invalid meshes. | Product copy calls the result faceted geometry and exposes limitations before upload. |

## 2. Architecture Decisions

### ADR-001: Upload-time conversion with immutable provenance

`POST /api/intus/projects/imports/3mf` stores the original bytes in a tenant/project-scoped `ProjectAsset`, creates a `ProjectImportJob`, and publishes a small NATS command referencing a JetStream object-store key and SHA-256 digest. The API performs bounded streaming upload checks and ZIP envelope preflight only. It does not invoke lib3mf or OpenCascade.

The isolated import worker fetches the exact source object, verifies its digest, parses and converts it, and publishes a result containing derived object-store references plus a bounded manifest. The API result consumer verifies digests, persists the derived BREP and manifest as immutable assets, marks the job terminal, and creates the generated `design.py` in one transaction.

The source 3MF remains authoritative provenance. The BREP and manifest are deterministic derived assets identified by a conversion-version string. Retrying creates a new import-job attempt and new derived asset revisions without mutating the original bytes.

### ADR-002: Native BREP is the editable runtime representation

Build123D 0.8.0 `Mesher.read()` reconstructs each mesh as triangular faces, sews them into shells, and returns `Solid` for a manifold closed shell or `Shell` otherwise. The converter scales every returned shape into millimetres using this fixed mapping:

| 3MF unit | Scale to millimetres |
|---|---:|
| micrometre | `0.001` |
| millimetre | `1` |
| centimetre | `10` |
| metre | `1000` |
| inch | `25.4` |
| foot | `304.8` |

The converter places the ordered shapes in one Build123D `Compound`, exports it with `export_brep`, and stores object names and solid/shell status by child index in the manifest. The compile runtime imports the BREP once through `import_brep`, reapplies manifest labels, and returns an `Imported3mfModel` wrapper with ordered `parts`, `parts_by_name`, `compound`, and manifest metadata.

The first implementation uses the mesh objects returned by Build123D 0.8.0. It records a `component_graph_not_preserved` warning when 3MF build-item/component instancing or transforms cannot be represented by that reader. It does not silently claim full 3MF assembly fidelity.

### ADR-003: Binary transport uses JetStream object storage

The existing compile-command limit is 8 MiB and source messages are UTF-8 text. Original 3MF and derived BREP bytes therefore never appear as base64 in source rows or command JSON.

Add a digest-addressed JetStream object bucket for project-asset transport. API producers put immutable blobs before publishing import or compile commands. Commands contain bucket, key, digest, and byte-size metadata. Workers fetch, size-check, digest-check, and hydrate bytes into their temporary workspace. Database `ProjectAsset.content` remains the durable authority; JetStream object storage is a transport/cache and may be repopulated from PostgreSQL.

### ADR-004: AI edits receive metadata, not geometry bytes

Project source files remain flat `.py` text. An LLM edit request adds a bounded `asset_context` derived from the successful import manifest: source display name, conversion version, units, part indices, unique safe labels, shape type, bounds, and boolean capability. The Pi workspace receives no `.3mf` or `.brep` content.

The repo-owned Pi system prompt documents `tertius_imports.load_3mf_model("source")`, directs edits to retain the loader unless replacement is intentional, and prohibits boolean operations on parts whose manifest status is not `solid`. The selected provider/model, optimistic source versions, automatic repair, and linked compile behavior remain unchanged.

## 3. User Experience Contract

### Shared dialog

Generate Design and Intus render the same authenticated `Import3mfDialog` beside their project controls. The trigger label is **Import 3MF**. Guest users do not see the trigger.

The dialog contains:

- a `.3mf` file picker with drag-and-drop support;
- a project-name input initialized from the safe filename stem;
- the compressed upload limit and faceted-geometry limitation;
- a statement that units are normalized to millimetres;
- a primary **Create project** action and cancel action;
- upload and conversion progress states that remain keyboard and screen-reader accessible;
- terminal warnings summarized before activation.

On success, the client activates the project only while it is still tracking that import. It emits the existing `tertius:active-project-changed` event, opens the generated `design.py` in Intus when applicable, starts a GLB compile, and displays the artifact in the shared Extus viewport. If the user navigated to another project while conversion ran, the new project is available in the selector but does not steal activation.

On failure, the dialog shows the bounded user message, retains the failed project's original asset, and offers **Retry conversion**. The previously active project remains active.

### Generated source

Successful conversion creates this stable starting contract:

```python
import build123d as bd
from tertius_imports import load_3mf_model

imported = load_3mf_model("source")
parts = imported.parts
parts_by_name = imported.parts_by_name
model = imported.compound
```

The compiler continues discovering `model` through the existing Build123D shape collection path. Users and AI may replace `model` with transformations, assemblies, or boolean results. The immutable imported geometry is reloaded on every compile, so editing Python never mutates the source asset.

## 4. API And Persistence Contract

### HTTP API

| Method and path | Request | Success | Principal failures |
|---|---|---|---|
| `POST /projects/imports/3mf` | Multipart `file` plus `project_name`; authenticated CSRF mutation | `202` with `job_id`, `project_name`, `status=queued` | `400 invalid_3mf_upload`, `409 project_name_conflict`, `413 import_too_large`, `503 import_unavailable` |
| `GET /projects/imports/3mf/jobs/{job_id}` | Authenticated tenant/user scope | Job status, bounded progress, warnings, project name, manifest summary, and optional compile linkage | `404 import_job_not_found` |
| `POST /projects/imports/3mf/jobs/{job_id}/retry` | Failed terminal job | `202` with new attempt status | `409 import_not_retryable` or `import_already_active` |
| `GET /projects/{name}/assets` | Project scope | Metadata only; never binary content | `404 project_not_found` |

The upload route reads bounded chunks and rejects the body as soon as the compressed-byte limit is exceeded. It accepts only `.3mf` plus the 3MF content type or generic octet-stream. The filename is display metadata; the stored logical source name is always `source.3mf`.

### Database entities

| Entity | Required fields and invariants |
|---|---|
| `ProjectAsset` | UUID, tenant/project composite scope, logical name, display name, kind (`source_3mf`, `derived_brep`, `import_manifest`), media type, immutable content, byte size, SHA-256, revision, conversion version, created time; unique project/kind/revision. |
| `ProjectImportJob` | UUID, tenant/project/user scope, source asset ID, attempt, status (`queued`, `running`, `succeeded`, `failed`), error code, bounded user message, progress payload, derived asset IDs, created/started/finished times; at most one active job per project. |
| `CompileJobAsset` | Compile job/project/tenant scope, project asset ID, logical hydrated filename, SHA-256, byte size, JetStream object key; immutable snapshot link. |

Deleting or replacing source assets is outside this MVP. Asset metadata endpoints never return `content`, object-store credentials, raw tenant IDs, or transport keys.

### Manifest schema version 1

```json
{
  "schema_version": 1,
  "conversion_version": "tertius-3mf-brep-v1-build123d-0.8.0",
  "source_sha256": "<64 lowercase hex>",
  "source_unit": "MM",
  "scale_to_mm": 1.0,
  "object_count": 1,
  "total_vertices": 0,
  "total_triangles": 0,
  "warnings": [],
  "parts": [
    {
      "index": 0,
      "name": "part_001",
      "source_name": "",
      "shape_type": "solid",
      "boolean_capable": true,
      "is_valid": true,
      "vertex_count": 0,
      "triangle_count": 0,
      "bounds_mm": {"min": [0, 0, 0], "max": [1, 1, 1]}
    }
  ]
}
```

Generated names are unique, ASCII-safe identifiers. Duplicate or blank source names become `part_001`, `part_002`, and so on. Raw metadata values are not copied into logs, telemetry labels, or AI context.

## 5. Resource And Security Limits

| Limit | Value | Enforcement point |
|---|---:|---|
| Compressed upload bytes | 128 MiB | Streaming API upload and worker digest metadata |
| ZIP entries | 2,048 | API envelope preflight and worker validation |
| Total uncompressed ZIP bytes | 512 MiB | Central-directory preflight and worker extraction |
| Single XML part | 64 MiB | Worker parser preflight |
| Mesh objects | 2,048 | Worker before BREP conversion |
| Total vertices | 10,000,000 | Worker before BREP conversion |
| Total triangles | 10,000,000 | Worker before BREP conversion |
| Absolute normalized coordinate | 1,000,000 mm | Worker numeric validation |
| Manifest JSON | 256 KiB | Worker result validation and persistence |
| Conversion wall time | 300 seconds | Import worker process-tree timeout |
| Derived BREP bytes | 512 MiB | Worker result and object-store put |

3MF is treated as untrusted ZIP/XML/native-parser input. Reject encrypted entries, traversal paths, absolute paths, duplicate canonical entry names, unsupported compression methods, non-finite coordinates, missing 3D model relationships, and digest/size mismatches. Parse and convert only under the canonical k3s one-shot-worker controls: non-root, dropped capabilities, read-only root, bounded `/tmp`, no service-account token, isolated network, process timeout, CPU/memory/ephemeral-storage limits, and gVisor when enabled.

Compose remains a development adapter and must document that it does not equal the canonical k3s native-parser isolation.

## 6. Component And File Map

| File or module | Responsibility |
|---|---|
| `server/core/models.py` | Add immutable assets, import jobs, and compile-job asset snapshots. |
| `server/migrations/versions/` | Add tenant-safe tables, composite constraints, indexes, and active-job uniqueness. |
| `server/core/repositories.py` | Asset/import repositories, transactional result application, retry, and compile asset snapshot. |
| `server/core/project_assets.py` | Names, MIME types, digest validation, bounded public DTOs, and conversion manifest schema. |
| `server/core/object_store.py` | Digest-addressed JetStream object put/get/delete-cache helpers. |
| `server/core/import_3mf_messages.py` | Versioned import command/result/progress contracts with no embedded binary. |
| `server/workflows/intus/intus_server.py` | Streaming upload, list/status/retry endpoints, and safe authorization. |
| `server/workflows/intus/import_3mf_job.py` | One-shot worker entrypoint, heartbeats, conversion subprocess, result publication, and ACK/NAK behavior. |
| `server/workflows/intus/import_3mf_converter.py` | ZIP/native-parser validation, Mesher conversion, unit scaling, BREP export, manifest creation, and limits. |
| `server/workflows/intus/import_3mf_result_consumer.py` | Verify result provenance/digests, persist derived assets, generate `design.py`, and terminalize jobs. |
| `server/core/compile_messages.py` | Add immutable asset reference list to compile commands. |
| `server/core/compile_runtime.py` | Fetch and hydrate byte assets separately from UTF-8 source. |
| `server/core/compile_sandbox.py` | Make repo-owned `tertius_imports` helper available and translate imported-shell boolean failures. |
| `server/core/tertius_imports.py` | Load BREP/manifest, restore labels, and expose `Imported3mfModel`. |
| `server/core/llm_file_edit.py` and Pi command path | Build and deliver bounded asset context without editable binary files. |
| `server/core/pi_agent_system_prompt.md` | Document faceted-solid helper and solid-only boolean rule. |
| `ui/src/workflows/shared/projectStorage.ts` | Import/list/status/retry types and authenticated REST implementation. |
| `ui/src/workflows/shared/ui/Import3mfDialog.tsx` | Shared accessible picker, project name, progress, warnings, retry, activation, and compile handoff. |
| `ui/src/workflows/generate/GenerateDesignWindow.tsx` | Render the authenticated shared import trigger near project controls and display imported project artifacts. |
| `ui/src/workflows/intus/ui/CompilerTab.tsx` | Render the same import trigger and load generated `design.py` after success. |
| `infra/charts/tertius/` | Object-store config and import-worker KEDA/Job runtime wiring. |
| `docker-compose.yml`, `docker-compose.parity.yml` | Import worker/object-store settings and intentional isolation differences. |
| `scripts/check-runtime-parity.sh` | Require all new settings across Helm and Compose. |
| `scripts/smoke-live-flow.sh` | Optional 3MF path that uploads, waits, compiles, edits, recompiles, and verifies asset/job linkage. |

## 7. Error Handling Matrix

| Failure | Detection | API/job outcome | User recovery | Logging and telemetry |
|---|---|---|---|---|
| Unauthenticated or guest import | Auth dependency/UI mode | `401/403`; no project or asset | Log in and retry | Existing bounded auth signals only |
| Invalid project name | Existing project-name validator | `400 invalid_project_name` | Edit proposed name | Do not log raw name as label |
| Project-name collision | Tenant-scoped unique constraint | `409 project_name_conflict`; no partial import | Choose suggested collision-safe name | Bounded error code |
| Upload exceeds 128 MiB | Streaming counter | `413 import_too_large`; transaction rolled back | Use a smaller model | Log byte bucket, not filename |
| Invalid/encrypted/traversal ZIP | Envelope/worker validation | Failed job `invalid_3mf_archive` | Re-export valid 3MF | No raw entry names in metrics |
| Resource counts exceed limits | Worker preflight | Failed job `3mf_resource_limit` | Simplify/export a smaller mesh | Bounded limit category |
| Unsupported unit or non-finite/extreme coordinate | Worker numeric validation | Failed job `invalid_3mf_geometry` | Correct source units/geometry | No coordinate values in labels |
| Object store unavailable on upload | Put/publish failure | `503 import_unavailable`; transaction rolls back or remains retryable before publish | Retry | Existing NATS availability signals |
| Import worker timeout/OOM | Job deadline/reconciler | Failed job `3mf_conversion_timeout` or bounded worker failure | Simplify mesh and retry | No source bytes or names in logs |
| No manifold solids | Manifest contains only shells | Import succeeds with warning; transforms/assembly work; booleans are unavailable | Repair source mesh externally if booleans are required | Count by bounded shape type |
| BREP or manifest digest mismatch | Result consumer/runtime verification | Job or compile fails closed with `asset_integrity_error` | Retry conversion; operator inspects transport | Digest is not a metric label |
| Stale/missing object-store cache | Compile hydration | API repopulates from durable asset before publish; worker fails `asset_unavailable` if fetch still fails | Retry compile | Bounded transport status |
| Boolean requested on shell | Runtime helper/compile traceback translation | Compile fails with `imported_part_not_solid` and named part index | Choose a solid part or avoid boolean | Safe part index only |
| Client closes/navigates during conversion | Poll lifecycle | Job continues; project not auto-activated by stale client | Select project later | No special server error |
| AI provider unavailable | Existing LLM job behavior | Import and compile remain successful; edit job reports existing provider failure | Retry AI edit | Existing privacy-safe provider signals |

## 8. Anti-Patterns (Do Not)

| Do not | Do instead | Reason |
|---|---|---|
| Read 3MF with `File.text()` or store it in `ProjectFile.content`. | Stream bytes into immutable `ProjectAsset`. | Binary corruption and source/AI leakage. |
| Base64 binary assets into compile or Pi JSON commands. | Use digest-addressed JetStream object references. | Avoids message limits and payload amplification. |
| Parse lib3mf/OpenCascade geometry in the API process. | Use the isolated one-shot import worker. | Native parsers operate on untrusted input. |
| Discard the original after BREP conversion. | Retain immutable source and conversion provenance. | Conversion is derived and lossy. |
| Reparse 3MF on every compile. | Load the preconverted BREP. | Predictable latency and reproducibility. |
| Treat every imported shape as a valid solid. | Record `solid` or `shell`, validity, and boolean capability per part. | Non-manifold meshes cannot support reliable solid operations. |
| Claim preservation of full 3MF hierarchy, textures, or feature history. | Surface exact manifest warnings and faceted-model wording. | Build123D 0.8.0 does not preserve those semantics. |
| Ignore model units because geometry loaded successfully. | Scale coordinates explicitly to millimetres and test every supported unit. | Mesher reports units but leaves numeric coordinates unchanged. |
| Send 3MF/BREP bytes, raw metadata, or full manifest to the AI provider. | Send only bounded safe part metadata and helper documentation. | Privacy, context, and prompt-injection safety. |
| Auto-activate a project after the user navigated elsewhere. | Activate only from the client still tracking the successful job. | Avoids surprising cross-tab state changes. |
| Use compile-only smoke as final validation. | Run full authenticated import, compile, AI edit, and post-edit compile. | The feature changes Generate Design and AI behavior. |
| Test only a generated cube fixture. | Also exercise the supplied Falcon 9 3MF. | Synthetic geometry does not prove the requested workflow. |

## 9. Test Case Specifications

### Unit and component tests

| ID | Component | Input | Expected result | Edge case |
|---|---|---|---|---|
| U-001 | Upload validator | Valid small `.3mf` multipart stream | Exact bytes, size, digest, and safe display name accepted | Generic octet-stream accepted only with `.3mf` suffix |
| U-002 | Upload validator | 128 MiB plus one byte | `413 import_too_large` before full buffering | Chunk crosses boundary |
| U-003 | ZIP preflight | Traversal, absolute, encrypted, duplicate canonical, 2,049-entry, and 512 MiB-plus archives | Specific bounded rejection for each | Compressed-bomb ratio |
| U-004 | Asset repository | Same names in two tenants | Tenant-isolated immutable assets and digests | Cross-tenant lookup returns none |
| U-005 | Import repository | Two active jobs for one project | Second creation rejected by DB/repository invariant | Retry after terminal failure succeeds |
| U-006 | Object store | Put/get known bytes by digest | Byte-exact round trip and idempotent put | Existing key with wrong digest fails closed |
| U-007 | Import command/result | Valid object references and progress | Versioned serialization round trip | Embedded content and oversized manifest rejected |
| U-008 | Converter units | Identical boxes in MC/MM/CM/M/IN/FT | All normalized bounds match in millimetres | Unsupported unit fails |
| U-009 | Converter manifold status | Closed box and open triangle mesh | Box is valid `solid`; open mesh is `shell` and not boolean capable | Multiple inner shells recorded |
| U-010 | Converter boolean proof | Imported manifold box minus Build123D cylinder | Valid non-empty result | Shell boolean is not advertised |
| U-011 | Converter multi-object | Two named mesh objects | Stable order, unique safe names, counts, bounds, and compound BREP | Blank and duplicate names generated safely |
| U-012 | Converter limits | Too many vertices/triangles/objects, non-finite coordinate, extreme bound | Bounded resource/geometry failure | Threshold value itself succeeds |
| U-013 | Runtime loader | BREP plus matching manifest | Ordered parts, labels, dictionary, compound, and solid flags restored | Digest mismatch and index mismatch fail closed |
| U-014 | Compile snapshot | Project with successful import | Exact derived BREP/manifest assets linked to immutable compile job | Later retry does not alter old job links |
| U-015 | Compile hydration | Valid object refs | UTF-8 sources and binary assets written with correct modes and paths | Missing/cache-stale blob fails with bounded code |
| U-016 | LLM context | Successful import manifest | Bounded part metadata and helper contract included | Source/global metadata and bytes absent |
| U-017 | Result consumer | Successful derived refs | Assets, generated source, and terminal job committed atomically | Digest mismatch leaves no partial derived state |
| U-018 | Shared import dialog | Valid file and suggested project name | Multipart submission, progress polling, success warnings, activation event, and compile handoff | Cancel and navigation prevent stale auto-activation |
| U-019 | Shared import dialog errors | Collision, upload rejection, conversion failure | Inline accessible message and appropriate retry affordance | Double-submit disabled |
| U-020 | Surface gating | Guest and authenticated renders in Generate/Intus | Trigger hidden for guests and visible in both authenticated surfaces | Same dialog implementation used twice |

### Integration and runtime tests

| ID | Flow | Setup | Verification | Teardown |
|---|---|---|---|---|
| I-001 | PostgreSQL migration | Upgrade from current head | New constraints/indexes exist; downgrade/upgrade test preserves previous tables | Test database dropped by fixture |
| I-002 | NATS object transport | Local JetStream | Source and derived blobs move by reference; command/result remain below message limits | Test bucket removed |
| I-003 | Import pipeline | Synthetic MM and IN fixtures | Upload through API, one-shot conversion, result persistence, generated source, and terminal status succeed | Imported projects/assets deleted by fixture |
| I-004 | Compile pipeline | Successful synthetic import | Derived asset snapshots, worker hydration, BREP load, and GLB artifact succeed | Job/artifact fixture cleanup |
| I-005 | Boolean compile | Manifold imported solid plus generated subtraction code | GLB compile succeeds and bounds/volume differ from unedited source | Project fixture cleanup |
| I-006 | Failure/retry | Non-manifold/invalid/timeout fixtures | Bounded failure, previous active project retained, retry semantics correct | Job fixture cleanup |
| I-007 | Helm/Compose parity | Render canonical and adapter runtimes | Import subjects, object bucket, limits, worker env, KEDA/job controls, and documented isolation differences align | Render temp removed |
| I-008 | Falcon 9 requested sample | Downloaded `falcon9_200mm.3mf` with recorded SHA-256 | New project imports, conversion succeeds within configured limits, generated source compiles to GLB, viewer loads, and manifest accurately reports capabilities/warnings | Keep only user-requested project; remove disposable release/port-forwards |
| I-009 | Authenticated AI edit | Isolated k3s smoke release and explicit provider consent | Import Falcon 9, compile, submit a transform or supported boolean edit, observe persisted safe asset context, terminal AI job, linked post-edit compile, and viewer artifact | Standard isolated harness cleanup |
| I-010 | Browser accessibility | Both import entry points | Keyboard operation, focus return, live progress announcements, inline errors, warnings, and no console/network failures | Close browser session |

## 10. Validation And Rollout

Implementation follows TDD at each boundary. Run focused repository/model/message/converter/runtime tests before API and UI integration. Run frontend typecheck, lint, component tests, build, backend ruff/mypy/full pytest, migration tests, deployment config, Helm render, and runtime-parity checks before runtime validation.

The canonical proof uses an isolated local-values k3s smoke release with demo authentication, KEDA, NATS JetStream object storage, the import worker, compile worker, and LLM secret. The required live path is:

```text
authenticated browser
  -> Import 3MF from Generate Design
  -> new project/import job
  -> isolated conversion worker
  -> persisted BREP/manifest
  -> generated design.py
  -> GLB compile
  -> Extus viewer
  -> real AI edit using safe manifest context
  -> linked post-edit GLB compile
  -> updated viewer artifact
```

The supplied sample is acquired from `http://100.86.195.45:8000/outputs/falcon9/final/falcon9_200mm.3mf`, stored only in `/tmp` or disposable harness storage, and never committed. Record its SHA-256 and byte size in validation evidence. If that endpoint is unavailable, synthetic fixtures do not substitute for final completion; report the exact network blocker and keep the goal incomplete.

Before sending generated project Python to the external provider, obtain explicit user consent in the live validation turn. The original/derived binary assets are never sent.

## 11. References

| Topic | Location | Exact section or symbol |
|---|---|---|
| Project persistence | [`../../../server/core/models.py`](../../../server/core/models.py) | `Project`, `ProjectFile`, `SourceSnapshot` |
| Project repository and filename rules | [`../../../server/core/repositories.py`](../../../server/core/repositories.py) | `ProjectRepository`, `require_valid_python_filename` |
| Compile command snapshots | [`../../../server/workflows/intus/intus_server.py`](../../../server/workflows/intus/intus_server.py) | `compile_project` |
| Compile message size/config | [`../../../server/core/compile_messages.py`](../../../server/core/compile_messages.py) | `CompileCommand`, payload validation |
| Text hydration boundary | [`../../../server/core/compile_runtime.py`](../../../server/core/compile_runtime.py) | `hydrate_project_files` |
| Sandbox and exporters | [`../../../server/core/compile_sandbox.py`](../../../server/core/compile_sandbox.py) | `_sandbox_worker`, `run_compile_sandbox` |
| Build123D 0.8 3MF reader | [`../../../.venv/lib/python3.14/site-packages/build123d/mesher.py`](../../../.venv/lib/python3.14/site-packages/build123d/mesher.py) | `Mesher.read`, `_get_shape` |
| Build123D BREP persistence | [`../../../.venv/lib/python3.14/site-packages/build123d/exporters3d.py`](../../../.venv/lib/python3.14/site-packages/build123d/exporters3d.py) | `export_brep` |
| Existing browser project adapter | [`../../../ui/src/workflows/shared/projectStorage.ts`](../../../ui/src/workflows/shared/projectStorage.ts) | `ProjectStorage`, `createAuthenticatedStorage` |
| Existing Intus upload control | [`../../../ui/src/workflows/intus/ui/CompilerTab.tsx`](../../../ui/src/workflows/intus/ui/CompilerTab.tsx) | `handleUpload` and upload trigger |
| Generate Design flow | [`../../../ui/src/workflows/generate/GenerateDesignWindow.tsx`](../../../ui/src/workflows/generate/GenerateDesignWindow.tsx) | project selection, submit, compile, and repair lifecycle |
| Canonical browser validation | [`../../harness/browser-validation.md`](../../harness/browser-validation.md#journey-c-authenticated-full-workflow) | Journey C |
| Runtime parity | [`../../harness/runtime-parity.md`](../../harness/runtime-parity.md#parity-checklist) | Parity checklist |
| Quality gates | [`../../harness/quality-gates.md`](../../harness/quality-gates.md#change-to-validation-matrix) | Change-to-validation matrix |

## 12. Clarity Gate

### Foundation checks

- [x] Actionable: every behavior maps to an API, entity, module, or verification.
- [x] Current: inspected against `master` at `53709d2` on 2026-08-11.
- [x] Single source: the original asset, conversion job, manifest, and compile snapshot each have one owner.
- [x] Decision, not wish: storage, conversion timing, units, limits, failures, and UI outcomes are fixed.
- [x] Prompt-ready: implementation does not require an agent to select product behavior.
- [x] No future state: unsupported fidelity and reconstruction features are explicit non-goals.
- [x] No fluff: each section carries an implementation implication.

### Document architecture checks

- [x] Type identified as implementation design.
- [x] Anti-patterns are present only in this implementation document.
- [x] Test cases are present only in this implementation document.
- [x] Error handling is present only in this implementation document.
- [x] Deep links identify exact paths and symbols or anchors.
- [x] Existing harness and workflow contracts are referenced rather than duplicated.

### AI coder understandability score

| Criterion | Score | Evidence |
|---|---:|---|
| Actionability (25%) | 10/10 | Exact entities, endpoints, worker flow, UI behavior, and rollout. |
| Specificity (20%) | 10/10 | Limits, states, errors, schemas, unit factors, and commands are concrete. |
| Consistency (15%) | 10/10 | Immutable source and derived digest snapshots define provenance everywhere. |
| Structure (15%) | 9/10 | Contracts and matrices separate decisions, components, and validation. |
| Disambiguation (15%) | 10/10 | Twelve anti-patterns and twenty unit/component cases cover failure-prone boundaries. |
| Reference clarity (10%) | 9/10 | Exact source paths, symbols, and harness anchors are listed. |

**Weighted score:** 9.7/10. The design passes the Stream Coding implementation threshold.
