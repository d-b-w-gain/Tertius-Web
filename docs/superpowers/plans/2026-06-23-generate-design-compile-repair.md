# Generate Design Compile Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Generate Design compile failures caused by hallucinated build123d APIs, and automatically run one targeted repair attempt when generated code fails in the compile sandbox.

**Architecture:** Keep the configured `LLM_FILE_EDIT_SYSTEM_PROMPT` as the operator-owned prompt, but append repo-owned build123d runtime guardrails in `server/core/llm_client.py` so every environment gets the same compatibility constraints. Keep the repair loop in `GenerateDesignWindow`: the frontend already links LLM edit jobs to compile jobs and owns the post-edit compile orchestration, so it can submit one follow-up LLM edit with the compile traceback, then reuse the existing polling and compile path.

**Tech Stack:** FastAPI/Pydantic backend, React/Vite frontend, Vitest/Testing Library, pytest, OpenTelemetry traces for final live validation.

---

## Files

- Modify: `server/core/llm_client.py`
  - Add a small build123d runtime guardrail block appended to file-edit system messages.
- Modify: `server/tests/test_llm_client.py`
  - Assert the guardrail block is present and specifically bans `bd.RoundedPolygon`.
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
  - Add one-shot compile repair state and prompt construction.
  - Trigger repair only for Generate Design-linked compile failures that are retryable/sandbox errors.
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`
  - Cover the repair submission, one-attempt limit, and no repair on non-sandbox failures.
- Optional docs update: `docs/configuration-and-secrets.md`
  - Note that the secret prompt is augmented by repo-owned build123d runtime guardrails.

## Anti-Patterns

| Don't | Do Instead | Why |
|---|---|---|
| Replace `LLM_FILE_EDIT_SYSTEM_PROMPT` with a Python fallback | Append a narrow guardrail to the configured prompt | The prompt is intentionally supplied by Secret; missing prompt should still fail configuration. |
| Retry compile indefinitely | Allow one automatic repair attempt per assistant message / originating LLM edit | Prevents spend loops and confusing UI churn. |
| Trigger repair for every compile failure | Repair only `sandbox_error` or traceback-like generated-code failures | Queue, auth, and quota failures are not source-code repair problems. |
| Hide the original failure | Add a visible message that a repair attempt is running and why | User should understand why the design is being edited again. |
| Send secrets or telemetry IDs to the model | Send only the user-visible compile error/traceback and project files already selected for editing | Preserves telemetry and prompt safety boundaries. |

## Task 1: Append Build123d Runtime Guardrails

**Files:**
- Modify: `server/core/llm_client.py`
- Test: `server/tests/test_llm_client.py`

- [ ] **Step 1: Write the failing backend message-builder test**

Add this test near `test_build_file_edit_messages_uses_secret_system_prompt`:

```python
def test_build_file_edit_messages_appends_build123d_runtime_guardrails():
    request, files = _file_edit_request_and_files()

    messages = build_file_edit_messages(
        request,
        files,
        system_prompt="custom system prompt",
    )

    system_content = messages[0]["content"]
    assert system_content.startswith("custom system prompt")
    assert "build123d runtime guardrails" in system_content
    assert "Do not use bd.RoundedPolygon" in system_content
    assert "bd.Box" in system_content
    assert "bd.Cylinder" in system_content
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py::test_build_file_edit_messages_appends_build123d_runtime_guardrails -q
```

Expected: FAIL because the guardrail text is not appended yet.

- [ ] **Step 3: Add the guardrail constant and append helper**

In `server/core/llm_client.py`, add near `require_file_edit_system_prompt`:

```python
BUILD123D_RUNTIME_GUARDRAILS = """\
build123d runtime guardrails:
- Use only build123d APIs known to exist in this runtime; do not invent helpers, classes, or functions.
- Do not use bd.RoundedPolygon; it is not available.
- For rounded rectangular or handle-like geometry, prefer bd.Box, bd.Cylinder, bd.Sphere, bd.Cone, boolean operations, and fillets on resulting solids.
- Always produce code that can run with `import build123d as bd`.
- Avoid advanced builder-mode APIs unless they already appear in the supplied project files.
"""


def file_edit_system_prompt_with_runtime_guardrails(system_prompt: str) -> str:
    configured = require_file_edit_system_prompt(system_prompt)
    return f"{configured.rstrip()}\n\n{BUILD123D_RUNTIME_GUARDRAILS.strip()}"
```

Then change `build_file_edit_messages`:

```python
system_prompt = file_edit_system_prompt_with_runtime_guardrails(system_prompt)
```

- [ ] **Step 4: Run the focused backend tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

Expected: PASS.

## Task 2: Add One-Shot Compile Repair In Generate Design

**Files:**
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
- Test: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`

- [ ] **Step 1: Extend Generate Design message state**

Add repair state to `ChatMessage`:

```ts
repairAttempted?: boolean
repairForCompileJobId?: string
```

- [ ] **Step 2: Add helper predicates and repair prompt builder**

Near the existing helper functions in `GenerateDesignWindow.tsx`, add:

```ts
function isRepairableCompileFailure(data: CompileJobStatus) {
  const detail = `${data.error_code || ''}\n${data.error || ''}\n${data.user_message || ''}`.toLowerCase()
  return data.retryable !== false && (
    data.error_code === 'sandbox_error' ||
    detail.includes('traceback') ||
    detail.includes('attributeerror') ||
    detail.includes('nameerror') ||
    detail.includes('typeerror')
  )
}

function buildCompileRepairPrompt(originalPrompt: string, data: CompileJobStatus) {
  const failure = [
    data.error_code ? `Error code: ${data.error_code}` : '',
    data.user_message ? `User message: ${data.user_message}` : '',
    data.error ? `Traceback:\n${data.error}` : '',
  ].filter(Boolean).join('\n\n')
  return [
    'The previous generated design failed to compile in the Tertius build123d sandbox.',
    'Fix the Python source so it compiles successfully. Preserve the original design intent.',
    'Do not use APIs shown as missing in the traceback. Return the full corrected file content.',
    '',
    `Original user request:\n${originalPrompt}`,
    '',
    failure,
  ].join('\n')
}
```

- [ ] **Step 3: Add the repair submit function**

Add a callback next to `queueCompile`:

```ts
const submitCompileRepair = useCallback(async (
  projectName: string,
  assistantMessageId: string,
  failedCompileJobId: string,
  data: CompileJobStatus,
) => {
  const currentMessage = messagesRef.current.find(message => message.id === assistantMessageId)
  if (!currentMessage || currentMessage.repairAttempted) return false
  const originalPrompt = messagesRef.current.find(message => message.id === promptMessageId(currentMessage.jobId || ''))?.content || prompt
  const { requestFiles, activeFileId } = await buildLlmEditRequest()
  const repairPrompt = buildCompileRepairPrompt(originalPrompt || 'Generate a design.', data)
  const job = await storage.applyLlmFileEditJob(projectName, {
    prompt: repairPrompt,
    files: requestFiles.map(file => ({
      id: file.id,
      filename: file.filename,
      updated_at: file.updated_at,
    })),
    active_file_id: activeFileId,
    model_id: selectedModel?.id,
    metadata: { source: 'generate_design_compile_repair' },
  })
  updateAssistantMessage(assistantMessageId, current => ({
    ...current,
    content: `${current.content}\n\nCompile failed; attempting one automatic repair.`,
    compileStatus: 'running',
    repairAttempted: true,
    repairForCompileJobId: failedCompileJobId,
    jobId: job.job_id,
  }))
  startLlmEditPolling(projectName, job.job_id, assistantMessageId)
  return true
}, [buildLlmEditRequest, prompt, selectedModel?.id, startLlmEditPolling, storage, updateAssistantMessage])
```

If `messagesRef` does not exist, create it with:

```ts
const messagesRef = useRef(messages)
useEffect(() => {
  messagesRef.current = messages
}, [messages])
```

- [ ] **Step 4: Trigger repair from failed compile polling**

In `pollCompileJob`, inside `if (data.status === 'failed')`, before marking the message failed, add:

```ts
if (isRepairableCompileFailure(data)) {
  const repairStarted = await submitCompileRepair(projectName, assistantMessageId, jobId, data)
  if (repairStarted) {
    clearCompileTimer(jobId)
    compileRequestRef.current.delete(jobId)
    setStatusText('Compile failed; automatic repair is running.')
    return
  }
}
```

Add `submitCompileRepair` to the `pollCompileJob` callback dependencies.

- [ ] **Step 5: Add frontend tests**

In `GenerateDesignWindow.test.tsx`, add:

```ts
it('runs one automatic repair when generated design compile fails with sandbox_error', async () => {
  // Arrange existing Generate Design success response, first compile failed status with
  // error_code sandbox_error and AttributeError for bd.RoundedPolygon, then repair LLM
  // edit success and second compile success.
  // Assert two llm-edit job submissions were made.
  // Assert the second prompt includes the compile traceback and original user request.
  // Assert metadata.source is generate_design_compile_repair.
  // Assert the final assistant message reaches compileStatus succeeded.
})

it('does not auto-repair non-sandbox compile failures', async () => {
  // Arrange compile status failed with error_code source_bundle_too_large and retryable false.
  // Assert only one llm-edit job submission was made and the message remains failed.
})

it('does not run more than one automatic repair for the same assistant message', async () => {
  // Arrange initial compile failure, repair edit success, repair compile failure.
  // Assert exactly two llm-edit job submissions total: original plus one repair.
})
```

Use the existing fetch mocks in this test file rather than introducing new test helpers.

- [ ] **Step 6: Run focused frontend tests**

Run:

```bash
rtk npm --prefix ui test -- GenerateDesignWindow.test.tsx
```

Expected: PASS.

## Task 3: Documentation And Validation

**Files:**
- Modify: `docs/configuration-and-secrets.md`

- [ ] **Step 1: Document the prompt layering**

Add under the `LLM_FILE_EDIT_SYSTEM_PROMPT` section:

```markdown
The configured file-edit system prompt is augmented at runtime with repo-owned
build123d compatibility guardrails. The Secret remains required; the guardrails
only add runtime-specific API constraints such as avoiding unavailable build123d
helpers.
```

- [ ] **Step 2: Run backend and frontend focused gates**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py server/tests/test_llm_file_edit.py -q
rtk npm --prefix ui test -- GenerateDesignWindow.test.tsx
```

Expected: PASS.

- [ ] **Step 3: Run full live validation**

Because this changes Generate Design and AI-edit-linked compile behavior, run full live-flow, not compile-only:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
UI_LOCAL_PORT=18083 API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 TRACES_LOCAL_PORT=10431 \
scripts/harness-k3s.sh live-flow
```

Expected: authenticated Generate Design edit succeeds; if the first compile fails with a repairable sandbox error, one repair LLM edit is queued and the repaired compile is attempted.

## Open Decisions

- The repair loop is frontend-driven in this plan. If we later need repair to happen for API-only clients, add a backend repair endpoint instead of duplicating the browser logic.
- The first implementation should allow exactly one repair attempt. More attempts require explicit user action or a product decision on budget limits.
