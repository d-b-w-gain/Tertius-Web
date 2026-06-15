# LLM File Edit Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-script LLM generation contract with an authenticated, paid, frontend-callable endpoint that accepts a user prompt plus project file database pointers, asks the LLM provider for structured file edits, persists the accepted files as a new project version, and returns the updated files for the UI to render.

**Architecture:** The FastAPI Intus backend remains the trusted boundary. The browser sends only the active project name, user prompt, and backend-issued `ProjectFile.id` pointers; the API validates tenant/project ownership, loads file contents internally, prepends the server-owned system prompt, calls the OpenAI-compatible provider, validates the provider's structured file response against the requested files, writes all file updates in one DB transaction with one `SourceSnapshot`, records LLM usage, publishes a billing event, and returns the updated file payload. The existing provider key, model, base URL, usage guard, billing stream, and Keycloak auth remain server-only.

**Tech Stack:** Python, FastAPI, Pydantic, SQLAlchemy, Alembic, OpenAI Python SDK, NATS JetStream, React/Vite TypeScript, pytest, Vitest.

---

## Document Type

Implementation plan. This document is standalone and intentionally repeats the full API, storage, billing, validation, test, and deployment constraints needed to implement the feature without reading older plan files.

## Current State

| Area | Current Behavior | Required Change |
|------|------------------|-----------------|
| UI file handles | `ProjectStorage.listFiles(projectName)` returns `string[]` filenames only. | Add authenticated file metadata so UI can send `ProjectFile.id` database pointers. |
| File content access | UI loads content with `GET /projects/{name}/code?file=...`. | Keep this route for editor loading, but return updated file contents directly from the LLM edit response. |
| File persistence | `ProjectRepository.save_code(...)` updates one file and creates one `SourceSnapshot`. | Add a batch update method that updates multiple existing files and creates one project snapshot. |
| LLM endpoint | `POST /projects/{name}/build-script/generate` accepts prompt/current code and returns one script without saving. | Add `POST /projects/{name}/files/llm-edit` for multi-file edits that saves the provider response. Keep the old route until the UI no longer depends on it. |
| Versioning | `SourceSnapshot` captures all current project files with a message and content hash. | Treat one successful LLM edit request as one new project version by writing one `SourceSnapshot`. |
| Paid endpoint controls | `llm_usage_records`, `assert_llm_usage_allowed`, `record_llm_usage`, and `LlmTokenUsageEvent` exist. | Reuse them and fail closed on billing persistence or publish failure. |
| Provider credentials | `LLM_API_KEY` is API-only; UI and compile jobs must not receive it. | Preserve this deployment boundary. |

## Existing Files To Know

| Path | Why It Matters |
|------|----------------|
| `server/workflows/intus/intus_server.py` | FastAPI Intus routes, including project files, save, compile, and current LLM generation route. |
| `server/core/llm_client.py` | OpenAI-compatible provider wrapper, prompt construction, token extraction, billing event publication. |
| `server/core/llm_usage.py` | DB-backed LLM rate/quota checks and usage persistence. |
| `server/core/billing_messages.py` | `LlmTokenUsageEvent`, billing message size checks, billing message IDs. |
| `server/core/repositories.py` | Tenant-scoped project/file repository and source snapshot creation. |
| `server/core/models.py` | `ProjectFile`, `SourceSnapshot`, `SourceSnapshotFile`, `LlmUsageRecord`. |
| `server/core/config.py` | `LLM_*` and billing settings. |
| `server/core/nats_client.py` | Compile and billing JetStream stream setup. |
| `ui/src/workflows/shared/projectStorage.ts` | UI storage abstraction that should expose file metadata and the LLM edit call. |
| `ui/src/workflows/intus/ui/CompilerTab.tsx` | Current Intus editor tabs, active file state, autosave, compile, history rendering. |
| `ui/src/api/client.ts` | Authenticated fetch wrapper. |

## Product Decisions

| Decision | Value |
|----------|-------|
| Database pointer type | Use `ProjectFile.id` as the canonical pointer. Include `filename` in the request for UI/debug readability, but validate by ID. |
| Files eligible for edit | Only files explicitly supplied in the request can be modified. The provider must not create, delete, rename, or modify unrequested files in this pass. |
| Provider response format | JSON object with a `files` array. Each file item contains `file_id`, `content`, and optional `summary`. |
| Save behavior | Successful provider output is automatically persisted as a new project version. There is no separate user approval step in this backend contract. |
| Version model | Use one project-wide `SourceSnapshot` per successful LLM edit request. Do not add per-file version tables in this pass. |
| Guest mode | Authenticated-only. Guest localStorage projects have no database pointers and cannot call this endpoint. |
| Old build-script endpoint | Keep until UI migration is complete. The new file edit endpoint becomes the intended UI path. |

## API Contract

### File Metadata Endpoint

Modify:

`GET /api/intus/projects/{name}/files`

Backward-compatible response:

```json
{
  "files": ["design.py", "helper.py"],
  "file_metadata": [
    {
      "id": "2e3fe196-1f6c-4e2b-8a77-595fc7e046ab",
      "filename": "design.py",
      "updated_at": "2026-06-16T04:12:45.123456Z"
    },
    {
      "id": "bd56db48-76dc-4c5c-b849-701aa973ed32",
      "filename": "helper.py",
      "updated_at": "2026-06-16T04:12:45.123456Z"
    }
  ]
}
```

Rules:
- Keep `files` so existing UI/tests continue to work.
- Add `file_metadata` for authenticated UI callers.
- Return only files belonging to `ctx.tenant_id` and the named project.
- Preserve `design.py` first ordering.

### LLM File Edit Request

Create:

`POST /api/intus/projects/{name}/files/llm-edit`

```json
{
  "prompt": "Refactor the purlin into a reusable helper and update the main design to use it.",
  "files": [
    {
      "id": "2e3fe196-1f6c-4e2b-8a77-595fc7e046ab",
      "filename": "design.py"
    },
    {
      "id": "bd56db48-76dc-4c5c-b849-701aa973ed32",
      "filename": "helper.py"
    }
  ],
  "active_file_id": "2e3fe196-1f6c-4e2b-8a77-595fc7e046ab",
  "metadata": {
    "source": "compiler_tab",
    "interaction_id": "ui-123"
  }
}
```

Validation:
- `prompt`: required, `1..12000` chars.
- `files`: required, `1..20` items.
- `files[*].id`: required UUID.
- `files[*].filename`: required Python filename, must match the DB row for that ID.
- `active_file_id`: optional UUID, must be one of the requested file IDs when present.
- `metadata`: optional `dict[str, str]`, max 50 entries, keys and values max 200 chars each.

### Provider Message Contract

The backend builds messages; the browser never sends system prompt text.

System prompt:

```text
You edit Python source files for Tertius Intus.
Return only valid JSON. Do not include markdown fences or explanation.
You may modify only files listed in the user message.
Do not create, delete, or rename files.
Each returned file must use the exact file_id supplied by the user.
Return the full final content for every changed file.
If a file does not need changes, omit it from the files array.
All code must be executable Python source suitable for build123d when geometry is involved.
```

User message shape:

```text
User request:
<prompt>

Active file id:
<active_file_id or "none">

Files available for editing:
[
  {
    "file_id": "<uuid>",
    "filename": "design.py",
    "content": "<current file content>"
  }
]

Return JSON matching:
{
  "files": [
    {
      "file_id": "<uuid from files available for editing>",
      "content": "<full final Python source>",
      "summary": "<short human-readable summary>"
    }
  ]
}
```

Provider response accepted by backend:

```json
{
  "files": [
    {
      "file_id": "2e3fe196-1f6c-4e2b-8a77-595fc7e046ab",
      "content": "import build123d as bd\nfrom helper import make_purlin\n\npart = make_purlin()\n",
      "summary": "Updated design.py to call helper.make_purlin."
    },
    {
      "file_id": "bd56db48-76dc-4c5c-b849-701aa973ed32",
      "content": "import build123d as bd\n\ndef make_purlin():\n    return bd.Box(1, 2, 3)\n",
      "summary": "Added reusable purlin helper."
    }
  ]
}
```

Provider response rejection rules:
- Reject non-JSON response.
- Reject missing `files`.
- Reject more returned files than requested files.
- Reject any returned `file_id` not in the request.
- Reject duplicate returned `file_id`.
- Reject invalid Python filename/content association after DB lookup.
- Reject file content above `200000` chars.
- Reject an empty `files` array with `422` and message `LLM returned no file changes`.

### LLM File Edit Response

```json
{
  "success": true,
  "model": "deepseek-v4-flash",
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 200,
    "total_tokens": 300
  },
  "snapshot": {
    "id": "1f6b14cd-a879-4056-a0f5-fdca9e79e88a",
    "message": "LLM edit: Refactor the purlin into a reusable helper",
    "content_hash": "a4ad62f..."
  },
  "files": [
    {
      "id": "2e3fe196-1f6c-4e2b-8a77-595fc7e046ab",
      "filename": "design.py",
      "content": "import build123d as bd\nfrom helper import make_purlin\n\npart = make_purlin()\n",
      "updated_at": "2026-06-16T04:13:22.123456Z",
      "changed": true,
      "summary": "Updated design.py to call helper.make_purlin."
    },
    {
      "id": "bd56db48-76dc-4c5c-b849-701aa973ed32",
      "filename": "helper.py",
      "content": "import build123d as bd\n\ndef make_purlin():\n    return bd.Box(1, 2, 3)\n",
      "updated_at": "2026-06-16T04:13:22.123456Z",
      "changed": true,
      "summary": "Added reusable purlin helper."
    }
  ]
}
```

Rules:
- Return only files changed by the LLM, not every requested file.
- Include full `content` for each changed file so the UI can render without follow-up `GET /code` calls.
- Include `snapshot` so the UI can refresh/render history consistently with existing project versioning.
- Include `usage` for paid endpoint display/debugging.

## Error Handling Matrix

| Case | Status | Body | Notes |
|------|--------|------|-------|
| Missing/invalid auth | 401 | Existing auth behavior | Must happen before DB/provider work. |
| Project not found | 404 | `{"success": false, "error": "Project not found"}` | Tenant-scoped lookup only. |
| Invalid prompt/files payload | 422 | FastAPI/Pydantic validation body | Use Pydantic constraints. |
| Invalid filename | 400 | `{"success": false, "error": "Invalid Python filename"}` | Reuse `require_valid_python_filename`. |
| File pointer not in project/tenant | 404 | `{"success": false, "error": "File not found"}` | Do not reveal cross-tenant file existence. |
| File ID/filename mismatch | 400 | `{"success": false, "error": "File pointer does not match filename"}` | Prevent stale/malicious UI payloads. |
| Missing `LLM_API_KEY` | 503 | `{"success": false, "error": "LLM provider is not configured", "retryable": false}` | Existing paid endpoint behavior. |
| Quota/rate exceeded | 429 | `{"success": false, "error": "LLM usage limit exceeded", "retryable": true}` | Must occur before provider call. |
| Provider timeout/error | 503 | `{"success": false, "error": "LLM generation failed", "retryable": true}` | Log exception server-side. |
| Provider non-JSON/malformed response | 502 | `{"success": false, "error": "LLM returned invalid file edits", "retryable": true}` | Provider responded, but not usable. |
| Provider returns unauthorized file | 502 | `{"success": false, "error": "LLM returned invalid file edits", "retryable": true}` | Do not persist any file. |
| Provider returns no file changes | 422 | `{"success": false, "error": "LLM returned no file changes", "retryable": false}` | User can revise prompt. |
| Billing setup/publish/persistence failure | 503 | `{"success": false, "error": "LLM billing failed", "retryable": true}` | Fail closed and rollback saved files. |
| DB save/snapshot failure | 503 | `{"success": false, "error": "LLM file update failed", "retryable": true}` | Roll back DB transaction. |

## Anti-Patterns

| Do Not | Do Instead | Why |
|--------|------------|-----|
| Let the UI send file contents as source of truth for LLM context | Load contents from `ProjectFile` rows after validating file IDs | Browser state can be stale or manipulated. |
| Use filenames as database pointers | Use `ProjectFile.id`, with filename as a mismatch guard | IDs are stable and tenant/project scoped. |
| Allow provider to create/delete/rename files | Only accept returned file IDs that were requested | Keeps first version narrow and easy to test. |
| Save each returned file with `save_code()` in a loop | Add one batch repository method with one transaction and one snapshot | Avoids multiple versions for one AI action. |
| Return provider output before billing is durable | Publish/persist billing and commit DB state before returning success | Paid endpoint must fail closed. |
| Expose `LLM_API_KEY`, base URL, or model selection to UI | Keep all provider config server-side | Prevents credential leakage and billing spoofing. |
| Trust LLM JSON without schema validation | Parse into Pydantic response models and validate IDs against requested rows | LLM output is untrusted external input. |
| Store the full user prompt in `SourceSnapshot.message` | Truncate `message` to 500 chars before insertion (`SourceSnapshot.message` is `String(500)`) | Long prompts will raise a `DataError` at flush and abort the whole edit. |
| Update compile job pods with LLM settings | Keep LLM env only on backend/API | Compile worker isolation must remain intact. |
| Break existing `files: string[]` response immediately | Add `file_metadata` alongside `files` | Keeps current UI/tests compatible during migration. |
| Claim per-file versioning exists | Use project-wide `SourceSnapshot` and name it clearly in API response | Current schema versions whole project source state. |

## Test Case Specifications

### Unit Tests Required

| Test ID | Component | Input | Expected Output |
|---------|-----------|-------|-----------------|
| UT-001 | `ProjectRepository.list_file_metadata` | Seeded project with two files | Ordered metadata with IDs, filenames, updated_at; `design.py` first. |
| UT-002 | `ProjectRepository.files_by_ids` | Valid IDs in tenant/project | Returns `ProjectFile` rows keyed by ID. |
| UT-003 | `ProjectRepository.files_by_ids` | Cross-tenant or wrong-project ID | Omits row so route returns 404. |
| UT-004 | `ProjectRepository.stage_file_updates` | Two file updates | Updates both rows and creates one `SourceSnapshot`. |
| UT-005 | `parse_llm_file_edit_response` | Valid JSON file edit response | Returns validated file edit objects. |
| UT-006 | `parse_llm_file_edit_response` | Non-JSON, duplicate ID, unknown ID, empty files | Raises `LlmInvalidFileEditError`. |
| UT-007 | `estimate_file_edit_tokens` | Prompt plus two DB-loaded file contents | Estimate exceeds `llm_max_output_tokens` alone. |
| UT-008 | `parse_llm_file_edit_response` | Markdown-fenced JSON (e.g. ```` ```json ... ``` ````) | Strips fence and returns validated file edit objects. |
| UT-009 | `ProjectRepository.stage_file_updates` | Long `message` (e.g. 12000 chars) | Truncates to 500 chars; `SourceSnapshot.message` length is ≤ 500. |

### Integration Tests Required

| Test ID | Flow | Setup | Verification |
|---------|------|-------|--------------|
| IT-001 | List files metadata | Authenticated client and seeded project | `GET /projects/default_purlin/files` includes old `files` and new `file_metadata`. |
| IT-002 | Successful LLM file edit | Fake provider returns two valid file edits | Endpoint returns changed files, DB rows update, exactly one `SourceSnapshot`, one usage record. |
| IT-003 | Cross-tenant file pointer | Request includes another tenant's file ID | `404`, provider stub never invoked, no snapshot row, no `LlmUsageRecord` row, billing publisher never opened. |
| IT-004 | Provider returns unauthorized file ID | Fake provider returns an ID not requested | `502`, DB unchanged, no usage record commit. |
| IT-005 | Billing publisher unavailable | Billing setup raises `LlmBillingError` before provider call | `503`, provider not called. |
| IT-006 | Billing publish fails after provider | Fake publisher raises | `503`, DB rollback keeps files unchanged. |
| IT-007 | Mounted public route | `main` app route `/api/intus/projects/{name}/files/llm-edit` | Same success response as direct Intus app. |

### UI Tests Required

| Test ID | Component | Setup | Verification |
|---------|-----------|-------|--------------|
| UI-001 | `projectStorage.listFileMetadata` | API returns `file_metadata` | Storage returns IDs, filenames, updated_at. |
| UI-002 | `projectStorage.applyLlmFileEdit` | API returns changed files | Storage returns full changed file payload. |
| UI-003 | `CompilerTab` | Authenticated project with metadata | AI edit request sends selected file IDs, not file contents. |
| UI-004 | `CompilerTab` | Successful AI edit changes active file | Editor content and file tabs update from response. |
| UI-005 | `CompilerTab` | Guest mode | AI edit control is disabled or hidden and no API call is made. |

## File Structure

### Backend

- Modify `server/core/repositories.py`
  - Add `list_file_metadata(project_name)` returning a list of dicts with `{id, filename, updated_at}`.
  - Add `files_by_ids(project_name, file_ids)`.
  - Add `stage_file_updates(project_name, updates, user_id, message)` returning `(snapshot, changed_files)`. The returned list and `message` are truncated to fit `SourceSnapshot.message` (`String(500)`).
- Modify `server/core/llm_usage.py`
  - Add an `operation: str` keyword argument to `record_llm_usage(...)`. Existing call sites pass `operation="build_script.generate"`; the new file-edit route passes `operation="files.llm_edit"`.
- Modify `server/core/llm_client.py`
  - Keep current build-script helpers until old route is retired.
  - Add file-edit request/result Pydantic models.
  - Add `build_file_edit_messages(...)`.
  - Add `parse_llm_file_edit_response(...)`.
  - Add `estimate_file_edit_tokens(...)`.
  - Add `generate_file_edits(...)`.
- Modify `server/workflows/intus/intus_server.py`
  - Add route models for file metadata and file edit request/response if they are API-specific.
  - Update `list_files` to include `file_metadata`.
  - Add `POST /projects/{name}/files/llm-edit`.
- Modify `server/tests/test_repositories.py`
  - Add repository tests for file metadata, ID lookup, and batch snapshots.
- Modify `server/tests/test_llm_client.py`
  - Add provider message, response parsing, and billing event tests for file edits.
- Modify `server/tests/test_build_script_generation.py`
  - Keep existing tests for old route until it is deleted.
  - Add new file edit endpoint tests here or create `server/tests/test_llm_file_edit.py`.

### Frontend

- Modify `ui/src/workflows/shared/projectStorage.ts`
  - Add `ProjectFileMetadata`, `LlmFileEditRequest`, `LlmFileEditResult` types.
  - Add `listFileMetadata(projectName)`.
  - Add `applyLlmFileEdit(projectName, request)`.
  - Keep existing `listFiles` behavior.
- Modify `ui/src/workflows/intus/ui/CompilerTab.tsx`
  - Track file metadata alongside filename tabs.
  - Add authenticated-only AI prompt state and submit handler.
  - Send selected `ProjectFile.id` values to the endpoint.
  - Apply returned file contents to state; switch to active changed file when appropriate.
  - Refresh history after success.
- Modify UI tests under `ui/src/workflows/intus/ui/` and `ui/src/workflows/shared/projectStorage.test.ts`.

## Tasks

### Task 1: Repository File Metadata And Batch Versioning

**Files:**
- Modify: `server/core/repositories.py`
- Test: `server/tests/test_repositories.py`

- [ ] **Step 1: Add failing metadata and batch snapshot tests**

Add tests:

```python
from sqlalchemy import func, select

from core.models import ProjectFile, SourceSnapshot


def test_project_repository_lists_file_metadata_with_design_first(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    metadata = repo.list_file_metadata("same_name")

    assert [row["filename"] for row in metadata] == ["design.py", "helper.py"]
    assert all(row["id"] for row in metadata)
    assert all(row["updated_at"] for row in metadata)


def test_project_repository_batch_file_updates_create_one_snapshot(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    file_rows = db_session.scalars(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.filename.in_(["design.py", "helper.py"]),
        )
    ).all()
    files = {row.id: row for row in file_rows}

    snapshot, changed = repo.stage_file_updates(
        "same_name",
        {
            next(row.id for row in files.values() if row.filename == "design.py"): "design = 2",
            next(row.id for row in files.values() if row.filename == "helper.py"): "helper = 2",
        },
        seeded["user_a"],
        "LLM edit: update two files",
    )
    db_session.commit()

    assert snapshot.message == "LLM edit: update two files"
    assert len(changed) == 2
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    assert repo.get_code("same_name", "design.py") == "design = 2"
    assert repo.get_code("same_name", "helper.py") == "helper = 2"


def test_project_repository_stage_file_updates_truncates_long_snapshot_message(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    file_rows = db_session.scalars(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.filename == "design.py",
        )
    ).all()
    long_message = "LLM edit: " + ("x" * 12000)

    snapshot, _ = repo.stage_file_updates(
        "same_name",
        {file_rows[0].id: "design = 3"},
        seeded["user_a"],
        long_message,
    )
    db_session.commit()

    assert len(snapshot.message) <= 500
    assert snapshot.message.startswith("LLM edit:")
```

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

Expected before implementation: tests fail because the new methods do not exist.

- [ ] **Step 2: Implement repository helpers**

Add methods to `ProjectRepository`:

```python
def list_file_metadata(self, project_name: str) -> list[dict[str, object]]:
    project = self.get_project(project_name)
    if project is None:
        return []
    files = self.db.scalars(
        select(ProjectFile)
        .where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)
        .order_by(ProjectFile.filename)
    ).all()
    rows = [
        {"id": file.id, "filename": file.filename, "updated_at": file.updated_at}
        for file in files
    ]
    rows.sort(key=lambda row: (row["filename"] != "design.py", row["filename"]))
    return rows

def files_by_ids(self, project_name: str, file_ids: list[UUID]) -> dict[UUID, ProjectFile]:
    project = self.get_project(project_name)
    if project is None or not file_ids:
        return {}
    rows = self.db.scalars(
        select(ProjectFile).where(
            ProjectFile.tenant_id == self.tenant_id,
            ProjectFile.project_id == project.id,
            ProjectFile.id.in_(file_ids),
        )
    ).all()
    return {row.id: row for row in rows}

def stage_file_updates(
    self,
    project_name: str,
    updates: dict[UUID, str],
    user_id: UUID,
    message: str,
) -> tuple[SourceSnapshot, list[ProjectFile]] | None:
    project = self.get_project(project_name)
    if project is None:
        return None
    files = self.files_by_ids(project_name, list(updates.keys()))
    if set(files) != set(updates):
        return None
    now = now_utc()
    changed: list[ProjectFile] = []
    for file_id, content in updates.items():
        file = files[file_id]
        if file.content != content:
            file.content = content
            file.updated_at = now
            changed.append(file)
    if not changed:
        raise ValueError("LLM returned no file changes")
    project.updated_at = now
    self.db.flush()
    truncated_message = (message or "LLM edit")[:500]
    snapshot = self._snapshot(project, user_id, truncated_message)
    return snapshot, changed
```

Change `_snapshot(...)` to return the created `SourceSnapshot`.

- [ ] **Step 3: Run repository tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

Expected: repository tests pass.

### Task 2: LLM File Edit Provider Models

**Files:**
- Modify: `server/core/llm_client.py`
- Test: `server/tests/test_llm_client.py`

- [ ] **Step 1: Add failing provider parsing tests**

Add tests for:
- valid file edit JSON.
- markdown-fenced JSON.
- unknown file ID rejection.
- duplicate file ID rejection.
- empty `files` rejection.
- provider billing event operation is `files.llm_edit`.

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

Expected before implementation: imports or assertions fail.

- [ ] **Step 2: Add file edit models and parser**

Add Pydantic models:

```python
class LlmFilePointer(BaseModel):
    id: UUID
    filename: str

class LlmEditableFile(BaseModel):
    id: UUID
    filename: str
    content: str = Field(max_length=200000)

class LlmFileEditInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    files: list[LlmFilePointer] = Field(min_length=1, max_length=20)
    active_file_id: UUID | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

class LlmReturnedFileEdit(BaseModel):
    file_id: UUID
    content: str = Field(max_length=200000)
    summary: str = Field(default="", max_length=500)

class LlmFileEditProviderResult(BaseModel):
    files: list[LlmReturnedFileEdit] = Field(min_length=1, max_length=20)

class LlmFileEditResult(BaseModel):
    success: bool = True
    files: list[LlmReturnedFileEdit]
    model: str
    usage: TokenUsage
    provider_request_id: str | None = None
    billing_event_id: UUID | None = None
```

Add `LlmInvalidFileEditError`.

Implement `parse_llm_file_edit_response(content: str, allowed_file_ids: set[UUID]) -> LlmFileEditProviderResult`. The implementation must call the existing `strip_markdown_code_fence(content)` helper (see `llm_client.py:90`) before `json.loads` so markdown-fenced provider responses are accepted.

- [ ] **Step 3: Add message builder and provider call**

Implement:

```python
def build_file_edit_messages(request: LlmFileEditInput, files: list[LlmEditableFile]) -> list[dict[str, str]]:
    ...

def estimate_file_edit_tokens(request: LlmFileEditInput, files: list[LlmEditableFile], *, max_output_tokens: int) -> int:
    ...

async def generate_file_edits(...):
    ...
```

Provider call must use:

```python
response = await client.chat.completions.create(
    model=settings.llm_model,
    messages=build_file_edit_messages(request, files),
    max_tokens=settings.llm_max_output_tokens,
    response_format={"type": "json_object"},
)
```

Billing event must use:
- `workflow="intus"`
- `operation="files.llm_edit"`
- `prompt=request.prompt`
- tenant/user/project IDs from trusted backend context.

`generate_file_edits(...)` must accept `auth: AuthContext`, `project_id: UUID`, and `billing_publisher: Publisher | None` like `generate_build_script`, but must publish the billing event only **after** `parse_llm_file_edit_response` succeeds. If the provider output is invalid (unknown ID, duplicate ID, empty `files`, non-JSON, oversized content), no billing event is published and no `LlmUsageRecord` is created. This matches the existing build-script ordering, but for the file-edit endpoint the parsing step happens inside the client function so the route does not need to re-parse.

- [ ] **Step 4: Run LLM client tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

Expected: tests pass.

### Task 3: Backend API Routes

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Test: `server/tests/test_llm_file_edit.py`
- Test: `server/tests/test_build_script_generation.py`

- [ ] **Step 1: Add failing endpoint tests**

Create `server/tests/test_llm_file_edit.py` covering IT-001 through IT-007 from this plan.

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_file_edit.py -q
```

Expected before implementation: route and payload tests fail.

- [ ] **Step 2: Update `list_files`**

Change route return from:

```python
return {"files": files}
```

to:

```python
return {
    "files": files,
    "file_metadata": [
        {
            "id": str(row["id"]),
            "filename": row["filename"],
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in repo.list_file_metadata(name)
    ],
}
```

- [ ] **Step 3: Add `POST /projects/{name}/files/llm-edit`**

Implement the route with this order:
1. Validate project exists by tenant-scoped repo lookup.
2. Validate filenames and duplicate file IDs in request.
3. Load `ProjectFile` rows by IDs from DB.
4. Return `404` if any file ID is missing.
5. Return `400` if any supplied filename does not match the DB row.
6. Estimate tokens from the server-loaded contents.
7. Run `assert_llm_usage_allowed(...)`.
8. Create billing publisher before provider call.
9. Call `generate_file_edits(...)`. The provider output is parsed and validated inside this function; on `LlmInvalidFileEditError` (non-JSON, unknown ID, duplicate ID, oversized content) no billing event is published and the route returns `502 {"success": false, "error": "LLM returned invalid file edits", "retryable": true}`. On empty `files` from the provider, the route returns `422 {"success": false, "error": "LLM returned no file changes", "retryable": false}`.
10. Persist returned edits through `repo.stage_file_updates(...)`. If this raises `ValueError("LLM returned no file changes")` (LLM returned content identical to current), the route returns the same `422` shape as the empty-files case.
11. Persist LLM usage through `record_llm_usage(..., operation="files.llm_edit")`.
12. Commit DB transaction.
13. Flush/close billing NATS connection.
14. Return changed file payload.

The route must rollback on provider, billing, validation, or DB errors after the transaction begins. The `finally` block must close the billing NATS connection exactly as the existing `build-script/generate` handler does.

- [ ] **Step 4: Run focused endpoint tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_file_edit.py server/tests/test_build_script_generation.py -q
```

Expected: new endpoint tests pass and old build-script route tests still pass.

### Task 4: Frontend Storage Contract

**Files:**
- Modify: `ui/src/workflows/shared/projectStorage.ts`
- Test: `ui/src/workflows/shared/projectStorage.test.ts`

- [ ] **Step 1: Add failing storage tests**

Add tests asserting:
- authenticated `listFileMetadata` parses `file_metadata`.
- fallback metadata is derived from filenames when backend lacks `file_metadata`.
- `applyLlmFileEdit` posts file IDs and prompt to `/files/llm-edit`.
- guest `applyLlmFileEdit` rejects with `Log in to use AI file edits`.

Run:

```bash
cd ui && rtk npm test -- projectStorage.test.ts
```

Expected before implementation: tests fail because methods do not exist.

- [ ] **Step 2: Add storage types and methods**

Add:

```ts
export type ProjectFileMetadata = {
  id: string
  filename: string
  updated_at?: string
}

export type LlmFileEditResult = {
  success: true
  model: string
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number }
  snapshot: { id: string; message: string; content_hash: string }
  files: Array<{
    id: string
    filename: string
    content: string
    updated_at?: string
    changed: boolean
    summary?: string
  }>
}
```

Extend `ProjectStorage` with:

```ts
listFileMetadata: (projectName: string) => Promise<ProjectFileMetadata[]>
applyLlmFileEdit: (
  projectName: string,
  request: {
    prompt: string
    files: Array<{ id: string; filename: string }>
    active_file_id?: string
    metadata?: Record<string, string>
  },
) => Promise<LlmFileEditResult>
```

- [ ] **Step 3: Run storage tests**

```bash
cd ui && rtk npm test -- projectStorage.test.ts
```

Expected: storage tests pass.

### Task 5: Intus UI Integration

**Files:**
- Modify: `ui/src/workflows/intus/ui/CompilerTab.tsx`
- Test: `ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx`
- Test: `ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx`

- [ ] **Step 1: Add failing UI tests**

Add tests asserting:
- authenticated UI loads file metadata after project load.
- submitting an AI edit sends all current file IDs and `active_file_id`.
- successful response updates file tabs and active editor content (the active file's `code` state matches the returned content for that file).
- the AI prompt control is rendered for authenticated users and not rendered (or rendered disabled) for guest users, and the storage `applyLlmFileEdit` is never invoked in guest mode.
- after a successful AI edit that changes a non-active file, switching to that file tab triggers a fresh `storage.loadCode` call and the editor shows the new server-side content (the UI does not cache stale content locally).

Run:

```bash
cd ui && rtk npm test -- CompilerTab
```

Expected before implementation: tests fail because the UI has no AI edit control/method.

- [ ] **Step 2: Track metadata in `CompilerTab`**

Add state:

```ts
const [fileMetadata, setFileMetadata] = useState<ProjectFileMetadata[]>([])
const [aiPrompt, setAiPrompt] = useState('')
const [isApplyingAiEdit, setIsApplyingAiEdit] = useState(false)
```

Update project load to call `storage.listFileMetadata(projectName)`, set filename tabs from metadata, and keep current fallback to `storage.listFiles(projectName)` if metadata is empty.

- [ ] **Step 3: Add AI edit submit handler**

Implement:

```ts
const applyAiEdit = async () => {
  if (isGuest || !activeProject || !aiPrompt.trim() || fileMetadata.length === 0) return
  setIsApplyingAiEdit(true)
  try {
    const result = await storage.applyLlmFileEdit(activeProject, {
      prompt: aiPrompt.trim(),
      files: fileMetadata.map(file => ({ id: file.id, filename: file.filename })),
      active_file_id: fileMetadata.find(file => file.filename === activeFile)?.id,
      metadata: { source: 'compiler_tab' },
    })
    const nextMetadata = result.files.map(file => ({
      id: file.id,
      filename: file.filename,
      updated_at: file.updated_at,
    }))
    setFileMetadata(prev => prev.map(existing => nextMetadata.find(file => file.id === existing.id) || existing))
    setFiles(prev => Array.from(new Set([...prev, ...result.files.map(file => file.filename)])))
    const activeChanged = result.files.find(file => file.filename === activeFile) || result.files[0]
    if (activeChanged) {
      setActiveFile(activeChanged.filename)
      setCode(activeChanged.content)
    }
    setAiPrompt('')
    setLog(prev => `${prev ? `${prev}\n` : ''}[INFO] AI updated ${result.files.length} file(s).`)
    fetchGitStatus(activeProject)
  } catch (error) {
    const message = error instanceof Error ? error.message : 'AI file edit failed.'
    setLog(prev => `${prev ? `${prev}\n` : ''}[ERROR] ${message}`)
  } finally {
    setIsApplyingAiEdit(false)
  }
}
```

Keep visual changes compact. Do not build a landing page or broad redesign.

- [ ] **Step 4: Run UI tests**

```bash
cd ui && rtk npm test -- CompilerTab
```

Expected: Intus UI tests pass.

### Task 6: Full Verification

**Files:**
- No source changes unless tests expose a spec gap.

- [ ] **Step 1: Run focused backend tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py server/tests/test_llm_client.py server/tests/test_llm_file_edit.py server/tests/test_build_script_generation.py server/tests/test_llm_usage.py -q
```

Expected: all selected backend tests pass.

- [ ] **Step 2: Run full backend tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests -q
```

Expected: all backend tests pass.

- [ ] **Step 3: Run UI tests/build**

```bash
cd ui && rtk npm test -- --run
cd ui && rtk npm run build
```

Expected: tests and build pass.

- [ ] **Step 4: Run deployment checks**

```bash
rtk scripts/test-deployment-config.sh
rtk helm lint infra/charts/tertius
rtk docker compose config
```

Expected:
- deployment config script exits 0.
- Helm lint exits 0.
- Compose config renders.
- `LLM_API_KEY` remains backend/API-only and is not injected into UI or compile-job services.

- [ ] **Step 5: Run whitespace check**

```bash
rtk git diff --check
```

Expected: no whitespace errors.

## Clarity Gate

| Check | Status | Evidence |
|-------|--------|----------|
| Actionable | Pass | Every requirement maps to files, routes, methods, or tests. |
| Current | Pass | Based on current FastAPI/React code paths and existing paid LLM plumbing. |
| Single Source | Pass | This plan is standalone and does not require older plan files. |
| Decision, Not Wish | Pass | File IDs, endpoint path, response shape, save behavior, and error codes are fixed. |
| Prompt-Ready | Pass | Provider system/user message contract is explicitly defined. |
| No Future State | Pass | Per-file versions, file creation, deletion, renaming, streaming, and guest support are excluded. |
| No Fluff | Pass | Content is implementation-specific. |
| Type Identified | Pass | Document type declared as implementation plan. |
| Anti-patterns Placed | Pass | Anti-patterns section is included in this implementation doc. |
| Test Cases Placed | Pass | Unit, integration, and UI test specs are included. |
| Error Handling Placed | Pass | Error matrix is included. |
| Deep Links Present | Pass | Exact repo paths and route paths are listed. |
| No Duplicates | Pass | Repeated context is intentional because this replaces deleted older plans. |

AI Coder Understandability Score: 9.5/10.

Resolved from initial review:
- `record_llm_usage` operation is now an explicit parameter with a known value at every call site.
- `SourceSnapshot.message` truncation is in the repository and covered by UT-009.
- Both empty-files (`LlmInvalidFileEditError`) and no-op (`ValueError`) 422 paths are explicit on the route.
- `parse_llm_file_edit_response` uses the existing `strip_markdown_code_fence` helper, covered by UT-008.
- `list_file_metadata` return type is locked to `list[dict[str, object]]` keyed by string to match the dict-style test.
- IT-003 now asserts the provider stub and billing publisher were not called.
- Billing event ordering is documented: validation runs inside `generate_file_edits` before publish.

Remaining minor ambiguity: exact UI placement of the AI prompt control is left to the implementer, but it must be compact and inside `CompilerTab`; behavior, state, API calls, and tests are specified.
