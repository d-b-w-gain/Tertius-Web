# Generate Design UI and Historical Model Endpoint Plan

**Goal:** Rework the app to emphasize AI design editing through a new Generate Design tab, add a daily tenant AI budget gauge, and add an authenticated endpoint that serves specific historical model artifacts by ID.

## Current State

- Extus serves only the latest active-project model:
  - `GET /status`
  - `GET /model`
- The latest-model lookup is tenant scoped and active-project scoped in `server/workflows/extus/extus_server.py`.
- Intus compile job status already returns `artifact_id` for successful jobs:
  - `GET /projects/{name}/compile/jobs/{job_id}`
- Model artifacts are stored in `Artifact` rows with:
  - `tenant_id`
  - `project_id`
  - `kind`
  - `content_type`
  - `content`
- Historical conversation UI needs a stable URL for a selected message's artifact ID.
- The current AI edit UI exists in two places:
  - Artus sidebar bottom panel: `FeatureTreeTab.tsx`
  - Intus compiler toolbar: `CompilerTab.tsx`
- LLM edit responses already return token usage:
  - `usage.prompt_tokens`
  - `usage.completion_tokens`
  - `usage.total_tokens`
- Daily LLM quota enforcement already exists in `core.llm_usage.assert_llm_usage_allowed`, but there is no frontend-facing budget summary endpoint yet.

## App Shell UI

Modify `ui/src/App.tsx`.

1. Add a new top-level tab:
   - id: `generate`
   - label: `Generate Design`
2. Prefer making `generate` the default active tab so the AI edit path is the first experience.
3. Keep existing workflow tabs:
   - `Extus Viewport`
   - `Intus Compiler`
   - `Timus Drafting`
4. Render the new tab with:
   - `GenerateDesignWindow isActive={activeTab === 'generate'}`
5. Add the budget gauge as a fixed bottom-left app-shell component so it is visible across workflow tabs.

## Generate Design Tab

Add:

```text
ui/src/workflows/generate/GenerateDesignWindow.tsx
```

Layout:

1. Full height.
2. Left panel: `50%` width.
3. Right panel: `50%` width.
4. On narrow screens, stack vertically with conversation first and viewer second.

Left panel requirements:

1. Large conversation-style window showing all past messages for the active project.
2. User prompts and assistant outcomes are separate message bubbles.
3. Every submitted prompt remains visible after generation.
4. Clicking a past message with an `artifact_id` selects that message and displays its generated model in the right viewer.
5. Prompt composer is fixed at the bottom of the left panel.
6. The generate button is visually prominent:
   - larger than the current `AI Edit` / `Apply AI` buttons
   - high contrast
   - disabled only when no prompt, no active project, project metadata is unavailable, or a request is running
7. Show inline states:
   - generating/editing
   - compiling
   - compile failed
   - AI returned no change
   - AI could not complete
   - selected historical model unavailable

Right panel requirements:

1. Reuse the same model viewer behavior as Extus.
2. Display the latest active project model by default.
3. Display a specific historical model when a clicked conversation message has an `artifact_id`.
4. Show the selected message summary/status near the viewer without covering the model.

## Viewer Refactor

Modify:

```text
ui/src/workflows/extus/ui/ViewerTab.tsx
```

Extract reusable viewer pieces:

1. A low-level Three.js canvas component that accepts a model URL:
   - `ModelViewerCanvas`
   - responsible for scene setup, GLTF loading, orbit controls, grid, authored colors, resize behavior
2. A latest-model wrapper:
   - `LatestModelViewer`
   - owns `/status`, `/project_name`, and `/model?t=...` polling
3. `ViewerTab` remains the authenticated/guest Extus workflow wrapper and should preserve current behavior.

Generate Design should use:

1. `LatestModelViewer` when no historical message is selected.
2. `ModelViewerCanvas` with an explicit artifact URL when a historical message is selected.

## AI Edit Flow

Reuse and consolidate existing logic from:

```text
ui/src/workflows/artus/ui/FeatureTreeTab.tsx
ui/src/workflows/intus/ui/CompilerTab.tsx
ui/src/workflows/shared/projectStorage.ts
```

Generate flow:

1. Resolve the active project from Intus storage.
2. Load project file metadata.
3. Build the LLM edit file request:
   - include `design.py` first when available
   - include only files with `id` and `updated_at`
   - cap to `AI_EDIT_FILE_LIMIT`
4. Call:
   - `projectStorage.applyLlmFileEdit(activeProject, request)`
5. Record the assistant outcome in the conversation.
6. If outcome is `changed`, queue a compile:
   - endpoint: `POST /api/intus/projects/{projectName}/compile`
   - default `export_format`: `glb`
   - default `quality`: `sketch`
   - include the `code` field required by `CompileRequest`; use the changed `design.py` content from the LLM edit result when available, otherwise use the active Python file content returned by the edit response
7. Poll compile job status:
   - `GET /api/intus/projects/{projectName}/compile/jobs/{job_id}`
8. Store `artifact_id` on the assistant message when compile succeeds.
9. Select that message automatically so the new model appears in the right viewer.
10. Refresh the budget gauge after any valid LLM edit response.

## Conversation History

Initial implementation can keep conversation history in browser state, but the UI requirement says "all past messages." For durable history, add backend persistence.

Recommended backend-backed shape:

```text
GET /projects/{name}/ai-edits/history
POST internal write during /files/llm-edit + compile association
```

Minimum viable implementation:

1. Store conversation state in React while the tab is mounted.
2. Include:
   - prompt text
   - outcome
   - assistant message
   - model
   - usage
   - snapshot id
   - compile job id
   - artifact id
   - created timestamp
3. Plan a follow-up migration/table if persistence across reloads is required.

Durable implementation option:

1. Add an AI edit conversation table keyed by tenant/project.
2. Or extend the existing `llm_usage_records.metadata_json` with compile job/artifact linkage and expose a project-scoped history endpoint.
3. Prefer a dedicated table if this history becomes product UI, because `llm_usage_records` is billing/audit data and should not become the only UI state store.

## Budget Gauge UI

Add:

```text
ui/src/workflows/generate/AiBudgetGauge.tsx
```

Render from `App.tsx` fixed in the bottom-left corner.

Display:

1. Tenant daily AI budget remaining.
2. Tokens used today.
3. Last edit cost.

Use "tokens" as the initial cost unit because the LLM edit response currently exposes token usage, not money.

Gauge behavior:

1. Poll on mount.
2. Refresh after successful or valid non-changing LLM edit outcomes.
3. Hide or show a compact unavailable state for guests or unauthorized users.
4. Use a small radial or horizontal gauge with stable dimensions.

## LLM Budget Endpoint

Add an authenticated endpoint under Intus because LLM usage and quota are enforced there:

```http
GET /llm-usage/today
```

Response shape:

```json
{
  "tenant_daily_token_quota": 3200000,
  "tenant_tokens_used_today": 12000,
  "tenant_tokens_remaining_today": 3188000,
  "user_daily_token_quota": 3200000,
  "user_tokens_used_today": 4000,
  "user_tokens_remaining_today": 3196000,
  "last_edit": {
    "operation": "files.llm_edit",
    "model": "provider-model",
    "prompt_tokens": 1000,
    "completion_tokens": 500,
    "total_tokens": 1500,
    "created_at": "2026-06-19T12:00:00Z"
  }
}
```

Implementation:

1. Add response models in `server/core/usage_messages.py` or a new LLM usage messages module.
2. Add query helpers in `server/core/llm_usage.py` or `UsageRepository`.
3. Use `LlmUsageRecord`.
4. Calculate day start in UTC, matching `assert_llm_usage_allowed`.
5. Return `404` or hidden `403` only if auth policy requires owner-only visibility; otherwise authenticated tenant membership is enough for a personal usage gauge.
6. Include the LLM usage router from `server/workflows/intus/intus_server.py` separately from the owner-only `/usage/*` compile billing routes.

## Historical Model Endpoint

Add to `server/workflows/extus/extus_server.py`:

```http
GET /artifacts/{artifact_id}/model
```

Response:

- `200`: raw artifact bytes with the stored `content_type`
- `404`: artifact does not exist, belongs to another tenant, belongs to a different active project, has no inline content, or is not a supported model kind

Supported model artifact kinds:

- `gltf`
- `glb`
- `stl`

## Access Rules

The endpoint must:

1. Require normal Extus authentication via `get_auth_context`.
2. Filter by `Artifact.tenant_id == ctx.tenant_id`.
3. Filter by `Artifact.id == artifact_id`.
4. Filter by the active project, matching the existing `/model` behavior.
5. Reject artifacts whose `kind` is not one of `gltf`, `glb`, or `stl`.
6. Reject artifacts with missing `content`.

Project scoping:

- Use active-project scoping for now because Generate Design history is project-local.
- If future UI supports browsing history across projects, add an explicit project-scoped route instead of silently serving any tenant artifact from Extus.

## Historical Model Implementation Steps

1. Add a helper in `extus_server.py`:
   - `get_model_artifact_by_id(db, ctx, artifact_id)`
   - Query `Artifact` by tenant, ID, active project, and model kinds.
2. Add route:
   - `@app.get("/artifacts/{artifact_id}/model")`
   - Return `JSONResponse(status_code=404, ...)` for missing or empty content.
   - Return `Response(content=artifact.content, media_type=artifact.content_type)`.
3. Keep existing `/model` behavior unchanged.
4. Add backend tests in `server/tests/test_workflow_isolation.py`:
   - Serves an older artifact by explicit ID even when a newer latest artifact exists.
   - Returns `404` for another tenant's artifact ID.
   - Returns `404` for a same-tenant artifact from a different inactive project.
   - Returns `404` for non-model artifact kinds.
   - Returns `404` when content is missing.
5. Build frontend historical URLs as:

```ts
`${extusServerUrl}/artifacts/${artifactId}/model?t=${encodeURIComponent(createdAtOrArtifactId)}`
```

## Tests

Backend:

```bash
pytest server/tests/test_workflow_isolation.py -q
pytest server/tests/test_usage_endpoints.py -q
```

Frontend:

```bash
cd ui
npm test -- GenerateDesign
npm test -- ViewerTab
npm test -- AiBudgetGauge
```

Add or update tests for:

1. App shell renders the Generate Design tab.
2. Generate Design uses a 50/50 split on desktop.
3. Generate button is prominent and follows disabled states.
4. Submitting a prompt calls `/files/llm-edit`.
5. Changed edit queues compile and stores `artifact_id`.
6. Clicking a past message switches the viewer to `/artifacts/{artifact_id}/model`.
7. Budget gauge renders tenant remaining tokens and last edit tokens.
8. Historical model endpoint rejects cross-tenant and wrong-project artifacts.

## Risks

- If active-project scoping is enforced, clicking a historical message from a different project will return `404`. That is acceptable for the initial Generate Design tab if the conversation is project-local.
- If artifacts later move to object storage only, this endpoint should stream from the storage backend when `Artifact.content` is absent.
- STL support in the existing viewer may be weaker than GLB/GLTF; the Generate Design flow should prefer compiling `glb`.
- Durable conversation history likely needs a small backend schema addition. Keeping it only in React state satisfies the first UI build but not cross-reload history.
