# Incremental Component Cache

Date: 2026-10-26

Issues: #312 (replay changed Build123D chunks before full design construction)
and #313 (persist content-addressed replay and viewer component artifacts),
under epic #61. Implements the contract in
`2026-07-16-incremental-component-contract.md`.

## Summary

Designs opt in by marking component builders with
[cadcache](https://github.com/d-b-w-gain/cadcache)'s `@part` decorator. On
each compile, a marked builder whose inputs, reachable project code and
runtime are unchanged is not run: its exact geometry is loaded from a BREP
entry, with the export tessellation already attached, so the GLB exporter
skips meshing it too. Only components whose inputs changed are rebuilt.

The compile worker keeps entries in a tenant-scoped NATS JetStream Object
Store bucket and copies only the current project's entries into the job's
`/tmp` before `design.py` runs. Every component also gets a partnum-style part
number (`AAA-XXXX-XXXXX`) hashed from its inputs only, surfaced in GLB node
extras, the source map and a new `components` compile artifact.

```python
import build123d as bd
from cadcache import part


@part
def roof_cladding(mark="RC01", length_mm=portal_depth, width_mm=portal_span):
    ...  # plain Build123D; returns a Shape


building = bd.Compound(children=[
    roof_cladding().moved(roof_location),   # placement stays outside the part
    ...
])
```

The same file runs unchanged outside Tertius (`python design.py`,
`cadcache watch design.py`), where cadcache uses a local `.cadcache` folder.

## Contract Mapping

| Contract (#311) | Implementation |
| --- | --- |
| Explicit registration | `@part` on a top-level builder. Registration wins: no provenance inference is needed for marked builders. |
| `component_id` | the builder's qualified name (author supplied, stable across edits) |
| `replay` | the decorated function |
| `inputs` | call arguments bound to the signature with defaults applied, canonicalised (`f(10)`, `f(w=10)` and `f(10.0)` are one entry) |
| `dependencies` | collected by cadcache: project code the builder reaches (AST-hashed, so comments and formatting don't count), plain-data globals it reads (by value), and files it opens (by content, re-checked on every hit) |
| Instances / placement | outside the part: the assembly moves the returned shape, so placement-only edits never invalidate geometry |
| Registrations | managed components and connections a builder registers are recorded with its entry (cadcache `record_effect`) and replayed by `CompileSession.replay_registrations` on a hit, so registering builders are cached too |
| Unsafe replay → fallback | a part whose result contains components it did not register itself (`component_replay_ineligible_foreign_registration`), that registers something outside its result (`..._registration_outside_result`), that connects to components registered outside it (`..._external_connection`) or that repeats a registered shape (`..._repeated_registration`) is built and returned but not stored; unkeyable arguments raise a clear `TypeError` |
| Source navigation | provenance records a cadcache part call as a call of its builder, with resolved arguments, so cache hits keep `tertiusSourceCallIds` |

Definition-only loading from the #309 spike (`server/core/component_replay.py`)
is not used on this path. With explicit registration the top-level code runs
as written, and only the expensive builders are skipped. That avoids the
spike's hard cases: skipped setup statements, top-level mutation, and
unresolved provenance arguments. The full design still executes, so
unmarked code (portal frames with managed members, for example) costs what
it costs today.

### Pre-export cache key

cadcache key = SHA-256 of canonical JSON over:

- cadcache key format, Python `major.minor`, Build123D and OCP versions;
- the builder's module and qualified name, its AST hash and those of the
  project code it reaches, plain-data global values, bound arguments, and
  `@part(version=N)`;
- the Tertius salt (`tertius/component_cache.py`):
  - contract schema version, artifact schema version, source-map version;
  - a digest of the Tertius runtime package (so a runtime deploy invalidates);
  - the cadcache version;
  - export family and tessellation: `mesh` with the GLB/glTF
    `(linear, angular)` deflection for that quality, or `exact` for
    STEP/STL/views/bounds, which share entries.

Not in the key: timestamps, absolute paths, tenant/user/project/job IDs,
secrets, prompt text, instance IDs or transforms. Tenant scope is applied to
the storage location instead (below).

## Viewer Artifacts And Tessellation (#313)

GLB export is the largest cost a cache can remove once construction is
cached. On the reference shed it is 18 s and does not shrink with the
components cached today (see Measurements); construction remains the larger
cost there. A GLB compile meshes each newly built part
at the export deflection before storing it. OCCT writes the triangulation
into the BREP, and a later hit loads it. build123d's `export_gltf` only
meshes a node that lacks a fine-enough triangulation, so cached components
skip meshing entirely.

The persisted component artifact is therefore the exact BREP plus its viewer
mesh. Separate per-component GLB files are deferred to #314/#315. Today
nothing would consume them, because Extus still loads one monolithic GLB,
and exporting them would add work to every cache miss. The `components`
artifact already lists each component's entry and part number, as a starting
point for #314's manifest.

## Storage Decision

Compile jobs are KEDA `ScaledJob` pods whose only writable disk is an
`emptyDir` on `/tmp`, so cadcache's default local folder starts empty on every
compile. Options considered:

| Option | Verdict |
| --- | --- |
| PVC mounted into compile jobs at `CADCACHE_DIR` | Rejected. A ScaledJob pod learns its tenant only after it starts, so one shared mount would expose every tenant's entries to unrestricted `design.py`, against epic #61's rule against broad storage authority in compile pods. `local-path` RWO volumes also pin jobs to one node. |
| Chart Valkey | Rejected. Compile jobs have no Valkey egress today, and Valkey runs without auth and holds other application state. Opening it to untrusted pods widens their reach, and memory-bound storage is a poor fit for multi-MB BREPs. |
| External object storage (S3/MinIO) | Not in the chart; adds a dependency and credentials to manage. |
| **NATS JetStream Object Store** | **Chosen.** Already deployed, already reachable from compile jobs under the existing NATS-only NetworkPolicy, and already used for compile sidecars. Content digests are verified on read. Bucket TTL and size bound it. |

The trusted compile worker (`server/core/component_cache.py`) mediates all
access:

1. Object names are computed on the worker from hashed tenant and project IDs:
   entries at `t/<tenant>/e/<component>/<key>.{brep,json}`, the project
   manifest at `t/<tenant>/p/<project>/manifest.json`. The same cache key in
   two tenants is two objects. Raw IDs never appear in names.
2. Before the sandbox runs, the worker restores the entries named in the
   project's manifest into a per-compile folder, within
   `componentCachePrefetchMaxBytes`. Each entry is restored whole: BREP first,
   sidecar last, matching cadcache's own completion marker.
3. The sandbox gets only that folder (`TERTIUS_COMPONENT_CACHE_DIR`).
4. After a **successful** compile, the worker uploads the entries the run
   built and rewrites the manifest with the entries the run used. Failed
   compiles publish nothing.
5. Entries are immutable. Rewriting an identical object is a no-op, so retries
   and duplicate deliveries are idempotent. A missing, oversized or
   digest-mismatched object is a cache miss, and a store outage means a cold
   compile, never a failed one.

### Trust boundary and known gap

A design can only poison its own tenant's entries, and running its own code
already gives it that power. The remaining gap predates this change: NATS has
no authentication, and the sandbox subprocess shares the compile pod's
network namespace. A deliberately malicious `design.py` could open its own
NATS connection and read any bucket, including this one, the compile
sidecars and the compile streams. Closing that needs NATS accounts or
credentials kept from the sandbox, or a network-less sandbox process. It is
tracked as #316 hardening. Mounting a shared PVC would have made this exposure
trivial and impossible to close later.

### Lifecycle

- Bucket `TERTIUS_COMPONENT_CACHE`: 14-day TTL, 1 GiB, file storage, NATS
  `discard: new` (a full bucket rejects writes, so compiles run cold until
  TTL frees space). With the 8 GiB sidecar bucket this fits the 10 Gi
  JetStream PVC; raise both together.
- Reference-tracking GC, quotas per tenant and refresh-on-hit are #316.

## Part Numbers

Each `@part` call gets `AAA-XXXX-XXXXX`, computed exactly as
[partnum](https://github.com/d-b-w-gain/partnum) computes one (frozen scheme
`pn/1`), from:

- `class`: the builder's name with hyphens, or `@part(part_class=...)`;
- `parameters`: bound arguments plus the plain-data globals the builder reads;
- `uses`: parts passed in as arguments, by their own part numbers.

Code is excluded. A refactor rebuilds the component (its cache key changes)
but keeps its part number, while any input change gives a new number. Moved
or copied instances keep their part's number; boolean results don't inherit
it.

Surfaced in:

- GLB node extras `tertiusPart: {partNumber, component}` (alongside the
  existing `tertiusComponent`);
- the source map: `component_part_number` on the builder's source call;
- the `components` compile artifact (`tertius.components.v1`): one record per
  distinct component geometry with `component`, `part_number`,
  `cache_status` (`hit`, `built`, `uncacheable`, `uncached`), `instances`,
  build/load seconds, diagnostic and cache entry.

Part numbers are never telemetry labels.

## Telemetry

All labels are bounded. There are no tenant/project/job IDs, component names,
part numbers or keys.

| Metric | Labels |
| --- | --- |
| `tertius.compile.phase.duration` | `phase` (`startup`, `design`, `metadata`, `export`, `patch`, `dedup`, `finish`), `export_format` |
| `tertius.compile.component_cache.components` | `cache_status`, `export_format` |
| `tertius.compile.component_cache.component.duration` | `operation` (`build`, `load`), `export_format` |
| `tertius.compile.component_cache.store.operations` | `operation` (`open`, `prefetch`, `publish`), `outcome` (`ok`, `miss`, `unavailable`, `integrity_failed`) |
| `tertius.compile.component_cache.store.duration` | `operation`, `outcome` |
| `tertius.compile.component_cache.store.bytes` | `operation` |

Compile consume spans also carry counts:
`tertius.component_cache.{hit,built,uncacheable}`,
`prefetched_entries`, `prefetch_outcome`, `publish_outcome`.
Queries are in `docs/harness/queries/compile.promql`.

## Measurements

Measured on 2026-10-28 with `scripts/spikes/profile_incremental_component_compile.py`
inside the production API image (`tertius-api:master-256-1-c137c7a`, Python
3.14, build123d 0.11.1), with this branch's `server/` and `tertius/` and
cadcache 0.2.0 (`v0.2.0`). Default GLB quality on an 8-core k3s host that was
also serving production. Every compile used a fresh, randomly named project
folder, as `hydrate_project_files` does. Raw results stay out of the repo.

**Result:** with cadcache 0.2.0, caching made the reference shed about 7%
slower. Removing cadcache's audit hook made it about 6% faster. With
registration replay, mark lookup and a shed restructured to hand out
references instead of live shapes (cadcache 0.4.0), a one-part edit
compiles in 60–74 s instead of about 146 s, and an unchanged compile in
35–42 s, with byte-identical workbench artifacts (see "Fast one-part
edits").

### Reference shed (3x5shed `2cbbcc7`)

19 builders marked with `@part`, called 27 times per compile.

| Scenario | Runs | Design phase | Export | Compile wall |
| --- | --- | --- | --- | --- |
| No cache (today) | 3 | 108.1–109.9 s | 18.0–18.5 s | 143–150 s |
| Cache, empty (first compile) | 1 | 142.5 s | 18.2 s | 171 s |
| Cache, nothing changed | 3 | 125.5–126.2 s | 18.1 s | 154 s |
| Cache, comment-only edit | 1 | 124.4 s | 18.2 s | 155 s |

Every marked part came from cache after the comment-only edit, and the
roof colour edit rebuilt only `roof_cladding`. That edit's run measured
171 s, but every part in it, changed or not, ran 40–70% slower than in the
other runs, so it is left out as host contention.

Where the design phase goes with a warm cache:

- **Registering builders, about 43 s, never cached.** `floor_assembly`,
  `roof_ceiling_purlins`, `gable_cladding_base_girts` and the three
  `portal_frame` calls register managed members, so they are `uncacheable`
  (`component_replay_ineligible_session_side_effect`) and rebuild every time.
- **Cacheable builders: about 7 s to build, about 2 s to load.** That is
  the most caching saves on this design today: about 5 s.
- **cadcache overhead, about 17 s.** Key computation takes about 5 s for 27
  calls (`code_hash` about 4 s). cadcache's file-read audit hook, installed on
  the first part call and never removed, received 62 million
  `object.__getattr__` events from the provenance tracer's frame reads. Its
  cost is about 12 s (26 s measured under an event counter, which adds its
  own cost).
- **Unmarked top-level code, about 60 s, and export, 18 s.** Caching does
  not touch these.

### After removing the audit hook

cadcache now wraps `open()`, `io.open()` and `os.open()` only while a part is
building, instead of installing a process-wide audit hook. Same shed, same 19
builders, each round a baseline then a warm-cache compile:

| Round | No cache: design | Cache, nothing changed: design | Compile wall, no cache vs cache |
| --- | --- | --- | --- |
| 1 | 108.9 s | 105.4 s | 144 s vs 136 s |
| 2 | 111.4 s | 106.2 s | 147 s vs 137 s |

A warm cache is now 3.5–5 s faster than no cache, against 17 s slower
before, and the whole compile about 6% faster. One more round was discarded
as host contention: its cached run's export (unaffected by caching) took
26.8 s instead of 18 s.

### With registration replay (cadcache 0.3.0)

Registering builders are now cached: their registrations are recorded with the
entry and replayed on a hit. 23 builders marked, including `shed_building`
itself, `long_wall_stud_frames` and `portal_frame`. One run of each:

| Scenario | Design phase | Export | Compile wall | Artifacts vs no cache |
| --- | --- | --- | --- | --- |
| No cache (today) | 109.8 s | 18.0 s | 144.6 s | (reference) |
| Cache, empty | 144.5 s | 16.9 s | 171.0 s | identical |
| Cache, nothing changed | 10.4 s | 16.9 s | 35.6 s | identical |
| Cache, comment-only edit | 10.4 s | 16.8 s | 35.3 s | identical |
| Cache, roof colour edit | 115.7 s | 16.8 s | 141.6 s | identical |

"Identical" means byte-identical `compiled_design`, `structural`,
`procurement`, `drawing` and `bounds` artifacts. Bounds are rounded to a
nanometre, because geometry read back from an entry differs from a fresh
build in the last bits of a double.

An unchanged or comment-only compile is about 4× faster: the whole shed is
one cache hit. A one-part edit is not faster yet. The edit rebuilds
`shed_building` around it, and eight structural builders inside it are still
refused, because of how the shed's builders share live objects:

| Refusal | Builders | Cause |
| --- | --- | --- |
| `component_replay_ineligible_external_connection` | `door_frame_assembly`, `gable_cladding_base_girts`, `gable_end_cross_bracing`, `roof_plane_cross_bracing`, `rear_gable_window_frame` | connect their components to components registered by another builder |
| `component_replay_ineligible_foreign_registration` | `long_wall_stud_frames`, `roof_ceiling_purlins` | their result contains components another builder registered |
| `component_replay_ineligible_registration_outside_result` | `portal_frame` (x3) | registers components (base anchors, connectors) its caller places, and hands live shapes to other builders through attributes such as `tertius_members` and `tertius_base_anchors` |

### Fast one-part edits (cadcache 0.4.0, restructured shed)

Two changes removed those refusals:

- **Connections resolve outside components by mark.** A cached part may
  connect to a component registered elsewhere if that component has a mark;
  a hit finds it again in the session. If it is missing, `on_hit` returns
  False and the part is built instead.
- **Builders hand out references, not live shapes.** `tertius.refer(shape,
  name, value)` stores components by mark (`tertius_refs`, kept with the
  entry) and sets `shape.<name>` to a view that looks them up in the
  session on read, so `portal.tertius_members["left_column"]` works the same
  after a hit. A part whose result still carries attributes a hit can't give
  back (`component_replay_ineligible_live_attribute`) is built, not stored.

The shed then needed three changes on its `incremental-compile-structure`
branch: its 20 handle assignments became `refer(...)` calls, each portal
keeps the base hardware it registers in a "Portal base hardware"
sub-compound, and `lysaght_zc_physical_connection` no longer moves a
connector that already belongs to another assembly. All its components and
connections, every projection, its volume and its bounds are unchanged.
`unmanaged_geometry` differs only in labels and order, and the graph no
longer lists empty compounds as unmanaged geometry.

Every builder is then cacheable. Restructured shed, one run each; "first
time" means the edited state was not in the cache yet:

| Change | No cache | Cached | Rebuilt |
| --- | --- | --- | --- |
| Nothing, or a comment | 145 s | 35–42 s | nothing |
| Roof colour (first time) | 146.5 s | 60.1 s | `roof_cladding`, `shed_building` |
| Purlin ordered length (first time) | 144.3 s | 67.2 s | `roof_ceiling_purlins`, `roof_plane_cross_bracing`, `shed_building` |
| Door jamb length (first time) | 146.0 s | 70.8 s | `door_frame_assembly`, `gable_cladding_base_girts`, `gable_end_cross_bracing`, `shed_building` |
| Floor ledger length (first time) | 147.9 s | 73.9 s | `floor_assembly`, `shed_building` |
| Back to a state compiled before | ~146 s | 36–38 s | nothing |

Each cached result's workbench artifacts are byte-identical to a no-cache
build of the same edited design.

### Deferred saves (cadcache 0.5.0)

Writing a new entry is not needed for the compile's result, only for the
next compile. With `defer_saves`, a new build returns the shape it built
(no write and read-back of its entry), the entry is snapshotted when the part
returns, and the sandbox writes entries after its outputs: it writes a
`tertius-result-ready` marker, then flushes. The worker publishes the result
at the marker, then waits for the flush and uploads the new entries.

| Restructured shed, own `@part` marks | Before | Result ready | Cache write after |
| --- | --- | --- | --- |
| Empty cache | 181.6 s | 144.7 s | 37.2 s |
| Nothing changed | 35.6 s | 43.2 s (busy host; export 19.8 s) | 1.3 s |
| Roof colour (first time) | 59.6 s | 45.7 s | 21.4 s |

Artifacts are identical to a no-cache build in every scenario.

What remains of a one-part edit: about 18 s of whole-model GLB export, patch
and dedup, `shed_building`'s own code, and loading the other parts.
Per-component GLB output (#314/#315) would remove most of the export cost
from edits.

### dincase (`dincase/tertius-project`)

8 module-level builders marked (`roller`, `mount` and `case` parts). Single runs.

| Scenario | Design phase | All phases after startup |
| --- | --- | --- |
| No cache (today) | 4.0 s | 4.9 s |
| Cache, empty (first compile) | 4.5 s | 5.4 s |
| Cache, nothing changed | 3.3 s | 4.1 s |
| Cache, edit in `roller.py` | 3.4 s | 4.2 s |

The `roller.py` edit (`BED_CHAMFER`) rebuilt only `build_roller`. The first
compile's 8.9 s startup was a one-off warm-up and is excluded.

### Correctness findings

- **Stale part.** Changing the track roller's colour in `specs.json` left
  `build_roller` as a cache hit. The colour reaches the part as
  `ROLLER_REQ.colour`, an attribute of a `Spec` object that cadcache cannot
  key, so the change goes unnoticed and the viewer would show the old colour.
  No warning reached the compile output.
- **Failed compile.** Marking `shelf_rail.build_rail`, which takes a `Spec`
  argument, fails the whole compile with cadcache's unkeyable-argument
  `TypeError`.

### What would make it faster

1. Replay registrations on a hit: record the components and connections a
   part registers and re-register them when it loads, so registering builders
   become cacheable. This is the 43 s on the shed. Estimated design phase
   about 60 s and compile about 95 s against 145 s. That is an estimate, not
   a measurement.
2. Remove cadcache's process-wide audit hook (about 12 s). Done; see above.
3. Hash each function's code once per run instead of on every part call
   (about 5 s).
4. Per-component GLB artifacts (#314/#315) for the 18 s export.

With item 2 done the shed is slightly faster with a warm cache. Item 3 adds a
few seconds more. Item 1 is what makes caching worth it here.

## Validation

Run on 2026-10-28 inside the production API image with this branch and
cadcache `v0.5.0`:

- cadcache: 56 unit tests, including effects replay, nested parts, refused
  builds and a re-parented child that used to fail to load.
- Server and Tertius suites that do not need Postgres: 299 tests
  (`test_compile_*`, `test_component_cache*`, `test_component_replay.py`,
  `test_glb_dedup.py`, `test_tertius_*`, `tests/structural`), including an
  end-to-end sandbox test where cached components and a connection are
  replayed with an identical mechanical graph. Run without `conftest.py`, so
  the Postgres-backed tests have not run yet; they run in CI on a PR.
- `mypy`: no issues in 158 files.
- Reference shed: workbench artifacts byte-identical to a no-cache compile in
  every cached scenario (Measurements).
- Not yet run: `scripts/harness-k3s.sh live-flow` with
  `LIVE_FLOW_COMPILE_ONLY=true`, the worker's tenant-scoped NATS store
  against a live JetStream, and `scripts/test-deployment-config.sh`.
