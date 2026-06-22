# Generate Design Floating Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Generate Design open with a collapsed control sidebar, a floating conversation panel, and a full-window model viewer matching the Extus viewport emphasis.

**Architecture:** Keep the existing Generate Design data flow, AI edit polling, historical model selection, and Extus viewer components unchanged. Rework only the rendered shell so the viewer is the full-page base layer and the Generate Design conversation/settings UI is an overlay that is collapsed by default and can be expanded on demand.

**Tech Stack:** React 19, TypeScript, Tailwind utility classes, Vitest/Testing Library, existing Extus `LatestModelViewer` and `ModelViewerCanvas`.

---

## Requirements

- The model viewer must fill the Generate Design window by default.
- The Generate Design conversation/settings sidebar must be collapsed by default.
- A visible floating control must let the user open the conversation/sidebar.
- The expanded conversation must float over the viewer, not reserve half the viewport.
- The existing prompt submission, model selection, refresh, project selector, history selection, and historical model viewer behavior must keep working.
- Because this touches Generate Design and AI edit-linked model viewing, final validation must run full `scripts/harness-k3s.sh live-flow` or `scripts/harness-compose.sh live-flow`; compile-only mode is not sufficient.

## Anti-Patterns

| Do not | Do instead | Why |
|---|---|---|
| Replace the Extus viewer | Reuse `LatestModelViewer` and `ModelViewerCanvas` | Keeps existing renderer lifecycle and tests intact |
| Keep a desktop 50/50 split | Use a full-window viewer base layer | The requested UX is viewer-first |
| Hide the conversation with no affordance | Add an always-visible floating open button | Users must be able to submit prompts |
| Add new routing or storage state | Use local component state for panel open/closed | The change is visual, not workflow persistence |
| Use compile-only live-flow | Run full AI edit live-flow | Repo rules classify this as AI edit behavior |

## Task 1: Add Layout Regression Coverage

**Files:**
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`

- [ ] Add a test that renders `GenerateDesignWindow`, waits for the viewer, verifies the conversation panel is collapsed by default, opens it with the floating button, and verifies prompt controls are available.

Expected test shape:

```tsx
it('starts viewer-first with the Generate Design conversation collapsed into a floating panel', async () => {
  render(<GenerateDesignWindow />)

  await screen.findByText('Latest model viewer')

  expect(screen.getByRole('button', { name: 'Open Generate Design conversation' })).toBeInTheDocument()
  expect(screen.queryByPlaceholderText('Describe the CAD design or modification...')).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Open Generate Design conversation' }))

  expect(screen.getByRole('complementary', { name: 'Generate Design conversation' })).toBeInTheDocument()
  expect(screen.getByPlaceholderText('Describe the CAD design or modification...')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Close Generate Design conversation' })).toBeInTheDocument()
})
```

- [ ] Run the focused test and confirm it fails before implementation:

```bash
rtk npm --prefix ui test -- GenerateDesignWindow.test.tsx
```

Expected before implementation: failure because the open button, complementary region, and default-collapsed behavior do not exist.

## Task 2: Implement Floating Viewer Layout

**Files:**
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`

- [ ] Add component state:

```tsx
const [isConversationOpen, setIsConversationOpen] = useState(false)
```

- [ ] Change the root shell from the current `md:flex-row` split to a relative full-window container.
- [ ] Render the model header and viewer as the full-size base layer.
- [ ] Render a floating "Open Generate Design conversation" button when the panel is closed.
- [ ] Render the existing Generate Design controls/messages/form inside an `aside` with `role="complementary"` and `aria-label="Generate Design conversation"` when open.
- [ ] Add a "Close Generate Design conversation" button in the floating panel header.
- [ ] Preserve existing button names used by existing tests where possible, especially the submit button text `Generate Design`.

## Task 3: Verify Local UI Behavior

- [ ] Run the focused Generate Design tests:

```bash
rtk npm --prefix ui test -- GenerateDesignWindow.test.tsx
```

- [ ] Run the UI build or typecheck:

```bash
rtk npm --prefix ui run build
```

## Task 4: Run AI Smoke Flow

- [ ] Prefer the repo's k3s live-flow wrapper if local k3s validation is available:

```bash
rtk scripts/harness-k3s.sh live-flow
```

- [ ] If k3s is unavailable but Compose parity is available, run:

```bash
rtk scripts/harness-compose.sh live-flow
```

- [ ] Do not set `LIVE_FLOW_COMPILE_ONLY=true`; this change affects Generate Design and AI edit-linked model viewing.
- [ ] If the live-flow is blocked by missing runtime, auth, provider credentials, or port-forwarding, record the exact blocker and the focused validation commands that did run.
