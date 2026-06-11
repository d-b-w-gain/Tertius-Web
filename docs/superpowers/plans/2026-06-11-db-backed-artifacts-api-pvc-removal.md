# DB-Backed Artifacts and API PVC Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Tertius application artifact persistence from the API filesystem PVC into Postgres, keep only temporary compile-time filesystem use, and remove the API cache PVC from Helm.

**Architecture:** Project source files, workspace state, compile jobs, and artifact metadata already live in Postgres. Add artifact bytes to the existing artifact persistence boundary, then update Intus, Timus, and Extus to read/write artifact payloads through the DB instead of `ArtifactStore` filesystem paths. Once no runtime code needs `ARTIFACT_ROOT`, remove the API PVC, mount, config value, and deployment tests that require the API pod to mount a PVC.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres `BYTEA`, pytest, Helm, CloudNativePG, Bash deployment smoke scripts.

---

## Scope

In scope:
- Store generated artifact bytes for `stl`, `step`, `gltf`, `glb`, and `timus_views` in Postgres.
- Preserve ephemeral `TemporaryDirectory` usage for compile sandbox hydration and output collection.
- Remove only the API cache/artifact PVC and its `ARTIFACT_ROOT` wiring.

Out of scope:
- Valkey PVC or Valkey dependency changes.
- App Postgres CloudNativePG storage changes.
- Keycloak database storage changes.
- External object storage.
- Moving guest browser `localStorage` drafts into the DB.

## File Map

- `server/core/models.py`: add artifact byte storage column to `Artifact`.
- `server/migrations/versions/<new_revision>_artifact_content.py`: migrate existing `artifacts` table to include bytes.
- `server/core/repositories.py`: make `CompileRepository` record and fetch artifact bytes; keep pruning metadata in one place.
- `server/core/artifacts.py`: replace filesystem-oriented store with a DB-friendly helper, or remove it after callers migrate.
- `server/workflows/intus/intus_server.py`: write compile output bytes directly to artifact rows.
- `server/workflows/extus/extus_server.py`: stream latest model artifact bytes from DB and report artifact freshness from DB timestamps instead of file mtimes.
- `server/workflows/timus/timus_server.py`: write/read Timus model and drafting-view artifacts from DB.
- `server/tests/test_migrations.py`: assert new artifact byte column exists and model matches migration head.
- `server/tests/test_artifacts.py`: replace path traversal/file deletion tests with byte helper tests, or delete if helper goes away.
- `server/tests/test_compile_flow.py`: assert compile persists artifact bytes and prunes DB rows without filesystem deletion.
- `server/tests/test_tenant_integrity.py`: add artifact byte fixtures to integrity tests that construct `Artifact` rows.
- `server/tests/test_workflow_isolation.py`: assert Extus/Timus artifact reads are tenant/project scoped and do not depend on files.
- `infra/charts/tertius/values.yaml`: remove `app.config.artifactRoot` and disable/remove `api.persistence`.
- `infra/charts/tertius/values-local.yaml`: remove local API persistence overrides.
- `infra/charts/tertius/templates/configmap.yaml`: stop rendering `ARTIFACT_ROOT`.
- `infra/charts/tertius/templates/api.yaml`: remove API `api-cache` volume mount and volume.
- `infra/charts/tertius/templates/api-pvc.yaml`: delete the API PVC template.
- `scripts/test-deployment-config.sh`: replace API PVC assertions with assertions that API renders without a PVC and without `ARTIFACT_ROOT`.
- `scripts/test-k3s-deployment.sh`: stop requiring the API pod to mount a PVC.
- `README.md`: update local/deployment docs that mention `ARTIFACT_ROOT` or preserving API PVCs.

## Anti-Patterns (DO NOT)

| Don't | Do Instead | Why |
| --- | --- | --- |
| Store new artifact bytes on `/app/cache/tertius` | Store bytes in Postgres through `CompileRepository` | The API PVC is being removed |
| Keep `ARTIFACT_ROOT` as a required runtime setting | Remove it from chart config and code paths | It implies filesystem persistence still exists |
| Use `FileResponse` for DB-backed artifacts | Return `Response(content=artifact.content, media_type=artifact.content_type)` | `FileResponse` requires a filesystem path |
| Delete artifact files during pruning | Delete only DB rows once bytes live in DB | There are no files to clean up |
| Persist compile sandbox directories | Keep `hydrate_project_files()` as temporary filesystem use | Build tools need files, but they do not need durable storage |
| Remove Postgres or Keycloak PVCs in this change | Leave database-internal PVCs untouched | User scoped this plan to API persistence only |
| Add object storage abstraction now | Use Postgres `BYTEA` directly | YAGNI for current small generated artifacts |

## Test Case Specifications

### Unit Tests Required

| Test ID | Component | Input | Expected Output | Edge Cases |
| --- | --- | --- | --- | --- |
| UT-001 | `CompileRepository.record_artifact` | artifact bytes `b"solid"` | row has `content`, `byte_size`, `content_type` | empty bytes allowed |
| UT-002 | `CompileRepository.get_artifact_content` or equivalent | artifact id in same tenant | returns bytes and media type | missing id returns `None` |
| UT-003 | pruning | three artifacts same project/kind, keep 1 | old DB rows removed, newest remains | other kind remains |
| UT-004 | Extus response | latest STL artifact row with bytes | HTTP 200 with exact bytes | no row returns 404 |
| UT-005 | Timus model response | latest GLB/GLTF artifact row with bytes | HTTP 200 with exact bytes | missing row returns 404 |
| UT-006 | Timus drafting views | `timus_views` artifact content JSON bytes | PDF path loads JSON from bytes | invalid JSON returns 500 or controlled error |
| UT-007 | Extus status | latest STL artifact row with `created_at` | `mtime` equals artifact `created_at.timestamp()` | no row returns `0` |

### Integration Tests Required

| Test ID | Flow | Setup | Verification | Teardown |
| --- | --- | --- | --- | --- |
| IT-001 | Intus compile success | mock sandbox output path containing STL bytes | `artifacts.content == output_bytes`; response has `artifact_id` | rollback DB fixture |
| IT-002 | Extus tenant isolation | two tenants with artifact rows | authenticated tenant only receives own latest bytes | rollback DB fixture |
| IT-003 | Helm render | `helm template tertius infra/charts/tertius --values values-local.yaml` | no `tertius-api-cache`, no API PVC, no `ARTIFACT_ROOT` | none |
| IT-004 | k3s smoke script static gate | run `scripts/test-deployment-config.sh` | passes without API PVC assertions | none |

## Error Handling Matrix

| Error Type | Detection | Response | Logging | Test |
| --- | --- | --- | --- | --- |
| Artifact row missing | DB query returns `None` | 404 `File not found` for model routes; status routes return `mtime: 0` | no stack trace | Extus/Timus 404 and status tests |
| Artifact content missing after migration | `artifact.content is None` | 404 for read routes; compile writes never produce null content | warn if logger exists, otherwise no noisy print | migration/backfill decision test |
| Compile sandbox output missing | `result.output_path is None` | existing failed compile JSON path | existing exception handling | existing compile tests |
| DB write fails after compile output read | SQLAlchemy exception during record | rollback, mark compile job failed when possible | existing traceback behavior | compile failure regression |
| Invalid Timus `timus_views` payload | `json.loads()` raises | current 500 path is acceptable for first pass | traceback currently printed | Timus invalid payload test |

## Deep Links

- Existing filesystem artifact store: `server/core/artifacts.py`
- Existing artifact model: `server/core/models.py`
- Existing artifact metadata repository: `server/core/repositories.py`
- Intus compile write path: `server/workflows/intus/intus_server.py`
- Extus read path: `server/workflows/extus/extus_server.py`
- Timus write/read paths: `server/workflows/timus/timus_server.py`
- API PVC template: `infra/charts/tertius/templates/api-pvc.yaml`
- API mount template: `infra/charts/tertius/templates/api.yaml`
- API config template: `infra/charts/tertius/templates/configmap.yaml`
- Deployment config gate: `scripts/test-deployment-config.sh`
- k3s smoke gate: `scripts/test-k3s-deployment.sh`

---

### Task 1: Add Artifact Bytes to the DB Schema

**Files:**
- Modify: `server/core/models.py`
- Create: `server/migrations/versions/0002_artifact_content.py`
- Modify: `server/tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration test**

Add assertions to `test_alembic_upgrade_creates_multitenant_schema` in `server/tests/test_migrations.py`:

```python
artifact_columns = {
    column["name"]: column for column in inspector.get_columns("artifacts")
}
assert "content" in artifact_columns
assert str(artifact_columns["content"]["type"]).lower() in {"bytea", "blob", "largebinary"}
assert artifact_columns["content"]["nullable"] is True
```

- [ ] **Step 2: Run the migration test and verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py::test_alembic_upgrade_creates_multitenant_schema -q
```

Expected: fail because `artifacts.content` does not exist.

- [ ] **Step 3: Update the SQLAlchemy model**

In `server/core/models.py`, add `LargeBinary` to the SQLAlchemy imports and add `content` to `Artifact`:

```python
content: Mapped[bytes | None] = mapped_column(LargeBinary)
```

Place it near `storage_key`, `content_type`, and `byte_size`.

- [ ] **Step 4: Add the Alembic migration**

Create `server/migrations/versions/0002_artifact_content.py`:

```python
import sqlalchemy as sa
from alembic import op


revision = "0002_artifact_content"
down_revision = "0001_initial_multitenant_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("content", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifacts", "content")
```

This preserves upgradeability for existing artifact metadata rows without inventing empty content. New artifacts must write bytes; old rows with null content should return 404 until a one-time importer backfills them. Production should import required old files before deleting the old API PVC if existing artifacts must be retained.

- [ ] **Step 5: Run migration tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py -q
```

Expected: migration schema test and model/head comparison pass.

- [ ] **Step 6: Commit**

```bash
rtk git add server/core/models.py server/migrations/versions/0002_artifact_content.py server/tests/test_migrations.py
rtk git commit -m "feat: add db-backed artifact content"
```

### Task 2: Move Artifact Writes Into `CompileRepository`

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_compile_flow.py`
- Modify: `server/tests/test_tenant_integrity.py`
- Modify: `server/tests/test_artifacts.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/workflows/timus/timus_server.py`

- [ ] **Step 1: Write failing repository expectations**

Update tests that currently build filesystem artifacts to create DB-backed artifact rows. The important assertion in compile flow is:

```python
artifact = db_session.scalar(select(Artifact))
assert artifact.content == b"solid mocked"
assert artifact.byte_size == len(b"solid mocked")
assert artifact.storage_key.endswith(".stl")
```

For pruning tests, assert old artifact rows disappear and no file checks remain:

```python
artifacts = db_session.scalars(select(Artifact).order_by(Artifact.kind, Artifact.created_at)).all()
assert [(artifact.kind, artifact.content) for artifact in artifacts] == [
    ("step", b"ISO-10303"),
    ("stl", b"solid new"),
]
```

- [ ] **Step 2: Run compile tests and verify they fail**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_flow.py server/tests/test_artifacts.py -q
```

Expected: fail because `CompileRepository.record_artifact` does not accept or store content yet, and tests still rely on `ArtifactStore`.

- [ ] **Step 3: Change `CompileRepository.record_artifact` signature**

In `server/core/repositories.py`, change `record_artifact` to accept bytes:

```python
def record_artifact(
    self,
    project_id: UUID,
    job_id: UUID | None,
    kind: str,
    storage_key: str,
    content_type: str,
    content: bytes,
) -> Artifact:
    artifact = Artifact(
        tenant_id=self.tenant_id,
        project_id=project_id,
        compile_job_id=job_id,
        kind=kind.lower(),
        storage_key=storage_key,
        content_type=content_type,
        byte_size=len(content),
        content=content,
    )
    self.db.add(artifact)
    self.db.flush()
    return artifact
```

- [ ] **Step 4: Add an artifact key helper**

Either keep a trimmed helper in `server/core/artifacts.py`, or move this function into `server/core/repositories.py`:

```python
def artifact_storage_key(tenant_id: UUID, project_id: UUID, kind: str) -> str:
    ext = kind.lower()
    return f"{tenant_id}/{project_id}/{uuid4()}.{ext}"
```

Keep `CONTENT_TYPES` for content type lookup:

```python
content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
```

- [ ] **Step 5: Update Intus compile writes**

In `server/workflows/intus/intus_server.py`, replace:

```python
artifact_store = ArtifactStore(settings.artifact_root)
stored = artifact_store.write_bytes(ctx.tenant_id, project_id, ext, output_bytes)
if not artifact_store.path_for(stored.storage_key).exists():
    raise RuntimeError("Artifact write failed")
```

with:

```python
artifact = compile_repo.record_artifact(
    project_id,
    job_id,
    ext,
    output_bytes,
)
```

Remove the loop that calls `artifact_store.delete(...)`; keep `compile_repo.delete_artifacts(pruned_artifacts)`.

- [ ] **Step 6: Update Timus background artifact writes**

In `_background_build_timus_views` in `server/workflows/timus/timus_server.py`, replace `ArtifactStore.write_bytes(...)` with `compile_repo.record_artifact(project_id, job_id, "timus_views", output_bytes)`. Remove filesystem deletion during pruning.

- [ ] **Step 7: Run focused tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_compile_flow.py server/tests/test_artifacts.py -q
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
rtk git add server/core/repositories.py server/core/artifacts.py server/workflows/intus/intus_server.py server/workflows/timus/timus_server.py server/tests/test_compile_flow.py server/tests/test_artifacts.py
rtk git commit -m "feat: persist artifact bytes in postgres"
```

### Task 3: Move Artifact Reads Off the Filesystem

**Files:**
- Modify: `server/workflows/extus/extus_server.py`
- Modify: `server/workflows/timus/timus_server.py`
- Modify: `server/tests/test_workflow_isolation.py`

- [ ] **Step 1: Write failing Extus read tests**

Change Extus tests so they create `Artifact(content=b"solid latest", ...)` directly and do not create files under `tmp_path`. The success assertion should verify response bytes:

```python
response = client.get("/api/extus/model", headers=auth_headers)
assert response.status_code == 200
assert response.content == b"solid latest"
```

The missing artifact test should now mean no DB row, not a missing file:

```python
response = client.get("/api/extus/model", headers=auth_headers)
assert response.status_code == 404
```

- [ ] **Step 2: Write failing Extus status tests**

Create a latest model artifact with a fixed `created_at` and bytes, then assert status uses the DB timestamp rather than a file mtime:

```python
created_at = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
artifact = Artifact(
    tenant_id=seeded_tenant.tenant_id,
    project_id=seeded_tenant.project_id,
    kind="stl",
    storage_key=f"{seeded_tenant.tenant_id}/{seeded_tenant.project_id}/latest.stl",
    content_type="application/octet-stream",
    byte_size=len(b"solid latest"),
    content=b"solid latest",
    created_at=created_at,
)
db_session.add(artifact)
db_session.commit()

response = client.get("/api/extus/status", headers=auth_headers)
assert response.status_code == 200
assert response.json()["mtime"] == created_at.timestamp()
```

Also assert an authenticated tenant with no artifact receives:

```python
assert response.json()["mtime"] == 0
```

- [ ] **Step 3: Write failing Timus read tests**

For `/projects/{name}/model`, create a `gltf` or `glb` `Artifact` row with `content=b"model-bytes"` and assert:

```python
assert response.status_code == 200
assert response.content == b"model-bytes"
```

For `timus_views`, create a `timus_views` artifact with JSON bytes:

```python
content=json.dumps({"top": [], "front": [], "side": [], "iso": []}).encode("utf-8")
```

Then assert the drafting PDF endpoint returns `application/pdf`.

- [ ] **Step 4: Run workflow isolation tests and verify they fail**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_workflow_isolation.py -q
```

Expected: fail because Extus/Timus still resolve `ArtifactStore.path_for(...)` and Extus status still calls `path.stat().st_mtime`.

- [ ] **Step 5: Update Extus model response**

In `server/workflows/extus/extus_server.py`, remove `FileResponse`, `ArtifactStore`, and `get_artifact_path`. Return bytes directly:

```python
@app.get("/model")
def get_model(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return Response(content=artifact.content, media_type=artifact.content_type)
```

- [ ] **Step 6: Update Extus status**

Replace the current artifact path lookup and `path.stat().st_mtime` in Extus status with a DB timestamp:

```python
@app.get("/status")
def get_status(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    artifact = get_latest_model_artifact(db, ctx)
    if artifact is None or artifact.content is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"mtime": artifact.created_at.timestamp()}
```

- [ ] **Step 7: Update Timus model reads**

In `get_gltf_model`, replace filesystem reads with:

```python
if not latest_artifact or latest_artifact.content is None:
    return Response("No 3D model found", 404)
return Response(content=latest_artifact.content, media_type=latest_artifact.content_type)
```

- [ ] **Step 8: Update Timus drafting-view reads**

In `get_drafting_pdf`, replace:

```python
artifact_store = ArtifactStore(get_settings().artifact_root)
artifact_path = artifact_store.path_for(latest_artifact.storage_key)

with open(artifact_path, "r") as f:
    views = json.load(f)
```

with:

```python
views = json.loads(latest_artifact.content.decode("utf-8"))
```

- [ ] **Step 9: Run workflow tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_workflow_isolation.py -q
```

Expected: pass.

- [ ] **Step 10: Commit**

```bash
rtk git add server/workflows/extus/extus_server.py server/workflows/timus/timus_server.py server/tests/test_workflow_isolation.py
rtk git commit -m "feat: stream artifacts from postgres"
```

### Task 4: Remove `ARTIFACT_ROOT` Runtime Configuration

**Files:**
- Modify: `server/core/config.py`
- Modify: `server/tests/test_config.py`
- Modify: `server/tests/test_auth.py`
- Modify: `server/tests/test_keycloak_integration.py`
- Modify: `README.md`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Write failing config test updates**

In `server/tests/test_config.py`, remove `ARTIFACT_ROOT` from env fixture setup and delete assertions like:

```python
assert settings.artifact_root == "/tmp/env-artifacts"
```

Add:

```python
assert not hasattr(settings, "artifact_root")
```

- [ ] **Step 2: Remove config field**

In `server/core/config.py`, delete:

```python
artifact_root: str = Field(default="/tmp/tertius-artifacts")
```

- [ ] **Step 3: Remove test fixture settings**

Delete `artifact_root="/tmp/tertius-artifacts"` from test settings stubs in:
- `server/tests/test_auth.py`
- `server/tests/test_keycloak_integration.py`
- any remaining `server/tests/**` file found by `rg -n "artifact_root|ARTIFACT_ROOT" server/tests`

- [ ] **Step 4: Remove local compose artifact mount**

In `docker-compose.yml`, remove:

```yaml
- ./artifacts:/tmp/tertius-artifacts
```

and any `ARTIFACT_ROOT` env var if present.

- [ ] **Step 5: Update README**

Remove the local `.env` line:

```bash
ARTIFACT_ROOT=/tmp/tertius-artifacts
```

Replace it with a note:

```markdown
Generated artifacts are stored in Postgres. Local compile runs still use temporary directories for sandbox input/output, but no durable artifact directory is required.
```

- [ ] **Step 6: Run config and backend artifact tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_config.py server/tests/test_auth.py server/tests/test_keycloak_integration.py server/tests/test_compile_flow.py server/tests/test_workflow_isolation.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
rtk git add server/core/config.py server/tests/test_config.py server/tests/test_auth.py server/tests/test_keycloak_integration.py README.md docker-compose.yml
rtk git commit -m "chore: remove artifact root configuration"
```

### Task 5: Remove the API PVC From Helm

**Files:**
- Delete: `infra/charts/tertius/templates/api-pvc.yaml`
- Modify: `infra/charts/tertius/templates/api.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `scripts/test-deployment-config.sh`

- [ ] **Step 1: Update deployment-config assertions first**

In `scripts/test-deployment-config.sh`, replace the current positive API PVC and `ARTIFACT_ROOT` assertions with:

```bash
if printf '%s\n' "$rendered" | rg -q 'name: tertius-api-cache|claimName: tertius-api-cache|mountPath: /app/cache/tertius|ARTIFACT_ROOT'; then
  echo "Local Helm render must not include the API artifact PVC, API cache mount, or ARTIFACT_ROOT." >&2
  exit 1
fi
```

Keep any generic PVC checks only if they are not specifically about the API pod. Do not add Valkey or database PVC changes in this plan.

- [ ] **Step 2: Run the gate and verify it fails**

Run:

```bash
rtk ./scripts/test-deployment-config.sh
```

Expected: fail because chart still renders API PVC/mount/`ARTIFACT_ROOT`.

- [ ] **Step 3: Remove values**

In `infra/charts/tertius/values.yaml`, delete:

```yaml
artifactRoot: /app/cache/tertius/artifacts
```

and delete the whole `api.persistence` block.

In `infra/charts/tertius/values-local.yaml`, delete:

```yaml
api:
  persistence:
    size: 2Gi
    storageClassName: local-path
```

while preserving unrelated `api` image/resources fields.

- [ ] **Step 4: Remove ConfigMap entry**

In `infra/charts/tertius/templates/configmap.yaml`, delete:

```yaml
ARTIFACT_ROOT: {{ .Values.app.config.artifactRoot | quote }}
```

- [ ] **Step 5: Remove API mount and volume**

In `infra/charts/tertius/templates/api.yaml`, delete:

```yaml
volumeMounts:
  - name: api-cache
    mountPath: /app/cache/tertius
```

and delete:

```yaml
volumes:
  - name: api-cache
    persistentVolumeClaim:
      claimName: {{ include "tertius.apiName" . }}-cache
```

There should be no `emptyDir` replacement; this change removes durable artifact storage and does not need an API cache volume.

- [ ] **Step 6: Delete the PVC template**

Delete:

```bash
rtk git rm infra/charts/tertius/templates/api-pvc.yaml
```

- [ ] **Step 7: Run Helm gate**

Run:

```bash
rtk ./scripts/test-deployment-config.sh
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
rtk git add infra/charts/tertius/templates/api.yaml infra/charts/tertius/templates/configmap.yaml infra/charts/tertius/values.yaml infra/charts/tertius/values-local.yaml scripts/test-deployment-config.sh
rtk git add -u infra/charts/tertius/templates/api-pvc.yaml
rtk git commit -m "chore: remove api artifact pvc"
```

### Task 6: Update k3s Smoke Script API PVC Checks

**Files:**
- Modify: `scripts/test-k3s-deployment.sh`
- Modify: `README.md`

- [ ] **Step 1: Replace API PVC smoke function**

In `scripts/test-k3s-deployment.sh`, split `check_pvc_bound_and_mounted`: keep a generic release PVC bound check, then add a function that verifies the API pod has no PVC claim:

```bash
check_release_pvcs_bound() {
  pvc_names=$(capture kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME}" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' || true)
  [ -n "$pvc_names" ] || {
    echo "No PVCs found for release ${RELEASE_NAME}." >&2
    exit 1
  }
  printf '%s\n' "$pvc_names"
  if printf '%s\n' "$pvc_names" | awk '$2 != "Bound" { found=1 } END { exit found ? 0 : 1 }'; then
    echo "At least one PVC is not Bound." >&2
    exit 1
  fi
}
```

```bash
check_api_has_no_pvc_mount() {
  api_pod=$(capture kubectl get pod -n "$NAMESPACE" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=api" -o jsonpath='{.items[0].metadata.name}')
  api_claims=$(capture kubectl get pod "$api_pod" -n "$NAMESPACE" -o jsonpath='{range .spec.volumes[*]}{.persistentVolumeClaim.claimName}{"\n"}{end}' || true)
  if [ -n "$api_claims" ]; then
    echo "API pod ${api_pod} still mounts PVCs:" >&2
    printf '%s\n' "$api_claims" >&2
    return 1
  fi
}
```

Then replace the call:

```bash
check_pvc_bound_and_mounted
```

with:

```bash
check_release_pvcs_bound
check_api_has_no_pvc_mount
```

- [ ] **Step 2: Keep database/other PVC behavior out of scope**

Do not change cleanup logic for CloudNativePG clusters or unrelated PVCs. If README currently says cleanup preserves PVCs generally, narrow the API artifact language:

```markdown
The API no longer owns an artifact PVC; generated artifacts are stored in Postgres.
```

- [ ] **Step 3: Run static deploy gate**

Run:

```bash
rtk bash -n scripts/test-k3s-deployment.sh
rtk ./scripts/test-deployment-config.sh
```

Expected: both pass.

- [ ] **Step 4: Commit**

```bash
rtk git add scripts/test-k3s-deployment.sh README.md
rtk git commit -m "test: expect api deployment without pvc"
```

### Task 7: Full Verification

**Files:**
- No source edits unless failures expose a missed reference.

- [ ] **Step 1: Search for removed runtime concepts**

Run:

```bash
rtk rg -n "ARTIFACT_ROOT|artifact_root|ArtifactStore|path_for|/app/cache/tertius|tertius-api-cache|api.persistence" server infra/charts/tertius scripts README.md docker-compose.yml
```

Expected: no runtime references. Historical docs under `docs/superpowers/plans/` may still mention old decisions and do not block this change.

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_migrations.py server/tests/test_compile_flow.py server/tests/test_workflow_isolation.py server/tests/test_config.py server/tests/test_artifacts.py -q
```

Expected: pass.

- [ ] **Step 3: Run deployment config gate**

Run:

```bash
rtk ./scripts/test-deployment-config.sh
```

Expected: pass.

- [ ] **Step 4: Run optional local k3s smoke**

Run only when the local cluster is available:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk ./scripts/test-k3s-deployment.sh
```

Expected: API, UI, Postgres, Keycloak, and route smoke checks pass; API pod does not mount a PVC.

## Rollout Notes

- Existing artifact files on the API PVC will not be automatically imported by the migration above. If production has artifact files that must survive, run a one-time importer before deleting the API PVC:
  - query `artifacts.storage_key`;
  - read the matching file from old `ARTIFACT_ROOT`;
  - update `artifacts.content`;
  - verify `byte_size = octet_length(content)`.
- After deploy and verification, manually delete only the old API cache PVC for the release. Do not delete database PVCs.

## Self-Review

- Spec coverage: items 1, 2, and 3 are covered by Tasks 1-7.
- Placeholder scan: no `TBD`, `TODO`, or "add appropriate" placeholders remain.
- Type consistency: `Artifact.content`, `CompileRepository.record_artifact`, `artifact_storage_key`, and route response changes are named consistently across tasks.
