# Artus Intus AI Edit Integration Plan

> **For agentic workers:** Implement this task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Artus "AI Design Modification" control to the authenticated Intus multi-file LLM edit endpoint.

**Architecture:** Artus remains the feature/operation inspection UI and keeps deterministic variable updates on the Artus API. Natural-language AI edits use the shared authenticated project storage helper against the Intus API. The browser sends only project file metadata pointers and never receives LLM provider credentials.

**Primary endpoint:** `POST /api/intus/projects/{projectName}/files/llm-edit`

**Endpoint to stop using:** `POST /api/artus/ai_modify`

---

## Product Decisions

- Artus AI edit must include all metadata-backed project files up to the backend request limit.
- The backend request limit is currently 20 files.
- When `design.py` exists, it must be the first requested file.
- When `design.py` exists and has a complete file pointer, use its `id` as `active_file_id`.
- If more than 20 editable files exist, include `design.py` first plus the next 19 metadata-backed files.
- If files are truncated to the 20-file limit, show a non-fatal user-visible message that only the first 20 files were included because the backend caps AI edit requests at 20 files.
- Do not fall back to `design.py`-only behavior.
- Keep deterministic Artus variable edits unchanged.

## Existing Files

| Path | Role |
|------|------|
| `ui/src/workflows/artus/ui/FeatureTreeTab.tsx` | Artus feature tree UI and AI Design Modification prompt. Currently calls `/ai_modify`. |
| `ui/src/workflows/shared/projectStorage.ts` | Shared project storage abstraction. Already exposes `listFileMetadata(projectName)` and `applyLlmFileEdit(projectName, request)`. |
| `ui/src/workflows/intus/ui/CompilerTab.tsx` | Existing reference implementation for Intus AI file edit. |
| `ui/src/workflows/artus/ArtusWindow.tsx` | Passes Artus `serverUrl` into `FeatureTreeTab`; should not need changes. |
| `server/workflows/intus/intus_server.py` | Owns the authenticated Intus `files/llm-edit` route. Should not need changes. |
| `server/core/llm_client.py` | Owns LLM file edit request/response models and provider behavior. Should not need changes unless tests expose a contract mismatch. |
| `server/workflows/artus/artus_server.py` | Verify there is no active `/ai_modify` route. If one exists, remove it or convert it to a clear `410 Gone` deprecated response. |

## Implementation Steps

- [ ] Update `FeatureTreeTab.tsx` imports.
  - Add `useMemo` to the React import.
  - Add:

```ts
import {
  createProjectStorage,
  type ProjectFileMetadata,
} from '../../shared/projectStorage';
```

- [ ] Add an Intus URL derivation helper near top-level helper functions.

```ts
function deriveIntusServerUrl(artusServerUrl: string): string {
  const trimmed = artusServerUrl.replace(/\/+$/g, '');
  if (trimmed.endsWith('/artus')) {
    return `${trimmed.slice(0, -'/artus'.length)}/intus`;
  }
  return trimmed.replace('/api/artus', '/api/intus');
}
```

- [ ] Add authenticated Artus AI file state inside `AuthenticatedFeatureTreeTab`.

```ts
const [activeProject, setActiveProject] = useState('');
const [fileMetadata, setFileMetadata] = useState<ProjectFileMetadata[]>([]);

const intusServerUrl = useMemo(() => deriveIntusServerUrl(serverUrl), [serverUrl]);

const storage = useMemo(
  () => createProjectStorage({
    authMode: 'authenticated',
    serverUrl: intusServerUrl,
    getAccessToken,
  }),
  [getAccessToken, intusServerUrl],
);
```

- [ ] Split Artus feature loading so explicit mutation refreshes are not skipped by the polling visibility guard.
  - Keep `shouldRunPollingRequest()` for interval polling.
  - Use an unguarded loader for explicit calls after direct edits, AI edit success, and AI conflict refresh.
  - Acceptable shape:

```ts
const loadFeatures = useCallback(async () => {
  try {
    const res = await apiFetch(`${serverUrl}/features`, getAccessToken);
    const data = await res.json();
    if (res.ok) {
      setActiveProject(data.project_name || '');
      setFeatures(data.features || []);
      setOperations(data.operations || []);
      setError(null);
    } else {
      setActiveProject('');
      setFileMetadata([]);
      setError(data.error);
      setFeatures([]);
      setOperations([]);
    }
  } catch {
    setActiveProject('');
    setFileMetadata([]);
    setError('Failed to connect to Artus server.');
    setFeatures([]);
    setOperations([]);
  }
}, [serverUrl, getAccessToken]);

const fetchFeatures = useCallback(async () => {
  if (!shouldRunPollingRequest()) return;
  await loadFeatures();
}, [loadFeatures]);
```

- [ ] Add an editable metadata type guard.

```ts
type EditableFilePointer = ProjectFileMetadata & {
  id: string;
  updated_at: string;
};

function hasEditableFilePointer(file: ProjectFileMetadata): file is EditableFilePointer {
  return Boolean(file.id && file.updated_at);
}
```

- [ ] Build all-files AI request metadata just-in-time.
  - Keep `design.py` first.
  - Use only complete metadata pointers.
  - Return truncation information instead of only calling `setAiMessage`, because the final success message must preserve the cap warning.

```ts
const AI_EDIT_FILE_LIMIT = 20;

const loadAiEditFiles = useCallback(async () => {
  if (!activeProject) {
    throw new Error('No active project is selected.');
  }

  const latestMetadata = await storage.listFileMetadata(activeProject);
  setFileMetadata(latestMetadata);

  const designFile = latestMetadata.find(file => file.filename === 'design.py');
  const remainingFiles = latestMetadata.filter(file => file.filename !== 'design.py');
  const orderedFiles = [
    ...(designFile ? [designFile] : []),
    ...remainingFiles,
  ].filter(hasEditableFilePointer);

  if (orderedFiles.length === 0) {
    throw new Error('AI edit requires authenticated project file metadata. Reload the project and try again.');
  }

  const requestFiles = orderedFiles.slice(0, AI_EDIT_FILE_LIMIT);
  const truncatedMessage = orderedFiles.length > AI_EDIT_FILE_LIMIT
    ? `AI edit included ${AI_EDIT_FILE_LIMIT} of ${orderedFiles.length} files because the backend request limit is ${AI_EDIT_FILE_LIMIT}.`
    : '';

  return {
    requestFiles,
    activeFileId: designFile && hasEditableFilePointer(designFile) ? designFile.id : requestFiles[0]?.id,
    truncatedMessage,
  };
}, [activeProject, storage]);
```

- [ ] Replace `handleAiModify`.
  - Remove the `/api/artus/ai_modify` fetch.
  - Call `storage.applyLlmFileEdit`.
  - Preserve backend error text in `aiMessage`.
  - Refresh metadata and features after conflict errors.
  - Preserve the truncation warning in the final success message.

```ts
const handleAiModify = async () => {
  if (!prompt.trim() || !activeProject) return;

  setIsProcessing(true);
  setAiMessage(null);

  try {
    const { requestFiles, activeFileId, truncatedMessage } = await loadAiEditFiles();

    const result = await storage.applyLlmFileEdit(activeProject, {
      prompt: prompt.trim(),
      files: requestFiles.map(file => ({
        id: file.id,
        filename: file.filename,
        updated_at: file.updated_at,
      })),
      active_file_id: activeFileId,
      metadata: {
        source: 'artus_feature_tree',
        active_panel: activePanel,
        highlighted_node: highlightedNode || '',
      },
    });

    const changedMetadata = result.files.map(file => ({
      id: file.id,
      filename: file.filename,
      updated_at: file.updated_at,
    }));

    setFileMetadata(prev =>
      prev.map(existing => changedMetadata.find(file => file.id === existing.id) || existing)
    );

    const summaries = result.files
      .map(file => file.summary)
      .filter(Boolean)
      .join(' ');
    const successMessage = summaries
      ? `AI updated ${result.files.length} file(s). ${summaries}`
      : `AI updated ${result.files.length} file(s).`;

    setAiMessage([truncatedMessage, successMessage].filter(Boolean).join(' '));
    setPrompt('');
    setEdits({});
    await loadFeatures();
  } catch (error) {
    const message = error instanceof Error ? error.message : 'AI file edit failed.';
    setAiMessage(`Error: ${message}`);

    if (message.includes('Files changed while AI edit was running')) {
      try {
        await loadAiEditFiles();
        await loadFeatures();
      } catch {
        // Ignore secondary refresh failures; the original error is already shown.
      }
    }
  } finally {
    setIsProcessing(false);
  }
};
```

- [ ] Keep `handleDirectApply()` behavior on `/api/artus/update_features`.
  - It remains the deterministic AST variable update path.
  - After success, use the explicit unguarded feature loader.

- [ ] Update the AI button disabled state.

```ts
disabled={isProcessing || !prompt.trim() || !activeProject}
title={!activeProject ? 'Select or load a project before using AI edit' : undefined}
```

- [ ] Verify or clean up the Artus placeholder backend endpoint.
  - If `server/workflows/artus/artus_server.py` still has `AIRequest` and `@app.post("/ai_modify")`, delete them if tests do not depend on them.
  - If a transition response is preferred, return `410 Gone` with:

```py
{
    "success": False,
    "error": "Artus AI edit moved to the Intus file LLM edit endpoint.",
}
```

## Error Handling Requirements

- [ ] `409` conflict: show backend message `Files changed while AI edit was running. Reload and try again.`, then refresh metadata and features in the background.
- [ ] `422` no changes: show `AI did not apply changes` or the backend error.
- [ ] `429` usage limit: show backend usage-limit message.
- [ ] `503` not configured, billing, or generation failure: show backend error.
- [ ] Missing metadata: show `AI edit requires authenticated project file metadata. Reload the project and try again.`
- [ ] Feature load non-OK and `catch` paths must clear `activeProject` and `fileMetadata` so stale project context cannot enable AI edit.

## Tests

- [ ] Add or update Artus UI tests near `ui/src/workflows/artus/ui`.

Recommended cases:

- [ ] Calls Intus LLM edit endpoint.
  - Mock `/api/artus/features` with `project_name`.
  - Mock `/api/intus/projects/{name}/files` with multiple `file_metadata` entries.
  - Submit the Artus AI prompt.
  - Assert the POST goes to `/api/intus/projects/{name}/files/llm-edit`.
  - Assert no call goes to `/api/artus/ai_modify`.

- [ ] Sends all files with `design.py` first.
  - Use metadata for `helper.py`, `design.py`, and `parts.py`.
  - Assert request file order is `design.py`, `helper.py`, `parts.py`.
  - Assert `active_file_id` equals the `design.py` ID.

- [ ] Respects backend 20-file cap.
  - Mock at least 21 editable metadata files.
  - Assert request contains 20 files.
  - Assert `design.py` is included first.
  - Assert the user-visible final message still includes the 20-file cap warning after success.

- [ ] Clears prompt and refreshes features after success.
  - Mock successful LLM response with changed files and summaries.
  - Assert prompt clears.
  - Assert local variable edits clear.
  - Assert Artus features are fetched again using the explicit refresh path.
  - Assert success message shows changed file count.

- [ ] Shows backend conflict error.
  - Mock `409` response from `/files/llm-edit`.
  - Assert the conflict message is rendered.
  - Assert metadata and features refresh is attempted.

- [ ] Guest behavior remains unchanged.
  - Artus should show `GuestWorkflowNotice`.
  - AI edit controls should not be available to guest users.
  - No authenticated API calls should be made in guest mode.

## Commands

Frontend:

```bash
cd ui
npm run typecheck
npm run lint
npx vitest run
npm run build
```

Backend only if touching backend files:

```bash
uv run pytest server/tests
uv run mypy
```

## Acceptance Criteria

- [ ] Artus "Apply AI" no longer calls `/api/artus/ai_modify`.
- [ ] Artus "Apply AI" calls the Intus `files/llm-edit` endpoint through `projectStorage.applyLlmFileEdit`.
- [ ] Request includes all metadata-backed project files up to the backend 20-file limit.
- [ ] `design.py` is first when present.
- [ ] `active_file_id` is the `design.py` ID when present.
- [ ] A visible warning remains when requests are truncated to 20 files.
- [ ] Successful AI edits clear the prompt and local variable edit state.
- [ ] Successful AI edits refresh Artus features and operations using an explicit unguarded refresh.
- [ ] Direct `Apply Edits` behavior is unchanged.
- [ ] No LLM provider credentials are exposed to the frontend.
- [ ] Tests cover request shape, success refresh, conflict handling, cap warning behavior, and guest behavior.
