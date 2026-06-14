# Compile Preflight Hang Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Intus from getting stuck on `Compiling...` when a compile request hangs before the backend creates a job.

**Architecture:** Split compile UI state into a short preflight phase and a backend-tracked job phase. Auth/token acquisition and the initial compile POST must be bounded by timeouts and must always settle the UI state; only a returned `job_id` enters job polling.

**Tech Stack:** React, TypeScript, Vite, Vitest, React Testing Library, existing `apiFetch`, existing Intus compile API.

---

## Context

Live production evidence on 2026-06-13 showed:

- `johnson` had no queued or running compile jobs in Postgres.
- NATS JetStream had no pending compile messages and two workers waiting.
- The API and UI logs had no `POST /api/intus/projects/default_purlin/compile` during the reported hang.
- The UI can set `isCompiling=true` before `apiFetch()` completes `getAccessToken()`, so a stalled token lookup or initial POST can leave the button stuck on `Compiling...` without any backend job.

## Deep Links

- Compile UI state: `ui/src/workflows/intus/ui/CompilerTab.tsx`
- API auth wrapper: `ui/src/api/client.ts`
- Auth token source: `ui/src/auth/AuthProvider.tsx`
- Existing compile tests: `ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx`
- Existing guest compile tests: `ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx`
- Backend compile enqueue endpoint: `server/workflows/intus/intus_server.py`
- Worker execution path: `server/workflows/intus/compile_worker.py`

## Anti-Patterns (DO NOT)

| Don't | Do Instead | Why |
|---|---|---|
| Treat preflight/auth as a real compile job | Use separate `queueing` and `compiling` states | Only `job_id` polling is backend recoverable |
| Leave `isCompiling` true after an auth/fetch timeout | Always reset state in bounded failure paths | Prevents indefinite disabled button |
| Add backend cleanup for a missing job | Fix the frontend pre-job state | The backend has nothing to clean when no POST arrives |
| Poll `/compile/jobs/...` without a returned `job_id` | Poll only after a successful `202` with `job_id` | Avoids phantom state |
| Hide timeout failures as generic fatal errors | Show specific retryable log text | User needs to know retry is safe |
| Add global fetch timeouts to all API calls | Scope timeout to compile preflight first | Reduces blast radius |
| Auto-retry compile POST after timeout | Let the user retry manually | A late POST could otherwise create duplicate jobs |

## Error Handling Matrix

| Error Type | Detection | UI State | Log Message | Retry |
|---|---|---|---|---|
| Token acquisition timeout | Preflight exceeds timeout before `apiFetch` sends request | Reset to idle | `[ERROR] Compile could not start because authentication timed out. Please try again or sign in again.` | Manual retry |
| Initial compile POST timeout | POST does not resolve within timeout | Reset to idle | `[ERROR] Compile request timed out before a job was created. Please try again.` | Manual retry |
| Initial compile POST non-2xx with `job_id` absent | `!res.ok || !data.job_id` | Reset to idle | Existing backend `user_message`, `short`, or `error` | Existing retry flag |
| Job polling timeout/network blip | Existing polling catch path | Stay compiling while polling retries | No new terminal log | Existing polling retry |
| Job terminal failure | `data.status === 'failed'` | Reset to idle | Existing failure message and details | Existing retry flag |
| Job terminal success | `data.status === 'succeeded'` | Reset to idle | Existing success message | N/A |

## Test Case Specifications

| Test ID | Component | Setup | Expected |
|---|---|---|---|
| TC-001 | `CompilerTab` manual compile | `getAccessToken` promise never resolves | Button returns from `Compiling...` after preflight timeout and shows auth timeout log |
| TC-002 | `CompilerTab` manual compile | `apiFetch` promise never resolves after token succeeds | Button returns from `Compiling...` after initial POST timeout and shows request timeout log |
| TC-003 | `CompilerTab` manual compile | `apiFetch` returns `{ success: true, job_id: "job-1" }` then polling succeeds | Existing success behavior remains unchanged |
| TC-004 | `CompilerTab` auto compile | Initial POST timeout occurs in auto mode | Auto-compile is disabled and UI state resets |
| TC-005 | `CompilerTab` retry behavior | Timeout occurs, user clicks compile again, second POST succeeds | Second click sends exactly one compile POST and polls returned job |
| TC-006 | `apiFetch` behavior | Existing readonly request tests | No global readonly dedupe/backoff behavior changes |

## File Structure

- Modify `ui/src/workflows/intus/ui/CompilerTab.tsx`
  - Add compile preflight timeout helper or local timeout wrapper.
  - Represent compile phase as `idle | queueing | compiling`, or keep `isCompiling` plus a visible phase label if smaller.
  - Ensure every pre-job failure clears state.
- Modify `ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx`
  - Add timeout regression tests for token hang, POST hang, and retry.
- Optionally modify `ui/src/api/client.ts`
  - Only if a small exported helper is needed for abortable timeout behavior.
  - Avoid changing global fetch semantics unless tests prove it is necessary.

## Task 1: Add Failing Regression Tests

**Files:**
- Modify: `ui/src/workflows/intus/ui/CompilerTab.compile.test.tsx`

- [ ] **Step 1: Use fake timers in the compile test file**

Add timer cleanup around the existing compile job tests if not already present:

```ts
beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.runOnlyPendingTimers()
  vi.useRealTimers()
})
```

If this conflicts with existing tests, scope fake timers inside only the new timeout tests.

- [ ] **Step 2: Add token timeout regression**

Add a test named `manual compile resets when authentication preflight times out`:

```ts
it('manual compile resets when authentication preflight times out', async () => {
  const getAccessToken = vi.fn(() => new Promise<string>(() => {}))
  renderCompilerTab({ getAccessToken })

  fireEvent.click(await screen.findByRole('button', { name: /compile/i }))
  expect(screen.getByRole('button', { name: /compiling/i })).toBeDisabled()

  await act(async () => {
    vi.advanceTimersByTime(15_000)
  })

  expect(screen.getByRole('button', { name: /compile & export/i })).not.toBeDisabled()
  expect(screen.getByText(/authentication timed out/i)).toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalledWith(
    expect.stringContaining('/compile'),
    expect.objectContaining({ method: 'POST' }),
  )
})
```

Use the existing render helper and fetch mock names from the file; keep the assertion shape equivalent if helper names differ.

- [ ] **Step 3: Add initial POST timeout regression**

Add a test named `manual compile resets when job creation request times out`:

```ts
it('manual compile resets when job creation request times out', async () => {
  fetchMock.mockImplementationOnce(() => new Promise<Response>(() => {}))
  renderCompilerTab()

  fireEvent.click(await screen.findByRole('button', { name: /compile/i }))
  expect(screen.getByRole('button', { name: /compiling/i })).toBeDisabled()

  await act(async () => {
    vi.advanceTimersByTime(20_000)
  })

  expect(screen.getByRole('button', { name: /compile & export/i })).not.toBeDisabled()
  expect(screen.getByText(/request timed out before a job was created/i)).toBeInTheDocument()
})
```

- [ ] **Step 4: Add retry-after-timeout regression**

Add a test named `manual compile can be retried after a pre-job timeout`:

```ts
it('manual compile can be retried after a pre-job timeout', async () => {
  fetchMock
    .mockImplementationOnce(() => new Promise<Response>(() => {}))
    .mockResolvedValueOnce(jsonResponse({ success: true, job_id: 'job-2', status: 'queued' }))
    .mockResolvedValueOnce(jsonResponse({
      job_id: 'job-2',
      status: 'succeeded',
      format: 'glb',
      artifact_id: 'artifact-2',
      finished_at: '2026-06-13T00:00:00Z',
    }))

  renderCompilerTab()

  fireEvent.click(await screen.findByRole('button', { name: /compile/i }))
  await act(async () => {
    vi.advanceTimersByTime(20_000)
  })

  fireEvent.click(screen.getByRole('button', { name: /compile & export/i }))
  await act(async () => {
    vi.advanceTimersByTime(1_000)
  })

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/intus/projects/default_purlin/compile',
    expect.objectContaining({ method: 'POST' }),
  )
  expect(fetchMock).toHaveBeenCalledWith(
    '/api/intus/projects/default_purlin/compile/jobs/job-2',
    expect.anything(),
  )
})
```

- [ ] **Step 5: Run the failing focused tests**

Run:

```bash
cd ui
npm run test -- CompilerTab.compile.test.tsx
```

Expected before implementation: the new timeout tests fail because `Compiling...` never resets.

## Task 2: Implement Bounded Compile Preflight

**Files:**
- Modify: `ui/src/workflows/intus/ui/CompilerTab.tsx`

- [ ] **Step 1: Add local constants near the top of the file**

```ts
const COMPILE_AUTH_TIMEOUT_MS = 15_000;
const COMPILE_CREATE_JOB_TIMEOUT_MS = 20_000;
```

- [ ] **Step 2: Add a local timeout helper**

Place this helper above `CompilerTab`:

```ts
async function withTimeout<T>(promise: Promise<T>, timeoutMs: number, message: string): Promise<T> {
  let timeoutId: number | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
  });

  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timeoutId !== undefined) {
      window.clearTimeout(timeoutId);
    }
  }
}
```

- [ ] **Step 3: Split token acquisition from the compile POST inside `startCompile`**

Replace the direct `apiFetch(..., getAccessToken, ...)` call with explicit token preflight and a token supplier:

```ts
const token = await withTimeout(
  getAccessToken(),
  COMPILE_AUTH_TIMEOUT_MS,
  'Compile could not start because authentication timed out. Please try again or sign in again.',
);

const res = await withTimeout(
  apiFetch(`${serverUrl}/projects/${projectName}/compile`, async () => token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: nextCode, export_format: format, quality, file: activeFileRef.current }),
  }),
  COMPILE_CREATE_JOB_TIMEOUT_MS,
  'Compile request timed out before a job was created. Please try again.',
);
```

- [ ] **Step 4: Preserve existing backend error behavior**

Keep this existing branch after `const data = await res.json()`:

```ts
if (!res.ok || !data.job_id) {
  setLog(`[ERROR] ${data.user_message || data.short || data.error || 'Failed to queue compile job'}`);
  setFailedCompileRetry(data.retryable ? { code: nextCode } : null);
  if (mode === 'auto') setAutoCompile(false);
  setCompilingState(false);
  return;
}
```

- [ ] **Step 5: Make the catch block display timeout messages**

Replace the generic catch body in `startCompile` with:

```ts
} catch (error) {
  const message = error instanceof Error ? error.message : 'Failed to reach server during compile.';
  setLog(`[ERROR] ${message}`);
  setFailedCompileRetry({ code: nextCode });
  if (mode === 'auto') setAutoCompile(false);
  setCompilingState(false);
}
```

This keeps manual retry available after pre-job failures without auto-resubmitting duplicate jobs.

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd ui
npm run test -- CompilerTab.compile.test.tsx
```

Expected: new and existing compile tests pass.

## Task 3: Keep Auth/API Tests Stable

**Files:**
- Test: `ui/src/auth/AuthProvider.test.tsx`
- Test: `ui/src/api/client.test.ts`
- Test: `ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx`

- [ ] **Step 1: Run auth and API tests**

Run:

```bash
cd ui
npm run test -- AuthProvider.test.tsx client.test.ts CompilerTab.guest.test.tsx
```

Expected: all pass. This confirms the compile-specific timeout did not regress guest mode, token handling, or readonly request behavior.

- [ ] **Step 2: If fake timers leak, scope timers to the new tests**

If unrelated tests fail because fake timers changed scheduling, remove file-level fake timers and wrap each timeout test with:

```ts
vi.useFakeTimers();
try {
  // test body
} finally {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
}
```

Then rerun the same command.

## Task 4: Full Frontend Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run UI tests**

Run:

```bash
cd ui
npm run test
```

Expected: all Vitest suites pass.

- [ ] **Step 2: Run UI build**

Run:

```bash
cd ui
npm run build
```

Expected: Vite production build completes.

- [ ] **Step 3: Run lint if available**

Run:

```bash
cd ui
npm run lint
```

Expected: lint passes, or if this repo's lint script is currently absent/broken, record the exact output.

## Task 5: Optional Live Smoke After Merge/Deploy

**Files:**
- No code changes expected.

- [ ] **Step 1: Watch live logs while clicking Compile**

Run:

```bash
rtk kubectl --kubeconfig=/home/johnson/.kube/config logs -n tertius deploy/tertius-ui -f --since=1m
```

Expected when compile starts: one `POST /api/intus/projects/default_purlin/compile`.

- [ ] **Step 2: Check backend job state**

Run:

```bash
rtk kubectl --kubeconfig=/home/johnson/.kube/config exec -n tertius tertius-postgres-1 -- \
  psql -U postgres -d tertius -c "select id, status, created_at, finished_at, error from compile_jobs order by created_at desc limit 5;"
```

Expected: new job reaches `succeeded` or `failed`; it must not remain missing while the UI says `Compiling...`.

- [ ] **Step 3: Check queue state**

Run:

```bash
rtk kubectl --kubeconfig=/home/johnson/.kube/config exec -n tertius tertius-nats-0 -c nats -- \
  sh -lc 'wget -qO- http://127.0.0.1:8222/jsz?consumers=true | head -c 12000'
```

Expected: compile consumer has no unbounded `num_ack_pending` or `num_pending` growth after the job settles.

## Self-Review

- Spec coverage: Covers the observed no-POST/no-job UI hang and preserves existing server-side job polling behavior.
- Placeholder scan: No `TBD`, broad "handle edge cases", or unspecified test steps.
- Type consistency: Uses existing `getAccessToken`, `apiFetch`, `setCompilingState`, `setFailedCompileRetry`, `setAutoCompile`, and `pollCompileJob` concepts already present in `CompilerTab.tsx`.

