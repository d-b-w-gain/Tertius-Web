# Plan: Persist AI Generation Conversations Permanently

## Current state

AI generation "conversations" in the **Generate Design** tab live only in React state:

- `messages: ChatMessage[]` in `ui/src/workflows/generate/GenerateDesignWindow.tsx:103`
- Cleared on project switch (`setMessages([])` at `GenerateDesignWindow.tsx:154`) and lost on unmount/refresh.
- Each `ChatMessage` = `{ id, role, content, createdAt, files?, usage?, artifactId?, modelUrl?, compileStatus? }`.

The underlying events are **already persisted** in Postgres, but not stitched together or retrievable as a history:

| Row | Holds | Location |
|-----|-------|----------|
| `LlmEditJob` | `request_payload` (prompt + file pointers + model_id), `result_payload` (outcome, files, usage, model, snapshot), `status`, `error`, timestamps | `server/core/models.py:244` |
| `CompileJob` | `status`, `export_format`, `error`, `artifact_id` (via `Artifact`), timestamps | `server/core/models.py:163` |
| `Artifact` | `storage_key`, `content_type`, bytes | `server/core/models.py:289` |

Gaps blocking reconstruction as a conversation:
1. No endpoint to **list** `LlmEditJob` rows for a project (only `GET /projects/{name}/files/llm-edit/jobs/{job_id}` at `intus_server.py:887`).
2. No link from `CompileJob` back to the `LlmEditJob` that triggered it (`CompileJob` has no `originating_llm_edit_job_id`). The frontend currently tracks this association only in memory.
3. Frontend has no `listMessages`/`loadConversation` storage method and does not hydrate `messages` on project load.

## Goal

When a user opens the Generate Design tab and selects a project, the full prior prompt/response/compile history for that project is loaded from the database and rendered. New prompts append to the same durable history. Refreshing the page or switching away and back preserves the conversation.

## Design decision

**Reconstruct the conversation from existing `LlmEditJob` rows** (single source of truth) instead of introducing a new `conversation_messages` table.

- Each `LlmEditJob` already maps 1:1 to a user-prompt + assistant-response pair.
- Conversation scope = **per project**, ordered by `created_at`. No separate `conversation_id` needed for v1 (noted as a future extension if multi-thread-per-project is wanted).
- The only schema addition required is linking `CompileJob` → `LlmEditJob` so the assistant message can show compile status / artifact historically.

Rejected alternative: a separate `conversation_messages` table would duplicate data already in `LlmEditJob`/`CompileJob` and require bidirectional sync. Not worth it.

## Backend changes

### 1. Migration `0009_compile_job_originating_llm_edit.py`

Add nullable column to `compile_jobs`:

```python
sa.Column("originating_llm_edit_job_id", sa.Uuid(), nullable=True)
sa.Index("ix_compile_jobs_originating_llm_edit", "originating_llm_edit_job_id")
```

- Nullable because compile jobs can be started manually (not via AI edit), and existing rows have no origin.
- No FK (the `compile_jobs` composite-FK pattern uses `(id, project_id, tenant_id)`; a loose nullable UUID column keeps backfill simple and avoids tightening coupling). Document this in the migration docstring.
- `down_revision = "0008_llm_edit_jobs"`.

### 2. Model update (`server/core/models.py`)

Add to `CompileJob`:
```python
originating_llm_edit_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True, index=True)
```

### 3. Repository (`server/core/repositories.py`)

Add to `LlmEditRepository`:
- `list_jobs_for_project(project_id: UUID, *, limit: int = 200) -> list[LlmEditJob]` — ordered by `created_at ASC`, tenant-scoped. This is the conversation source.
- `get_compile_job_for_llm_edit(project_id: UUID, llm_edit_job_id: UUID) -> CompileJob | None` — looks up via the new column (used to populate `artifactId`/`compileStatus` historically).

Add to `CompileRepository`:
- Accept an optional `originating_llm_edit_job_id` param on the create/queue path and persist it. The sole caller is the compile POST handler at `intus_server.py:320`.

### 4. Endpoint — list conversation (`intus_server.py`)

```
GET /projects/{name}/files/llm-edit/jobs?limit=200
```

Returns an ordered array shaped for the frontend:
```json
{
  "messages": [
    {
      "job_id": "...",
      "role": "user" | "assistant",
      "prompt": "...",            // from request_payload.prompt
      "content": "...",           // "" for non-terminal (queued/running); derived from outcome message / error / user_message for terminal
      "created_at": "...",
      "status": "succeeded" | "failed" | "running" | "queued",
      "model": "...",             // from result_payload.model
      "usage": { ... },           // from result_payload.usage
      "files": [ { filename, summary, changed } ],   // projected from result_payload.files with .content stripped
      "requested_file_count": N,  // len(request_payload.files); lets UI re-derive "Included N of M" against live fileMetadata
      "compile": {
        "job_id": "...",
        "status": "succeeded" | ...,
        "artifact_id": "...",
        "export_format": "glb"
      } | null
    }
  ]
}
```

- Pair each `LlmEditJob` into a single assistant entry that embeds the original prompt (the UI currently splits into two `ChatMessage` rows — user + assistant — but for persistence a single record carrying `prompt` + response is cleaner; the frontend can split for rendering).
- For `status` in `{"queued","running"}`, `result_payload`/`error`/`user_message` are all null, so `content` is returned as `""`. The frontend overlays its optimistic "Generating…" bubble on top of empty `content` for non-terminal entries — the server does not synthesize placeholder text.
- Payload weight: `request_payload`/`result_payload` are full JSON (file pointers + per-file snapshots). Project server-side: drop `result_payload.files[].content` (file body), keep only `filename`/`summary`/`changed`. Expose `requested_file_count` so the frontend re-derives the "Included N of M files" notice client-side (N from this count, M from the live `fileMetadata` it fetches separately) — replaces storing a transient notice.
- Compile payload: include the linked compile job id and resolve historical `artifact_id` through `CompileRepository.artifact_for_job(...)` (or an equivalent artifact join), because `CompileJob` stores status/format while `Artifact` stores the artifact id.
- Auth: `get_auth_context` + tenant scoping, same as the existing job-status endpoint.
- 404 if project not found; 400 if `get_project(name)` raises `ValueError` (mirrors the existing handler at `intus_server.py:898`).

### 5. Wire the origin link on auto-compile

Backend: the compile POST handler at `intus_server.py:320` is the only compile-create entry point; thread `originating_llm_edit_job_id` from the request body through to `CompileRepository`'s create/queue call and persist it on the new column.

Frontend: the job id becomes available at three sites in `GenerateDesignWindow.tsx`, all of which must carry the new param:
1. `submitPrompt` (`:452`) — where `job.job_id` is first returned from `storage.applyLlmFileEditJob`; stash it on the assistant `ChatMessage`.
2. `applyLlmEditResult` (`:329`) — currently calls `queueCompile(...)`; add the job id to its arguments so the compile request can include it.
3. `queueCompile` (`:292`) — extend its signature to accept `originatingLlmEditJobId: string`, and include it in the POST body at `:308`.

The assistant message's job id is available once polling starts, so threading is mechanical — but it touches all three call sites.

## Frontend changes

### 1. `projectStorage.ts`

Add to the `ProjectStorage` type and both guest/authenticated implementations:
```ts
listLlmEditConversation(projectName: string): Promise<LlmEditConversationEntry[]>
```
- Authenticated: GET the new endpoint.
- Guest: return `[]` (guests can't use AI edits; existing pattern).

Add a request field `originating_llm_edit_job_id?: string` to the compile POST body (typed via the existing compile call site in `GenerateDesignWindow.tsx:305`).

### 2. `GenerateDesignWindow.tsx`

- On initial project load or explicit project switch (replacing the current `setMessages([])` at line 154), call `storage.listLlmEditConversation(nextProject)` and hydrate `messages` from the result (split each entry into user/assistant pair to preserve the current rendering; recommend keeping the pair for minimal UI churn). Do **not** rehydrate on the existing active-project polling interval when `nextProject === activeProjectRef.current`; that interval should only refresh project metadata/status so it cannot clobber optimistic in-browser message content.
- **Stable message ids derived from `job_id`.** The current `messageId()` helper (`:83`) produces `${prefix}-${Date.now()}-${random}` ids that change every load, breaking `selectedMessageId` stability and the compile-origin thread. For hydrated messages use deterministic ids: user = `prompt:${job_id}`, assistant = `job:${job_id}`. Optimistically-created messages during `submitPrompt` must be re-keyed to these stable ids once `job.job_id` is returned from the create call (`:452`) — the assistant id is referenced by `pollLlmEditJob`/`pollCompileJob`/`setSelectedMessageId`, so re-key inline rather than letting two ids drift.
- **Patch-in-place on terminal status; never full re-fetch mid-session.** The assistant `content` is mutated in-browser with text the server cannot reconstruct — `"Compile queued as glb/medium."` (`:322`), the `truncatedMessage` suffix (`:477`), and incremental status updates. Replacing `messages` from `listLlmEditConversation` on terminal would erase these. Instead, patch the specific assistant message (looked up by its stable `job:${job_id}` id) with the authoritative `job_id`, `artifactId`, `compileStatus`, `usage`, and `files` — preserving `content`.
- Persist `job_id` on each assistant `ChatMessage` (new optional field) so the compile POST can pass `originating_llm_edit_job_id`.
- Polling for in-flight jobs on mount: **iterate every** hydrated entry whose `status` is `{"queued","running"}`, not just the last one (a prior crashed mid-flight job plus a fresh submit can both be non-terminal on next load). Replace the current single `llmEditTimerRef`/`compileTimerRef` and request counters with maps keyed by job id/message id before doing this; otherwise one resumed poll chain cancels or invalidates another. For each in-flight LLM entry, resume `pollLlmEditJob` keyed by the stable assistant id. For each linked compile object whose status is non-terminal, resume `pollCompileJob` using `compile.job_id`. Skip entries already terminal.
- Backend stale recovery: add a `reconcile_stale_job(...)` path for `LlmEditJob` or explicitly mark old queued/running background-task jobs failed in the list/status endpoints. Unlike compile jobs, LLM edit jobs currently run as FastAPI background tasks; after a server restart there is no durable worker to complete an already persisted queued/running job.

### 3. Tests

- `GenerateDesignWindow.test.tsx`: add cases for hydrating history on project load, for resuming polling when **any** entry is still running, and for patch-in-place on terminal preserving in-browser `content`.
- `projectStorage.test.ts`: cover `listLlmEditConversation` happy path + error surfacing.

## Server tests

- `test_llm_file_edit.py`: add a test that `GET .../files/llm-edit/jobs` returns ordered entries with derived `content`/`compile` for succeeded and failed jobs, and empty `content` for in-flight jobs (with the optimistic overlay being a frontend concern).
- `test_repositories.py`: cover `LlmEditRepository.list_jobs_for_project` ordering + tenant isolation, and `get_compile_job_for_llm_edit`.
- New test: compile job created with `originating_llm_edit_job_id` is retrievable via the link; manual compile (no origin) returns `None`.

## Migration / rollout

1. Run migration `0009` (additive, nullable column + index — safe online).
2. Deploy backend (new list endpoint + origin threading).
3. Deploy frontend (hydrate + reconcile).
4. Backfill is **not required** for correctness — historical compile jobs simply won't link to their LLM edit, so old assistant messages show compile info as `null`. Acceptable; full history of prompts/results is still visible.

## Out of scope (future)

- Multi-conversation-per-project (would add a `conversation_id` on `LlmEditJob` + a `conversations` table).
- Editing/replaying past prompts.
- Truncation/retention policy for very long histories (the `limit=200` default is a soft cap; pagination can be added later).
- Persisting UI-only transient text (e.g. "Included N of M files" notices) — re-derived at view time from `requested_file_count` (exposed by the list endpoint) vs. the live project file count, so not stored.

## File touch-list

- `server/migrations/versions/0009_compile_job_originating_llm_edit.py` (new)
- `server/core/models.py` — `CompileJob.originating_llm_edit_job_id`
- `server/core/repositories.py` — `LlmEditRepository.list_jobs_for_project`, `get_compile_job_for_llm_edit`; compile create path
- `server/workflows/intus/intus_server.py` — new `GET .../files/llm-edit/jobs` list endpoint; compile POST accepts origin id
- `ui/src/workflows/shared/projectStorage.ts` — `listLlmEditConversation`, origin id on compile request
- `ui/src/workflows/generate/GenerateDesignWindow.tsx` — hydrate on load, patch-in-place on terminal, thread job_id into compile, resume polling for in-flight
- `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`, `ui/src/workflows/shared/projectStorage.test.ts`, `server/tests/test_llm_file_edit.py`, `server/tests/test_repositories.py`
