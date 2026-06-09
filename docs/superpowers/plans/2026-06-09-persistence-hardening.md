# Persistence Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix active workspace persistence, make artifact storage durable in Kubernetes, add artifact retention cleanup, and guard migrations against model drift.

**Architecture:** Keep persistence behavior in `server/core`: `ProjectRepository` owns active workspace state and `CompileRepository` owns artifact metadata pruning. Workflow servers remain thin callers. Deployment config exposes `ARTIFACT_ROOT` to match the API PVC mount.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest, Helm templates.

---

### Task 1: Centralize Active Workspace State

**Files:**
- Modify: `server/core/repositories.py`
- Modify: `server/workflows/artus/artus_server.py`
- Modify: `server/workflows/extus/extus_server.py`
- Modify: `server/workflows/timus/timus_server.py`
- Test: `server/tests/test_repositories.py`
- Test: `server/tests/test_workflow_isolation.py`

- [ ] Write repository and workflow tests that call `set_active_project` through Artus, Extus, and Timus activation routes.
- [ ] Run focused tests and confirm they fail with missing `ProjectRepository.set_active_project`.
- [ ] Add `ProjectRepository.set_active_project(user_id, project_id)` that validates tenant ownership, updates `UserWorkspaceState`, and sets `active_file_id` to the project `design.py` when present.
- [ ] Make `activate_project(project_name, user_id)` delegate to `set_active_project`.
- [ ] Re-run focused tests and confirm they pass.

### Task 2: Align Artifact Root With API PVC

**Files:**
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `scripts/test-deployment-config.sh`

- [ ] Add a deployment-config assertion that rendered Helm output includes `ARTIFACT_ROOT: "/app/cache/tertius/artifacts"`.
- [ ] Run `scripts/test-deployment-config.sh` and confirm it fails.
- [ ] Add `app.config.artifactRoot` to chart values and render it as `ARTIFACT_ROOT` in the API ConfigMap.
- [ ] Re-run `scripts/test-deployment-config.sh`.

### Task 3: Add Artifact Retention Cleanup

**Files:**
- Modify: `server/core/config.py`
- Modify: `server/core/artifacts.py`
- Modify: `server/core/repositories.py`
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/workflows/timus/timus_server.py`
- Test: `server/tests/test_artifacts.py`
- Test: `server/tests/test_compile_flow.py`

- [ ] Write failing tests for safe artifact deletion and compile-time pruning of older artifact rows/files.
- [ ] Add `Settings.artifact_retention_limit` with a conservative default.
- [ ] Add `ArtifactStore.delete(storage_key)` that validates keys and ignores missing files.
- [ ] Add `CompileRepository.prune_artifacts(project_id, kind, keep_latest)` returning deleted storage keys.
- [ ] Call pruning after successful Intus and Timus artifact writes, commit DB deletion, then delete returned files from `ArtifactStore`.
- [ ] Re-run focused tests.

### Task 4: Add Migration Drift Test

**Files:**
- Modify: `server/tests/test_migrations.py`

- [ ] Write a test that runs Alembic to head, compares SQLAlchemy metadata against the migrated database with Alembic autogenerate, and expects no diffs.
- [ ] Run the migration test and confirm current schema drift fails if present.
- [ ] If drift is found, update migration/model definitions in scope.
- [ ] Re-run the migration test.

### Task 5: Verify

**Files:**
- No source edits.

- [ ] Run focused server tests for repositories, artifacts, compile flow, workflow isolation, and migrations.
- [ ] Run deployment config verification.
- [ ] Report exact verification results and any residual risks.
