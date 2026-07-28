# Pi Progress Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Generate Design progress visible from submission through
completion without changing the healthy Pi progress transport or privacy
contract.

**Architecture:** Keep progress attached to the existing assistant message and
render one uncontrolled native disclosure throughout the current run. Add a
frontend-only marker for current-session runs, derive the closed-panel badge
from existing active messages, and scroll only the newly submitted assistant
card into view.

**Tech Stack:** React 19, TypeScript 6, Tailwind CSS 4, Vitest 4, Testing Library

**Approved design:**
[`docs/superpowers/specs/2026-07-29-pi-progress-visibility-design.md`](../specs/2026-07-29-pi-progress-visibility-design.md)

---

## File Structure

| File | Responsibility |
|---|---|
| `ui/src/workflows/generate/GenerateDesignWindow.tsx` | Current-run marker, disclosure lifecycle, pending/complete copy, active badge, and one-time scroll |
| `ui/src/workflows/generate/GenerateDesignWindow.test.tsx` | Component and interaction regression coverage |
| `docs/superpowers/specs/2026-07-27-pi-progress-events-design.md` | Pointer from the original architecture spec to the corrective lifecycle |
| `docs/superpowers/specs/2026-07-29-pi-progress-visibility-design.md` | Single source of truth for corrected visibility behavior |
| `docs/superpowers/plans/2026-07-29-pi-progress-visibility.md` | TDD execution and validation record |

## Task 1: Preserve a visible disclosure through the current run

**Files:**

- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`

- [ ] **Step 1: Replace terminal-collapse expectations and add failing lifecycle tests**

Update the existing progress tests so their assertions express the approved
contract:

```tsx
it('shows pending progress immediately and preserves the open disclosure at completion', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  storage.getLlmFileEditJob
    .mockResolvedValueOnce({ job_id: 'llm-job-1', status: 'running', progress: null })
    .mockResolvedValueOnce({
      job_id: 'llm-job-1',
      status: 'running',
      progress: piProgressSnapshot(),
    })
    .mockResolvedValueOnce({
      job_id: 'llm-job-1',
      status: 'succeeded',
      progress: piProgressSnapshot(),
      result: {
        success: true,
        outcome: 'no_change',
        message: 'No edits needed.',
        model: 'test-model',
        usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
        snapshot: null,
        files: [],
      },
    })

  render(<GenerateDesignWindow />)
  await screen.findByText('Latest model viewer')
  openGenerateDesignConversation()
  fireEvent.change(
    screen.getByPlaceholderText('Describe the CAD design or modification...'),
    { target: { value: 'inspect and refine the model' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

  const summary = await screen.findByText('Thinking & activity')
  const details = summary.closest('details')
  expect(details).toHaveAttribute('open')
  expect(screen.getByText('Starting')).toBeInTheDocument()
  expect(screen.getByText('0 updates')).toBeInTheDocument()
  expect(screen.getByText('Waiting for the first progress update…')).toBeInTheDocument()

  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  expect(screen.getByText('Working')).toBeInTheDocument()
  expect(screen.getByText('Inspecting the model structure.')).toBeInTheDocument()

  await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
  await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
  expect(details).toHaveAttribute('open')
  expect(screen.getByText('Complete')).toBeInTheDocument()
})

it('preserves a manual close through progress and completion', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  storage.getLlmFileEditJob
    .mockResolvedValueOnce({
      job_id: 'llm-job-1',
      status: 'running',
      progress: piProgressSnapshot(),
    })
    .mockResolvedValueOnce({
      job_id: 'llm-job-1',
      status: 'succeeded',
      progress: piProgressSnapshot(),
      result: {
        success: true,
        outcome: 'no_change',
        message: 'No edits needed.',
        model: 'test-model',
        usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
        snapshot: null,
        files: [],
      },
    })

  render(<GenerateDesignWindow />)
  await screen.findByText('Latest model viewer')
  openGenerateDesignConversation()
  fireEvent.change(
    screen.getByPlaceholderText('Describe the CAD design or modification...'),
    { target: { value: 'inspect and refine the model' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

  const details = (await screen.findByText('Thinking & activity')).closest('details')
  fireEvent.click(screen.getByText('Thinking & activity'))
  expect(details).not.toHaveAttribute('open')
  await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
  await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
  expect(details).not.toHaveAttribute('open')
})
```

Rename the hydrated-history assertions from `Activity` to
`Thinking & activity`, assert `Complete`, and retain the collapsed-history
expectation. Retain the historical no-progress test: a terminal history entry
with `progress: null` has no disclosure.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
rtk npm test -- src/workflows/generate/GenerateDesignWindow.test.tsx
```

Expected: failures because the pending disclosure, new labels, persistent open
state, and manual-close behavior do not exist.

- [ ] **Step 3: Implement the minimal lifecycle state and disclosure**

Add the current-session marker:

```tsx
type ChatMessage = {
  // existing fields
  progress?: LlmEditProgressSnapshot
  progressActive?: boolean
  progressDisclosure?: boolean
}
```

Replace `ProgressActivity` with an optional-progress, uncontrolled disclosure:

```tsx
function ProgressActivity({
  progress,
  active,
  defaultOpen,
}: {
  progress?: LlmEditProgressSnapshot
  active: boolean
  defaultOpen: boolean
}) {
  const events = progress?.events || []
  const state = active ? (events.length > 0 ? 'Working' : 'Starting') : 'Complete'
  const latestEvent = events.at(-1)
  const latestLabel = latestEvent?.kind === 'reasoning_delta'
    ? 'Reasoning updated'
    : latestEvent ? toolActivityLabel(latestEvent) : ''

  return (
    <>
      {active && latestLabel && (
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          AI activity updated: {events.length} {events.length === 1 ? 'event' : 'events'}.
          Latest: {latestLabel}. Sequence: {progress?.last_sequence}.
        </p>
      )}
      <details defaultOpen={defaultOpen} className="border-t border-slate-800 bg-slate-950/35 px-3 py-2">
        <summary className="cursor-pointer select-none text-xs font-semibold text-slate-300">
          <span>Thinking &amp; activity</span>
          <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-cyan-300">
            {state}
          </span>
          <span className="ml-2 font-mono text-[10px] text-slate-400">
            {events.length} updates
          </span>
        </summary>
        {events.length === 0 ? (
          <p className="mt-3 text-xs leading-5 text-slate-400">
            {active ? 'Waiting for the first progress update…' : 'No activity details were received.'}
          </p>
        ) : (
          <ol className="ml-1 mt-3 space-y-3 border-l border-slate-700/80 pl-4">
            {progress?.truncated_before_sequence != null && (
              <li className="relative text-[11px] leading-4 text-slate-300">
                <span className="absolute -left-[1.19rem] top-1.5 h-1.5 w-1.5 rounded-full bg-slate-700" />
                Earlier activity was truncated.
              </li>
            )}
            {events.map(event => (
              <li key={event.sequence} className="relative text-[11px] leading-4 text-slate-400">
                <span className={`absolute -left-[1.19rem] top-1.5 h-1.5 w-1.5 rounded-full ${
                  event.kind === 'tool_finished' && event.is_error ? 'bg-red-500' : 'bg-cyan-700'
                }`} />
                {event.kind === 'reasoning_delta' ? (
                  <p className="whitespace-pre-wrap break-words text-slate-400">
                    {event.text?.slice(0, 1000)}
                  </p>
                ) : (
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span className={event.is_error ? 'font-medium text-red-300' : 'font-medium text-slate-300'}>
                      {toolActivityLabel(event)}
                    </span>
                    {event.target && (
                      <code className="min-w-0 break-all font-mono text-[10px] text-slate-300">
                        {event.target}
                      </code>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </details>
    </>
  )
}
```

Set the marker and active state on the optimistic assistant message:

```tsx
const assistantMessage: ChatMessage = {
  id: messageId('assistant'),
  role: 'assistant',
  content: 'Generating design edit...',
  createdAt: Date.now(),
  compileStatus: 'queued',
  progressActive: true,
  progressDisclosure: true,
}
```

Clear `progressActive` in the submit-error updater. Render the disclosure for a
current-session marker, active job, or retained events, with no lifecycle key:

```tsx
const hasActivity = message.role === 'assistant' && (
  Boolean(message.progressDisclosure)
  || Boolean(message.progressActive)
  || Boolean(message.progress?.events.length)
)

{hasActivity && (
  <ProgressActivity
    progress={message.progress}
    active={Boolean(message.progressActive)}
    defaultOpen={Boolean(message.progressDisclosure || message.progressActive)}
  />
)}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
rtk npm test -- src/workflows/generate/GenerateDesignWindow.test.tsx
```

Expected: all Generate Design tests pass.

## Task 2: Keep active work discoverable while the conversation moves

**Files:**

- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`

- [ ] **Step 1: Add failing badge and one-time scroll tests**

Add:

```tsx
it('shows an AI working badge while an active conversation is closed', async () => {
  storage.getLlmFileEditJob.mockResolvedValue({
    job_id: 'llm-job-1',
    status: 'running',
    progress: null,
  })
  render(<GenerateDesignWindow />)
  await screen.findByText('Latest model viewer')
  openGenerateDesignConversation()
  fireEvent.change(
    screen.getByPlaceholderText('Describe the CAD design or modification...'),
    { target: { value: 'inspect the model' } },
  )
  fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))
  fireEvent.click(screen.getByRole('button', { name: 'Close Generate Design conversation' }))
  expect(screen.getByRole('button', {
    name: /Open Generate Design conversation.*AI working/,
  })).toBeInTheDocument()
})

it('scrolls the submitted assistant turn into view once without moving focus', async () => {
  const scrollIntoView = vi.fn()
  const focus = vi.spyOn(HTMLElement.prototype, 'focus')
  const originalScrollIntoView = Element.prototype.scrollIntoView
  Object.defineProperty(Element.prototype, 'scrollIntoView', {
    configurable: true,
    value: scrollIntoView,
  })
  try {
    render(<GenerateDesignWindow />)
    await screen.findByText('Latest model viewer')
    openGenerateDesignConversation()
    const textarea = screen.getByPlaceholderText('Describe the CAD design or modification...')
    fireEvent.change(textarea, { target: { value: 'inspect the model' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate Design' }))

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1))
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    expect(focus).not.toHaveBeenCalled()
  } finally {
    focus.mockRestore()
    if (originalScrollIntoView) {
      Object.defineProperty(Element.prototype, 'scrollIntoView', {
        configurable: true,
        value: originalScrollIntoView,
      })
    } else {
      delete (Element.prototype as { scrollIntoView?: Element['scrollIntoView'] }).scrollIntoView
    }
  }
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
rtk npm test -- src/workflows/generate/GenerateDesignWindow.test.tsx
```

Expected: failures because the active badge and submitted-turn scroll do not
exist.

- [ ] **Step 3: Implement the derived badge and one-time ref callback**

Add:

```tsx
const pendingScrollMessageIdRef = useRef<string | null>(null)
const scrollSubmittedMessageIntoView = useCallback((
  node: HTMLDivElement | null,
  messageIdToScroll: string,
) => {
  if (!node || pendingScrollMessageIdRef.current !== messageIdToScroll) return
  pendingScrollMessageIdRef.current = null
  node.scrollIntoView?.({ block: 'nearest' })
}, [])
```

Set `pendingScrollMessageIdRef.current = assistantMessage.id` immediately before
appending the optimistic messages. Attach the callback only to assistant cards:

```tsx
<div
  ref={message.role === 'assistant'
    ? node => scrollSubmittedMessageIntoView(node, message.id)
    : undefined}
  key={message.id}
>
```

Derive and render the closed-panel badge:

```tsx
const hasActiveProgress = messages.some(message => (
  message.role === 'assistant' && message.progressActive
))

<button type="button" aria-expanded="false" onClick={() => setIsConversationOpen(true)}>
  <span>Open Generate Design conversation</span>
  {hasActiveProgress && (
    <span className="ml-2 inline-flex items-center gap-1 text-cyan-300">
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
      AI working
    </span>
  )}
</button>
```

- [ ] **Step 4: Run focused test, typecheck, lint, and build**

Run:

```bash
rtk npm test -- src/workflows/generate/GenerateDesignWindow.test.tsx
rtk npm run typecheck
rtk npm run lint
rtk npm run build
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the implementation**

```bash
rtk git add ui/src/workflows/generate/GenerateDesignWindow.tsx ui/src/workflows/generate/GenerateDesignWindow.test.tsx
rtk git commit -m "fix: keep Pi progress visible"
```

## Task 3: Verify the full Generate Design behavior and publish

**Files:**

- Modify: `docs/superpowers/plans/2026-07-29-pi-progress-visibility.md`

- [ ] **Step 1: Run the complete frontend and repository safety gates**

Run:

```bash
rtk npm test
rtk npm run typecheck
rtk npm run lint
rtk npm run build
rtk git diff --check origin/master...HEAD
```

Expected: all commands exit 0.

- [ ] **Step 2: Run authenticated k3s AI-edit validation**

Run the isolated local-values release and full AI flow:

```bash
rtk env KUBECONFIG=/home/johnson/.kube/config \
  NAMESPACE=tertius \
  RELEASE_NAME=tertius-live-flow-smoke \
  UI_LOCAL_PORT=18083 \
  API_LOCAL_PORT=18003 \
  METRICS_LOCAL_PORT=8430 \
  TRACES_LOCAL_PORT=10431 \
  scripts/harness-k3s.sh up

rtk env KUBECONFIG=/home/johnson/.kube/config \
  NAMESPACE=tertius \
  RELEASE_NAME=tertius-live-flow-smoke \
  UI_LOCAL_PORT=18083 \
  API_LOCAL_PORT=18003 \
  METRICS_LOCAL_PORT=8430 \
  TRACES_LOCAL_PORT=10431 \
  scripts/harness-k3s.sh live-flow
```

Expected: authenticated seed, pre-edit compile, AI edit, post-edit compile, and
trace checks succeed. Compile-only mode is not acceptable.

- [ ] **Step 3: Inspect the real browser interaction**

Against the smoke release, verify the six runtime assertions from
[`Pi Progress Visibility Correction`](../specs/2026-07-29-pi-progress-visibility-design.md#73-runtime-validation),
plus no unexpected console errors or failed relevant network requests.

- [ ] **Step 4: Request independent code review and resolve findings**

Review the complete range from `origin/master` to `HEAD` against the approved
spec. Fix every Critical or Important issue, rerun affected tests, and record
the result in this plan.

- [ ] **Step 5: Commit the completed plan**

```bash
rtk git add docs/superpowers/plans/2026-07-29-pi-progress-visibility.md
rtk git commit -m "docs: record Pi progress visibility verification"
```

- [ ] **Step 6: Push and open the PR**

```bash
rtk git push -u origin fix/pi-progress-visibility
rtk gh pr create --base master --head fix/pi-progress-visibility
rtk gh pr checks --watch
```

The PR body must summarize the lifecycle fix, list focused/full/frontend/live
validation, note any exact runtime blocker, and avoid prompts, source, progress
text, or raw identifiers.

## Plan Self-Review

- Spec coverage: Tasks 1-3 cover every requirement and exclusion in the
  approved corrective specification.
- Placeholder scan: No unfinished markers, deferred implementation, or vague
  error/test instruction remains.
- Type consistency: `progressDisclosure`, `progressActive`, optional `progress`,
  `defaultOpen`, and the scroll ref names are consistent across tests and
  implementation steps.
