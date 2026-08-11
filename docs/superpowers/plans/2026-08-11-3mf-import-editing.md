# 3MF Import And Faceted Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let authenticated users import a 3MF from Generate Design or Intus into a new project, convert it once into unit-normalized faceted BREP geometry, and transform, assemble, or boolean-edit valid imported solids through the existing Build123D/AI/compile/viewer workflow.

**Architecture:** The API streams the original 3MF into immutable tenant-scoped project-asset storage and a digest-addressed JetStream object bucket, then queues an isolated conversion job. The worker validates and converts the source to BREP plus a bounded manifest; compile jobs snapshot and hydrate those derived assets, while the AI receives only safe manifest metadata. Both frontend surfaces reuse one accessible import dialog and hand a successful project into the existing GLB compile and Extus viewer lifecycle.

**Tech Stack:** React 19, TypeScript, Vite/Vitest, FastAPI, Pydantic, SQLAlchemy/Alembic, PostgreSQL, NATS JetStream/KEDA, Python 3.14, Build123D 0.8.0, py-lib3mf 2.3.1, Helm/local k3s, Docker Compose parity.

**Design:** [`../specs/2026-08-11-3mf-import-editing-design.md`](../specs/2026-08-11-3mf-import-editing-design.md)

---

## File Structure

### New backend files

- `server/migrations/versions/0011_project_3mf_imports.py`: asset/import/compile-asset tables and tenant-safe constraints.
- `server/core/project_assets.py`: constants, safe metadata DTOs, manifest schema, upload/archive limits, and generated-source template.
- `server/core/object_store.py`: digest-addressed JetStream object bucket adapter.
- `server/core/import_3mf_messages.py`: versioned import command, progress, result, and object-reference contracts.
- `server/workflows/intus/import_3mf_converter.py`: bounded 3MF-to-BREP conversion and unit normalization.
- `server/workflows/intus/import_3mf_job.py`: one-shot request consumer and result publisher.
- `server/workflows/intus/import_3mf_result_consumer.py`: API-side result application and reconciliation.
- `server/core/tertius_imports.py`: compile-runtime BREP/manifest loader exposed to design scripts.
- `server/start-import-3mf-job.sh`: one-shot worker entrypoint.
- `server/tests/fixtures/three_mf.py`: deterministic synthetic 3MF fixture builders.
- `server/tests/test_project_assets.py`: domain and manifest tests.
- `server/tests/test_object_store.py`: object-store adapter tests.
- `server/tests/test_import_3mf_messages.py`: message contract tests.
- `server/tests/test_import_3mf_converter.py`: conversion, limits, units, solids/shells, and boolean tests.
- `server/tests/test_import_3mf_job.py`: worker ACK/NAK/result behavior.
- `server/tests/test_import_3mf_result_consumer.py`: transactional result application.
- `server/tests/test_import_3mf_api.py`: authenticated upload/status/retry tests.
- `server/tests/test_tertius_imports.py`: runtime loader and integrity tests.

### New frontend files

- `ui/src/workflows/shared/ui/Import3mfDialog.tsx`: shared accessible import flow.
- `ui/src/workflows/shared/ui/Import3mfDialog.test.tsx`: upload, polling, warning, activation, retry, and accessibility tests.

### Existing files to modify

- `server/core/models.py`, `server/core/repositories.py`, `server/core/config.py`, `server/core/nats_client.py`: persistence, settings, and messaging infrastructure.
- `server/core/compile_messages.py`, `server/core/compile_runtime.py`, `server/core/compile_sandbox.py`: immutable asset snapshots and runtime hydration.
- `server/core/llm_file_edit.py`, `server/core/pi_agent_messages.py`, `server/core/pi_agent_system_prompt.md`: bounded asset context.
- `server/workflows/intus/intus_server.py`, `server/workflows/intus/compile_job.py`, `server/workflows/intus/pi_agent_job.py`, `server/main.py`: HTTP and worker lifecycle integration.
- `ui/src/workflows/shared/projectStorage.ts`, `ui/src/workflows/generate/GenerateDesignWindow.tsx`, `ui/src/workflows/intus/ui/CompilerTab.tsx`: API adapter and shared UI entry points.
- `ui/src/api/client.ts`: preserve browser multipart boundaries while retaining JSON defaults and CSRF handling.
- `infra/charts/tertius/values.yaml`, `infra/charts/tertius/templates/configmap.yaml`, `infra/charts/tertius/templates/keda-scaledjob.yaml`, and a new import-worker template: canonical runtime wiring.
- `docker-compose.yml`, `docker-compose.parity.yml`, `scripts/check-runtime-parity.sh`, `scripts/test-deployment-config.sh`: adapter parity.
- `scripts/smoke-live-flow.sh`, `docs/harness/*.md`, `docs/configuration-and-secrets.md`: validation and operator contracts.

---

### Task 1: Define 3MF asset and manifest domain contracts

**Files:**
- Create: `server/core/project_assets.py`
- Create: `server/tests/test_project_assets.py`

- [x] **Step 1: Write failing manifest, name, limit, and source-template tests**

```python
def test_import_manifest_rejects_shell_marked_boolean_capable():
    payload = manifest_payload(shape_type="shell", boolean_capable=True)
    with pytest.raises(ValidationError):
        Import3mfManifest.model_validate(payload)


def test_safe_part_names_are_unique_and_deterministic():
    assert safe_part_names(["", "Fin Left", "Fin Left"]) == [
        "part_001",
        "fin_left",
        "fin_left_002",
    ]


def test_generated_source_uses_repo_owned_loader():
    assert generated_3mf_design_source() == (
        "import build123d as bd\n"
        "from tertius_imports import load_3mf_model\n\n"
        'imported = load_3mf_model("source")\n'
        "parts = imported.parts\n"
        "parts_by_name = imported.parts_by_name\n"
        "model = imported.compound\n"
    )
```

- [x] **Step 2: Run the focused tests and confirm the module is missing**

Run: `rtk uv run pytest -q server/tests/test_project_assets.py`

Expected: FAIL during collection because `core.project_assets` does not exist.

- [x] **Step 3: Implement exact constants, Pydantic schemas, safe naming, and source generation**

```python
MAX_3MF_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_3MF_ARCHIVE_ENTRIES = 2_048
MAX_3MF_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_3MF_XML_BYTES = 64 * 1024 * 1024
MAX_3MF_OBJECTS = 2_048
MAX_3MF_VERTICES = 10_000_000
MAX_3MF_TRIANGLES = 10_000_000
MAX_3MF_COORDINATE_MM = 1_000_000.0
MAX_3MF_MANIFEST_BYTES = 256 * 1024
MAX_3MF_DERIVED_BREP_BYTES = 512 * 1024 * 1024
IMPORT_3MF_CONVERSION_VERSION = "tertius-3mf-brep-v1-build123d-0.8.0"


class Import3mfPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: NonNegativeInt
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
    source_name: str = Field(max_length=160)
    shape_type: Literal["solid", "shell"]
    boolean_capable: bool
    is_valid: bool
    vertex_count: NonNegativeInt
    triangle_count: NonNegativeInt
    bounds_mm: Import3mfBounds

    @model_validator(mode="after")
    def solid_boolean_invariant(self):
        if self.boolean_capable != (self.shape_type == "solid" and self.is_valid):
            raise ValueError("boolean_capable must match valid solid status")
        return self
```

- [x] **Step 4: Run focused tests**

Run: `rtk uv run pytest -q server/tests/test_project_assets.py`

Expected: PASS.

- [x] **Step 5: Commit the domain contract**

```bash
rtk git add server/core/project_assets.py server/tests/test_project_assets.py
rtk git commit -m "feat: define 3mf import domain"
```

### Task 2: Add immutable asset and import-job persistence

**Files:**
- Create: `server/migrations/versions/0011_project_3mf_imports.py`
- Modify: `server/core/models.py`
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_repositories.py`
- Test: `server/tests/test_migrations.py`

- [x] **Step 1: Add failing tenant-isolation, active-job, retry, result-atomicity, and compile-snapshot repository tests**

```python
def test_project_asset_repository_is_tenant_scoped(db_session, seeded):
    asset = ProjectAssetRepository(db_session, seeded["tenant_a"]).create(
        project_id=seeded["project_a"], logical_name="source.3mf",
        display_name="falcon9.3mf", kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE, content=b"3mf", revision=1,
    )
    assert ProjectAssetRepository(db_session, seeded["tenant_b"]).get(asset.id) is None


def test_import_repository_allows_only_one_active_job(db_session, seeded):
    repo = ProjectImportRepository(db_session, seeded["tenant_a"])
    repo.create_queued(seeded["project_a"], seeded["user_a"], seeded["asset_a"])
    with pytest.raises(ActiveProjectImportError):
        repo.create_queued(seeded["project_a"], seeded["user_a"], seeded["asset_a"])
```

- [x] **Step 2: Run tests and confirm missing models/repositories**

Run: `rtk uv run pytest -q server/tests/test_repositories.py -k 'project_asset or project_import or compile_job_asset'`

Expected: FAIL because the new entities and repositories are undefined.

- [x] **Step 3: Add SQLAlchemy entities and migration**

Implement `ProjectAsset`, `ProjectImportJob`, and `CompileJobAsset` with the columns and composite foreign keys in the approved design. The migration must create a PostgreSQL partial unique index equivalent to:

```python
op.create_index(
    "uq_project_import_jobs_active_project",
    "project_import_jobs",
    ["project_id"],
    unique=True,
    postgresql_where=sa.text("status IN ('queued', 'running')"),
)
```

Store `sha256` as exactly 64 lowercase hexadecimal characters, `byte_size` as non-negative integer, and `ProjectAsset.content` as non-null `LargeBinary`. Add composite tenant/project constraints to every new cross-table relationship.

- [x] **Step 4: Implement repositories with staged transaction boundaries**

```python
class ProjectAssetRepository:
    def create(self, *, project_id, logical_name, display_name, kind,
               media_type, content, revision, conversion_version=None):
        digest = hashlib.sha256(content).hexdigest()
        row = ProjectAsset(
            tenant_id=self.tenant_id, project_id=project_id,
            logical_name=logical_name, display_name=display_name, kind=kind,
            media_type=media_type, content=content, byte_size=len(content),
            sha256=digest, revision=revision,
            conversion_version=conversion_version,
        )
        self.db.add(row)
        self.db.flush()
        return row


class ProjectImportRepository:
    def apply_success(self, *, job_id, source_sha256, brep, manifest, user_id):
        job = self.lock_job(job_id)
        if job.status not in {"queued", "running"}:
            return job
        if job.source_asset.sha256 != source_sha256:
            raise AssetIntegrityError("source digest mismatch")
        # Create derived assets, save generated design.py through the staged
        # ProjectRepository boundary, then mark succeeded before one commit.
```

- [x] **Step 5: Run repository and migration tests**

Run: `rtk uv run pytest -q server/tests/test_repositories.py server/tests/test_migrations.py`

Expected: PASS, or the repository's documented Docker/Testcontainers permission blocker before test execution; if blocked, rerun with the required Docker access.

- [x] **Step 6: Commit persistence**

```bash
rtk git add server/migrations/versions/0011_project_3mf_imports.py server/core/models.py server/core/repositories.py server/tests/test_repositories.py server/tests/test_migrations.py
rtk git commit -m "feat: persist 3mf project imports"
```

### Task 3: Add digest-addressed JetStream object storage

**Files:**
- Create: `server/core/object_store.py`
- Create: `server/tests/test_object_store.py`
- Modify: `server/core/config.py`
- Modify: `server/core/nats_client.py`
- Modify: `server/tests/test_nats_client.py`

- [x] **Step 1: Write failing adapter and bucket-configuration tests**

```python
@pytest.mark.asyncio
async def test_put_is_digest_addressed_and_idempotent(fake_object_store):
    ref = await ProjectObjectStore(fake_object_store, "TERTIUS_ASSETS").put(b"abc")
    assert ref.key == f"sha256/{hashlib.sha256(b'abc').hexdigest()}"
    assert await ProjectObjectStore(fake_object_store, "TERTIUS_ASSETS").get(ref) == b"abc"


@pytest.mark.asyncio
async def test_get_rejects_size_or_digest_mismatch(fake_object_store):
    ref = ObjectRef(bucket="TERTIUS_ASSETS", key="sha256/bad", sha256="0" * 64, byte_size=3)
    with pytest.raises(ObjectIntegrityError):
        await ProjectObjectStore(fake_object_store, "TERTIUS_ASSETS").get(ref)
```

- [x] **Step 2: Run tests to confirm missing adapter**

Run: `rtk uv run pytest -q server/tests/test_object_store.py server/tests/test_nats_client.py -k object`

Expected: FAIL because object-store helpers/settings are absent.

- [x] **Step 3: Implement object references, safe put/get, and bucket reconciliation**

```python
class ObjectRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bucket: str
    key: str
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    byte_size: NonNegativeInt


class ProjectObjectStore:
    async def put(self, content: bytes) -> ObjectRef:
        digest = hashlib.sha256(content).hexdigest()
        key = f"sha256/{digest}"
        await self.store.put(key, content)
        return ObjectRef(bucket=self.bucket, key=key, sha256=digest, byte_size=len(content))

    async def get(self, ref: ObjectRef) -> bytes:
        if ref.bucket != self.bucket:
            raise ObjectIntegrityError("unexpected bucket")
        result = await self.store.get(ref.key)
        content = bytes(result.data)
        if len(content) != ref.byte_size or hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ObjectIntegrityError("object integrity check failed")
        return content
```

Add bounded settings for bucket name, TTL/max bytes, import subjects, stream, durable consumers, ACK wait, and max delivery. Extend `ensure_*` helpers without logging keys/digests as metric labels.

- [x] **Step 4: Run object-store and NATS tests**

Run: `rtk uv run pytest -q server/tests/test_object_store.py server/tests/test_nats_client.py`

Expected: PASS.

- [x] **Step 5: Commit object transport**

```bash
rtk git add server/core/object_store.py server/core/config.py server/core/nats_client.py server/tests/test_object_store.py server/tests/test_nats_client.py
rtk git commit -m "feat: add project asset object transport"
```

### Task 4: Define import commands and implement the isolated converter

**Files:**
- Create: `server/core/import_3mf_messages.py`
- Create: `server/workflows/intus/import_3mf_converter.py`
- Create: `server/tests/fixtures/three_mf.py`
- Create: `server/tests/test_import_3mf_messages.py`
- Create: `server/tests/test_import_3mf_converter.py`

- [ ] **Step 1: Write failing message and conversion tests**

Cover all six unit factors, one/two object imports, blank/duplicate names, manifold solid, open shell, BREP round trip, simple box-minus-cylinder boolean, malformed archive, encrypted/traversal ZIP, non-finite/extreme coordinates, and every exact resource threshold.

```python
@pytest.mark.parametrize(
    ("unit", "factor"),
    [("MC", .001), ("MM", 1), ("CM", 10), ("M", 1000), ("IN", 25.4), ("FT", 304.8)],
)
def test_converter_normalizes_units(tmp_path, unit, factor):
    source = make_box_3mf(unit=unit, size=1)
    result = convert_3mf_bytes(source, tmp_path)
    assert result.manifest.scale_to_mm == factor
    assert result.manifest.parts[0].bounds_mm.max == pytest.approx((factor, factor, factor))


def test_manifold_import_supports_boolean(tmp_path):
    result = convert_3mf_bytes(make_box_3mf(size=20), tmp_path)
    model = load_brep_bytes(result.brep_bytes)
    cut = model - bd.Cylinder(2, 20)
    assert cut.is_valid
    assert cut.volume < model.volume
```

- [ ] **Step 2: Run tests and verify missing contracts/converter**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_messages.py server/tests/test_import_3mf_converter.py`

Expected: FAIL during collection.

- [ ] **Step 3: Implement strict Pydantic message contracts**

`Import3mfCommand` contains schema version, job/tenant/project/user IDs, source `ObjectRef`, conversion version, and trace state. `Import3mfResult` contains matching provenance, outcome, BREP/manifest `ObjectRef`s on success, bounded manifest summary, duration, or one bounded error code/user message on failure. Reject extra fields and any embedded `bytes`/base64 field.

- [ ] **Step 4: Implement ZIP preflight and converter**

```python
UNIT_TO_MM = {
    bd.Unit.MC: 0.001, bd.Unit.MM: 1.0, bd.Unit.CM: 10.0,
    bd.Unit.M: 1000.0, bd.Unit.IN: 25.4, bd.Unit.FT: 304.8,
}


def convert_3mf_bytes(source: bytes, workdir: Path) -> ConversionOutput:
    validate_3mf_archive(source)
    source_path = workdir / "source.3mf"
    source_path.write_bytes(source)
    mesher = bd.Mesher()
    shapes = mesher.read(source_path)
    if not shapes:
        raise Import3mfError("invalid_3mf_geometry", "The 3MF contains no mesh objects.")
    enforce_mesh_limits(mesher)
    factor = UNIT_TO_MM[mesher.model_unit]
    normalized = [shape.scale(factor) if factor != 1.0 else shape for shape in shapes]
    manifest = build_manifest(source, mesher, normalized, factor)
    compound = bd.Compound(normalized, children=normalized)
    brep_path = workdir / "source.brep"
    bd.export_brep(compound, brep_path)
    brep = brep_path.read_bytes()
    if len(brep) > MAX_3MF_DERIVED_BREP_BYTES:
        raise Import3mfError("3mf_resource_limit", "The converted model is too large.")
    manifest = manifest.model_copy(update={
        "brep_sha256": hashlib.sha256(brep).hexdigest(),
        "brep_byte_size": len(brep),
    })
    validate_brep_round_trip(brep_path, manifest)
    return ConversionOutput(brep_bytes=brep, manifest=manifest)
```

The converter runs in its own subprocess with the 300-second process-tree timeout. Capture py-lib3mf shutdown stderr separately; ignore only the exact known deallocator text after a successful validated output, and fail on other stderr when the subprocess exit status is non-zero.

- [ ] **Step 5: Run converter tests**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_messages.py server/tests/test_import_3mf_converter.py`

Expected: PASS.

- [ ] **Step 6: Commit converter**

```bash
rtk git add server/core/import_3mf_messages.py server/workflows/intus/import_3mf_converter.py server/tests/fixtures/three_mf.py server/tests/test_import_3mf_messages.py server/tests/test_import_3mf_converter.py
rtk git commit -m "feat: convert 3mf assets to faceted brep"
```

### Task 5: Implement import worker and result consumer

**Files:**
- Create: `server/workflows/intus/import_3mf_job.py`
- Create: `server/workflows/intus/import_3mf_result_consumer.py`
- Create: `server/start-import-3mf-job.sh`
- Create: `server/tests/test_import_3mf_job.py`
- Create: `server/tests/test_import_3mf_result_consumer.py`
- Modify: `server/main.py`

- [ ] **Step 1: Write failing worker lifecycle tests**

Test source fetch/digest verification, running transition, heartbeats, success refs, conversion error mapping, timeout, result publish retry, ACK only after publish, NAK on transient object-store/NATS failure, idempotent duplicate result, provenance mismatch, and atomic derived-asset/source creation.

- [ ] **Step 2: Run focused tests and confirm missing workers**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_job.py server/tests/test_import_3mf_result_consumer.py`

Expected: FAIL during collection.

- [ ] **Step 3: Implement one-shot worker using existing compile/Pi patterns**

```python
async def execute_import_command(command, object_store, publisher, settings):
    source = await object_store.get(command.source)
    output = await asyncio.to_thread(
        run_converter_subprocess, source, settings.import_3mf_timeout_seconds
    )
    brep_ref = await object_store.put(output.brep_bytes)
    manifest_bytes = output.manifest.model_dump_json().encode("utf-8")
    manifest_ref = await object_store.put(manifest_bytes)
    return Import3mfResult.success_for(
        command, brep=brep_ref, manifest=manifest_ref,
        summary=output.manifest.public_summary(),
    )
```

Use the existing trace propagation, bounded telemetry, heartbeat, result-publish retry, and ACK/NAK conventions. Never log source/derived bytes, project/user IDs, object keys, filenames, or raw metadata.

- [ ] **Step 4: Implement result consumer and stale-job reconciliation**

Fetch BREP and manifest by reference, verify both, parse the manifest, call `ProjectImportRepository.apply_success`, and persist generated source atomically. Map terminal errors through a fixed code-to-user-message table. Reconcile stale running jobs to retryable failure after the configured lease.

- [ ] **Step 5: Run focused pipeline tests**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_job.py server/tests/test_import_3mf_result_consumer.py`

Expected: PASS.

- [ ] **Step 6: Commit workers**

```bash
rtk git add server/workflows/intus/import_3mf_job.py server/workflows/intus/import_3mf_result_consumer.py server/start-import-3mf-job.sh server/tests/test_import_3mf_job.py server/tests/test_import_3mf_result_consumer.py server/main.py
rtk git commit -m "feat: run asynchronous 3mf imports"
```

### Task 6: Add authenticated streaming import/status/retry API

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Create: `server/tests/test_import_3mf_api.py`
- Modify: `server/tests/test_intus_endpoints.py`

- [ ] **Step 1: Write failing authenticated API tests**

Test guest/unauthenticated rejection, valid multipart upload, exact 128 MiB boundary, suffix/content-type rules, invalid project name, collision rollback, source/object-store/publish transaction, status tenant scope, safe response shape, failed retry, concurrent retry conflict, and no binary/object key leakage.

```python
def test_import_3mf_creates_new_project_and_queues(authenticated_client, make_3mf):
    response = authenticated_client.post(
        "/projects/imports/3mf",
        data={"project_name": "falcon9"},
        files={"file": ("falcon9.3mf", make_3mf(), THREE_MF_MEDIA_TYPE)},
    )
    assert response.status_code == 202
    assert response.json().keys() == {"success", "job_id", "project_name", "status"}
    assert response.json()["status"] == "queued"
```

- [ ] **Step 2: Run API tests and verify 404/missing behavior**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_api.py`

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Implement chunked upload and job endpoints**

Use FastAPI `UploadFile` and read no more than 1 MiB per iteration. Roll back project/asset/job rows if object-store put or command publish fails before the request returns. Return only public DTO fields. The retry route reuses the immutable source asset and increments attempt under a row lock.

- [ ] **Step 4: Run endpoint tests**

Run: `rtk uv run pytest -q server/tests/test_import_3mf_api.py server/tests/test_intus_endpoints.py`

Expected: PASS.

- [ ] **Step 5: Commit API**

```bash
rtk git add server/workflows/intus/intus_server.py server/tests/test_import_3mf_api.py server/tests/test_intus_endpoints.py
rtk git commit -m "feat: expose authenticated 3mf import api"
```

### Task 7: Snapshot and hydrate derived assets in compile jobs

**Files:**
- Modify: `server/core/compile_messages.py`
- Modify: `server/core/compile_runtime.py`
- Modify: `server/core/repositories.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/workflows/intus/compile_job.py`
- Modify: `server/tests/test_compile_messages.py`
- Modify: `server/tests/test_compile_job.py`
- Modify: `server/tests/test_compile_flow.py`
- Modify: `server/tests/test_compile_pipeline_e2e.py`

- [ ] **Step 1: Write failing immutable asset-snapshot tests**

```python
def test_compile_command_uses_object_refs_not_binary():
    command = compile_command_with_import_assets()
    encoded = command.model_dump_json()
    assert "source.brep" in encoded
    assert "source.manifest.json" in encoded
    assert "base64" not in encoded
    assert "3mf-bytes" not in encoded


def test_compile_job_asset_snapshot_survives_later_import_retry(db_session, imported_project):
    first = snapshot_compile_assets(imported_project)
    create_new_derived_revision(imported_project)
    assert snapshot_compile_assets_for_job(first.job_id)[0].sha256 == first.sha256
```

- [ ] **Step 2: Run focused compile tests and confirm missing asset contract**

Run: `rtk uv run pytest -q server/tests/test_compile_messages.py server/tests/test_compile_job.py server/tests/test_compile_flow.py`

Expected: FAIL on missing `assets` and snapshot behavior.

- [ ] **Step 3: Add compile asset references and hydration**

```python
class CompileAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: Literal["source.brep", "source.manifest.json"]
    object_ref: ObjectRef


def hydrate_project_assets(root: Path, assets: list[CompileAsset], get_object):
    assets_root = root / ".tertius" / "imports" / "source"
    assets_root.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        target = assets_root / asset.filename
        target.write_bytes(get_object(asset.object_ref))
```

At compile submission, lock the project, select only the latest successful BREP/manifest pair, create `CompileJobAsset` links, ensure each durable blob is present in the object bucket, and build the small command refs. The worker fetches and verifies bytes before starting the existing sandbox subprocess.

- [ ] **Step 4: Run compile tests including E2E**

Run: `rtk uv run pytest -q server/tests/test_compile_messages.py server/tests/test_compile_job.py server/tests/test_compile_flow.py server/tests/test_compile_pipeline_e2e.py`

Expected: PASS.

- [ ] **Step 5: Commit compile asset integration**

```bash
rtk git add server/core/compile_messages.py server/core/compile_runtime.py server/core/repositories.py server/workflows/intus/intus_server.py server/workflows/intus/compile_job.py server/tests/test_compile_messages.py server/tests/test_compile_job.py server/tests/test_compile_flow.py server/tests/test_compile_pipeline_e2e.py
rtk git commit -m "feat: hydrate imported assets for compile jobs"
```

### Task 8: Expose the BREP through the sandbox runtime helper

**Files:**
- Create: `server/core/tertius_imports.py`
- Create: `server/tests/test_tertius_imports.py`
- Modify: `server/core/compile_sandbox.py`
- Modify: `server/tests/test_compile_sandbox.py`

- [ ] **Step 1: Write failing loader and sandbox tests**

Test matching/mismatched source digest, BREP/manifest child count, safe labels, duplicate names, ordered parts, `parts_by_name`, solid flags, compound discovery, transform compile, simple solid boolean compile, shell-only compile, and user-facing translation for shell boolean failure.

- [ ] **Step 2: Run tests and verify loader is absent**

Run: `rtk uv run pytest -q server/tests/test_tertius_imports.py server/tests/test_compile_sandbox.py -k '3mf or imported'`

Expected: FAIL during loader import or asset lookup.

- [ ] **Step 3: Implement the runtime wrapper**

```python
@dataclass(frozen=True)
class Imported3mfPart:
    index: int
    name: str
    shape: bd.Shape
    is_solid: bool
    boolean_capable: bool


@dataclass(frozen=True)
class Imported3mfModel:
    parts: tuple[Imported3mfPart, ...]
    parts_by_name: Mapping[str, Imported3mfPart]
    compound: bd.Compound
    manifest: Import3mfManifest


def load_3mf_model(name: str) -> Imported3mfModel:
    if name != "source":
        raise ValueError("unknown imported model")
    root = Path(os.environ["TERTIUS_IMPORT_ASSET_ROOT"])
    manifest = Import3mfManifest.model_validate_json((root / "source.manifest.json").read_text())
    compound = bd.import_brep(root / "source.brep")
    restored_parts = tuple(compound.first_level_shapes()) if isinstance(compound, bd.Compound) else (compound,)
    if len(restored_parts) != len(manifest.parts):
        raise RuntimeError("imported asset manifest mismatch")
    # Validate and relabel topology, then rebuild with:
    # bd.Compound(restored_parts, children=restored_parts)
```

Set `TERTIUS_IMPORT_ASSET_ROOT` to the hydrated read-only path only for the sandbox subprocess. Do not expose database/NATS credentials or arbitrary host paths.

- [ ] **Step 4: Run loader and sandbox tests**

Run: `rtk uv run pytest -q server/tests/test_tertius_imports.py server/tests/test_compile_sandbox.py`

Expected: PASS.

- [ ] **Step 5: Commit runtime loading**

```bash
rtk git add server/core/tertius_imports.py server/core/compile_sandbox.py server/tests/test_tertius_imports.py server/tests/test_compile_sandbox.py
rtk git commit -m "feat: load faceted imports in build123d scripts"
```

### Task 9: Add privacy-safe imported-asset context to AI edits

**Files:**
- Modify: `server/core/llm_file_edit.py`
- Modify: `server/core/pi_agent_messages.py`
- Modify: `server/core/pi_agent_system_prompt.md`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/workflows/intus/pi_agent_job.py`
- Modify: `server/tests/test_llm_file_edit.py`
- Modify: `server/tests/test_pi_agent_messages.py`
- Modify: `server/tests/test_pi_agent_job.py`
- Modify: `server/tests/test_pi_agent_prompt.py`

- [ ] **Step 1: Write failing safe-context tests**

```python
def test_asset_context_contains_safe_manifest_only(import_manifest):
    context = build_import_asset_context(import_manifest)
    assert "source.3mf" in context
    assert "part_001" in context
    assert "boolean_capable=true" in context
    assert import_manifest.source_sha256 not in context
    assert "metadata" not in context.lower()
    assert len(context) <= 16_384


def test_pi_command_never_contains_binary_asset(imported_edit_command):
    payload = imported_edit_command.model_dump_json()
    assert ".brep" not in payload
    assert "UEsDB" not in payload
```

- [ ] **Step 2: Run focused AI tests and confirm missing context**

Run: `rtk uv run pytest -q server/tests/test_llm_file_edit.py server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_job.py server/tests/test_pi_agent_prompt.py`

Expected: FAIL on absent `asset_context`/prompt contract.

- [ ] **Step 3: Implement bounded context and guardrails**

Add a maximum 16,384-character plain-text context containing only source display basename, normalized unit, conversion version, part index/name, shape type, boolean flag, and rounded bounds. Persist it in the LLM job request for retries and add it to `PiAgentCommand`. The worker includes it as repo-owned context, not editable workspace content.

Append these prompt rules:

```markdown
- Imported 3MF geometry is loaded with `load_3mf_model("source")`.
- Preserve the loader and immutable source relationship unless the user explicitly asks to replace the imported model.
- Use `part.shape` for Build123D operations.
- Perform union, subtraction, intersection, split, or other solid-only operations only when `part.boolean_capable` is true.
- Never claim that faceted imports contain original sketches, dimensions, constraints, fillets, materials, textures, or feature history.
```

- [ ] **Step 4: Run AI boundary tests**

Run: `rtk uv run pytest -q server/tests/test_llm_file_edit.py server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_job.py server/tests/test_pi_agent_prompt.py`

Expected: PASS.

- [ ] **Step 5: Commit AI integration**

```bash
rtk git add server/core/llm_file_edit.py server/core/pi_agent_messages.py server/core/pi_agent_system_prompt.md server/workflows/intus/intus_server.py server/workflows/intus/pi_agent_job.py server/tests/test_llm_file_edit.py server/tests/test_pi_agent_messages.py server/tests/test_pi_agent_job.py server/tests/test_pi_agent_prompt.py
rtk git commit -m "feat: describe imported assets to ai edits"
```

### Task 10: Make the browser client multipart-safe and extend project storage

**Files:**
- Modify: `ui/src/api/client.ts`
- Modify: `ui/src/api/client.test.ts`
- Modify: `ui/src/workflows/shared/projectStorage.ts`
- Modify: `ui/src/workflows/shared/projectStorage.test.ts`

- [ ] **Step 1: Write failing authenticated and guest adapter tests**

```typescript
it('preserves the browser multipart boundary for FormData mutations', async () => {
  const body = new FormData()
  body.append('project_name', 'falcon9')
  await apiFetch('/projects/imports/3mf', getAccessToken, { method: 'POST', body })
  const headers = new Headers(fetchMock.mock.calls[0][1]?.headers)
  expect(headers.has('Content-Type')).toBe(false)
  expect(headers.get('X-CSRF-Token')).toBeTruthy()
})

it('uploads 3mf as multipart without reading it as text', async () => {
  const file = new File([new Uint8Array([0x50, 0x4b])], 'falcon9.3mf', { type: THREE_MF_MEDIA_TYPE })
  await storage.import3mf(file, 'falcon9')
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/intus/projects/imports/3mf',
    expect.objectContaining({ method: 'POST', body: expect.any(FormData) }),
  )
})

it('rejects guest 3mf imports locally', async () => {
  await expect(guestStorage.import3mf(file, 'falcon9')).rejects.toThrow('Log in to import 3MF models')
})
```

- [ ] **Step 2: Run tests and verify missing methods**

Run: `rtk npm --prefix ui test -- --run src/api/client.test.ts src/workflows/shared/projectStorage.test.ts`

Expected: FAIL TypeScript/test assertions because import methods are absent.

- [ ] **Step 3: Add exact frontend types and REST methods**

```typescript
export type Import3mfJobStatus = {
  job_id: string
  project_name: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  progress?: { stage: string; percent?: number }
  warnings: string[]
  error_code?: string
  user_message?: string
  retryable?: boolean
  manifest?: Import3mfManifestSummary
}

import3mf: (file: File, projectName: string) => Promise<Import3mfJobStatus>
getImport3mfJob: (jobId: string) => Promise<Import3mfJobStatus>
retryImport3mfJob: (jobId: string) => Promise<Import3mfJobStatus>
```

Use `FormData`; do not set `Content-Type` manually; let the browser create the multipart boundary. Route all mutations through existing cookie/CSRF `apiFetch`.

In `apiFetch`, retain the existing JSON default only for non-FormData bodies:

```typescript
if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
  headers.set('Content-Type', 'application/json')
}
```

- [ ] **Step 4: Run storage tests**

Run: `rtk npm --prefix ui test -- --run src/api/client.test.ts src/workflows/shared/projectStorage.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit browser API adapter**

```bash
rtk git add ui/src/api/client.ts ui/src/api/client.test.ts ui/src/workflows/shared/projectStorage.ts ui/src/workflows/shared/projectStorage.test.ts
rtk git commit -m "feat: add browser 3mf import api"
```

### Task 11: Build the shared accessible import dialog

**Files:**
- Create: `ui/src/workflows/shared/ui/Import3mfDialog.tsx`
- Create: `ui/src/workflows/shared/ui/Import3mfDialog.test.tsx`

- [ ] **Step 1: Write failing interaction/accessibility tests**

Cover authenticated trigger, `.3mf` accept filter, drag/drop, safe name suggestion, invalid suffix, duplicate submission lock, upload state, conversion polling with hidden/inactive pause, live announcements, warnings, success callback, stale navigation suppression, failure retry, cancel, Escape, focus trap/return, and no guest trigger.

```tsx
it('announces conversion and activates only while tracking the job', async () => {
  render(<Import3mfDialog storage={storage} onImported={onImported} isActive />)
  await user.click(screen.getByRole('button', { name: 'Import 3MF' }))
  await user.upload(screen.getByLabelText('3MF file'), file)
  await user.click(screen.getByRole('button', { name: 'Create project' }))
  expect(await screen.findByRole('status')).toHaveTextContent('Converting 3MF')
  await waitFor(() => expect(onImported).toHaveBeenCalledWith(expect.objectContaining({ project_name: 'falcon9' })))
})
```

- [ ] **Step 2: Run dialog tests and confirm component missing**

Run: `rtk npm --prefix ui test -- --run src/workflows/shared/ui/Import3mfDialog.test.tsx`

Expected: FAIL during import.

- [ ] **Step 3: Implement the intentional shared UI**

Use the existing Tertius visual language: compact project-control trigger, a clear centered dialog, filename drop zone, visible faceted-geometry limitation, millimetre normalization note, inline errors, warning panel, and a determinate bar only when the server supplies a percentage. Use native `<dialog>` when compatible with the test/runtime harness, labelled controls, `aria-live="polite"`, and focus return. Do not introduce a new UI framework.

- [ ] **Step 4: Run dialog tests, typecheck, and lint**

Run: `rtk npm --prefix ui test -- --run src/workflows/shared/ui/Import3mfDialog.test.tsx`

Run: `rtk npm --prefix ui run typecheck`

Run: `rtk npm --prefix ui run lint`

Expected: PASS.

- [ ] **Step 5: Commit dialog**

```bash
rtk git add ui/src/workflows/shared/ui/Import3mfDialog.tsx ui/src/workflows/shared/ui/Import3mfDialog.test.tsx
rtk git commit -m "feat: add shared 3mf import dialog"
```

### Task 12: Integrate both surfaces and compile/viewer handoff

**Files:**
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`
- Modify: `ui/src/workflows/intus/ui/CompilerTab.tsx`
- Modify: `ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx`
- Modify: `ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx`

- [ ] **Step 1: Write failing Generate Design and Intus integration tests**

Verify both authenticated surfaces render the same trigger, guests do not, success activates the new project, dispatches `tertius:active-project-changed`, loads `design.py`, starts one GLB compile, polls it, displays the linked artifact, and does not activate after the user changes project mid-conversion.

- [ ] **Step 2: Run surface tests and verify missing integration**

Run: `rtk npm --prefix ui test -- --run src/workflows/generate/GenerateDesignWindow.test.tsx src/workflows/intus/ui/CompilerTab.compile.test.tsx src/workflows/intus/ui/CompilerTab.guest.test.tsx`

Expected: FAIL on missing trigger/handoff.

- [ ] **Step 3: Mount the shared component without duplicating lifecycle logic**

Generate Design places the trigger beside `ProjectSelector` in the conversation controls. Intus replaces the misleading binary-capable use of the existing text **Upload** with separate **Upload Python** and **Import 3MF** actions. Both pass the same `ProjectStorage`, auth state, and active-state polling flag.

On success:

```typescript
await storage.activateProject(result.project_name)
window.dispatchEvent(new CustomEvent('tertius:active-project-changed', {
  detail: { projectName: result.project_name },
}))
await compileImportedProject(result.project_name, { format: 'glb', quality: 'sketch' })
```

Reuse each surface's existing compile/poll/artifact methods; do not add a third compile client.

- [ ] **Step 4: Run focused and full UI verification**

Run: `rtk npm --prefix ui test -- --run src/workflows/generate/GenerateDesignWindow.test.tsx src/workflows/intus/ui/CompilerTab.compile.test.tsx src/workflows/intus/ui/CompilerTab.guest.test.tsx`

Run: `rtk npm --prefix ui test -- --run`

Run: `rtk npm --prefix ui run build`

Expected: PASS.

- [ ] **Step 5: Commit surface integration**

```bash
rtk git add ui/src/workflows/generate/GenerateDesignWindow.tsx ui/src/workflows/generate/GenerateDesignWindow.test.tsx ui/src/workflows/intus/ui/CompilerTab.tsx ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx
rtk git commit -m "feat: import 3mf from generate and intus"
```

### Task 13: Wire canonical and adapter runtimes

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Create: `infra/charts/tertius/templates/import-3mf-worker.yaml`
- Modify: `infra/charts/tertius/templates/keda-scaledjob.yaml`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.parity.yml`
- Modify: `scripts/check-runtime-parity.sh`
- Modify: `scripts/test-deployment-config.sh`
- Modify: `server/.env.example`
- Modify: `docs/configuration-and-secrets.md`
- Modify: `docs/harness/runtime-parity.md`

- [ ] **Step 1: Add failing render/parity assertions**

Require identical import subject/stream/object-bucket names and all limits in API, result consumer, import worker, and compile worker. Assert k3s worker controls: non-root, dropped capabilities, read-only root, no token, network isolation, configured timeout/deadline, 8 GiB memory, 4 CPU, 4 GiB ephemeral storage, and 1 GiB `/tmp`. Document Compose isolation differences.

- [ ] **Step 2: Run config tests and observe missing wiring**

Run: `rtk scripts/test-deployment-config.sh`

Run: `rtk scripts/check-runtime-parity.sh`

Expected: FAIL on missing 3MF settings/worker.

- [ ] **Step 3: Add Helm/KEDA/Compose wiring**

Use separate request/result subjects and durable consumers under one bounded import stream. Ensure the JetStream asset bucket is available before API/import/compile workers start. Reuse the compile image and security context for the import worker, but use `server/start-import-3mf-job.sh` as its command. Never put object keys, source names, or digests in pod labels.

- [ ] **Step 4: Run render, deployment, and parity checks**

Run: `rtk scripts/test-deployment-config.sh`

Run: `rtk scripts/check-runtime-parity.sh`

Run: `rtk helm template tertius infra/charts/tertius --namespace tertius`

Expected: PASS/render succeeds.

- [ ] **Step 5: Commit runtime wiring**

```bash
rtk git add infra/charts/tertius server/.env.example docker-compose.yml docker-compose.parity.yml scripts/check-runtime-parity.sh scripts/test-deployment-config.sh docs/configuration-and-secrets.md docs/harness/runtime-parity.md
rtk git commit -m "feat: deploy isolated 3mf import workers"
```

### Task 14: Extend harness validation and user documentation

**Files:**
- Modify: `scripts/smoke-live-flow.sh`
- Modify: `docs/harness/local-harness.md`
- Modify: `docs/harness/browser-validation.md`
- Modify: `docs/harness/quality-gates.md`
- Create: `docs/importing-3mf.md`

- [ ] **Step 1: Add a synthetic non-provider smoke mode**

Add `LIVE_FLOW_3MF_PATH` support that authenticates, imports by multipart, polls the import, compiles generated source, verifies GLB bytes, optionally performs the existing real AI edit, then verifies the linked post-edit compile. Print only stage/status and bounded counts; never dump source, manifest, tokens, or asset identifiers.

- [ ] **Step 2: Add exact operator/user documentation**

Document both entry points, new-project behavior, faceted solid/shell semantics, unit normalization, boolean limitations, resource limits, retry, privacy boundary, canonical isolated validation, and Compose limitations. Include the exact harness invocation:

```bash
LIVE_FLOW_3MF_PATH=/tmp/falcon9_200mm.3mf \
LIVE_FLOW_VERIFY_CONVERSATION=true \
scripts/harness-k3s.sh live-flow
```

- [ ] **Step 3: Run shell/docs checks**

Run: `rtk bash -n scripts/smoke-live-flow.sh`

Run: `rtk scripts/check-runtime-parity.sh`

Expected: PASS.

- [ ] **Step 4: Commit harness/docs**

```bash
rtk git add scripts/smoke-live-flow.sh docs/harness/local-harness.md docs/harness/browser-validation.md docs/harness/quality-gates.md docs/importing-3mf.md
rtk git commit -m "docs: validate the 3mf import workflow"
```

### Task 15: Run full verification and the supplied Falcon 9 flow

**Files:**
- Modify only if a failing test reveals a spec ambiguity; update the design first, then implementation.
- Validation input: `/tmp/falcon9_200mm.3mf` downloaded from the user-supplied URL; do not commit.

- [ ] **Step 1: Acquire and fingerprint the exact sample**

Run:

```bash
rtk proxy curl -fSLo /tmp/falcon9_200mm.3mf http://100.86.195.45:8000/outputs/falcon9/final/falcon9_200mm.3mf
rtk sha256sum /tmp/falcon9_200mm.3mf
rtk stat -c '%s bytes' /tmp/falcon9_200mm.3mf
```

Expected: download succeeds; record non-zero size and 64-character SHA-256 in validation notes.

- [ ] **Step 2: Run focused backend quality gates**

Run: `rtk uv run ruff check server/core server/workflows/intus server/tests`

Run: `rtk uv run mypy server/core server/workflows/intus`

Run: `rtk uv run pytest -q server/tests/test_project_assets.py server/tests/test_object_store.py server/tests/test_import_3mf_messages.py server/tests/test_import_3mf_converter.py server/tests/test_import_3mf_job.py server/tests/test_import_3mf_result_consumer.py server/tests/test_import_3mf_api.py server/tests/test_tertius_imports.py`

Expected: PASS.

- [ ] **Step 3: Run complete backend/frontend/config gates**

Run: `rtk uv run pytest -q server/tests`

Run: `rtk npm --prefix ui test -- --run`

Run: `rtk npm --prefix ui run typecheck`

Run: `rtk npm --prefix ui run lint`

Run: `rtk npm --prefix ui run build`

Run: `rtk scripts/test-deployment-config.sh`

Run: `rtk scripts/check-runtime-parity.sh`

Expected: PASS. If Testcontainers cannot access Docker, rerun with Docker-socket permission before classifying the suite.

- [ ] **Step 4: Deploy an isolated local-values k3s smoke release**

Follow [`../../harness/local-harness.md`](../../harness/local-harness.md) exactly. Confirm demo auth, NATS, KEDA, import worker, compile worker, API, UI, PostgreSQL, Valkey, and Keycloak readiness separately. Do not validate this feature against a shared/Flux-managed release.

- [ ] **Step 5: Run Falcon 9 import and compile without the external provider**

Run:

```bash
LIVE_FLOW_3MF_PATH=/tmp/falcon9_200mm.3mf \
LIVE_FLOW_COMPILE_ONLY=true \
scripts/harness-k3s.sh live-flow
```

Expected: authenticated upload returns 202; import succeeds; manifest reports non-zero objects/vertices/triangles and honest warnings; generated `design.py` compiles; GLB artifact is non-empty and loads through the UI origin.

- [ ] **Step 6: Browser-check both entry points and a supported boolean**

In the authenticated browser, verify Generate Design and Intus both open the same dialog, the imported Falcon project appears, progress/warnings are accessible, the viewer renders, and the console/network panels have no unexplained errors. Use the manifest to select a `boolean_capable` part; edit `design.py` to subtract a small cylinder that intersects its bounds; compile and confirm a new GLB artifact. If no part is boolean capable, verify transform/assembly success and report the source limitation rather than pretending a boolean succeeded.

- [ ] **Step 7: Obtain consent and run the real AI edit flow**

Before this step, ask the user for explicit consent to send generated project Python and bounded part metadata to the configured external provider. Never send the 3MF/BREP bytes. After consent:

```bash
LIVE_FLOW_3MF_PATH=/tmp/falcon9_200mm.3mf \
LIVE_FLOW_VERIFY_CONVERSATION=true \
scripts/harness-k3s.sh live-flow
```

Expected: pre-edit compile succeeds, real AI edit terminates successfully, persisted context contains safe manifest metadata only, post-edit compile succeeds, and conversation/artifact linkage retains the selected model and import provenance.

- [ ] **Step 8: Run implementation and spec review**

Dispatch a code-review agent over the full diff. Resolve every correctness, security, tenant-isolation, native-parser, binary-leakage, UI-accessibility, runtime-parity, and validation-evidence finding. Rerun all affected gates after fixes.

- [ ] **Step 9: Commit final verification evidence**

Update this plan's checkboxes and add a concise validation section with commands and outcomes. Do not commit the sample or credentials.

```bash
rtk git add docs/superpowers/plans/2026-08-11-3mf-import-editing.md
rtk git commit -m "docs: record 3mf import verification"
```

---

## Plan Self-Review

- **Spec coverage:** Tasks 1-14 map every architecture, API, persistence, security, AI, UI, runtime, documentation, and test requirement in the approved design; Task 15 proves the requested sample and authenticated workflow.
- **Placeholder scan:** Every task contains concrete implementation behavior, test input, expected outcome, and verification command.
- **Type consistency:** `ObjectRef`, `Import3mfManifest`, `ProjectAsset`, `ProjectImportJob`, `CompileJobAsset`, `Import3mfCommand`, `Import3mfResult`, `Import3mfJobStatus`, and `Imported3mfModel` retain the same names and responsibilities throughout.
- **Dependency order:** Domain -> persistence -> object transport -> converter/messages -> workers -> API -> compile assets -> runtime loader -> AI context -> frontend adapter -> shared dialog -> surfaces -> deployment -> harness -> full evidence.
- **Stream Coding clarity score:** 9.6/10. Exact files, types, limits, commands, outcomes, anti-pattern references, and the real Falcon 9 acceptance flow leave no material product decision to an implementation worker.
