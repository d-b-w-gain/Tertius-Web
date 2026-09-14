# Pi Progress Visibility Correction

**Document type:** Implementation specification

**Status:** Approved

**Date:** 2026-07-29

## 1. Decision Summary

This specification supersedes only the frontend visibility lifecycle in
[Pi Progress Events Design](./2026-07-27-pi-progress-events-design.md#user-experience).
The existing worker, transport, persistence, API, privacy, and polling contracts
remain unchanged.

| Question | Decision |
|---|---|
| Exact problem | Generate Design receives safe Pi progress, but users can miss it because no surface exists before event 1 and the disclosure closes at terminal completion. |
| Success measure | A submitted edit immediately shows a visible progress surface; live updates remain expanded unless the user closes them; the just-completed run preserves that state; active work remains discoverable while the conversation is closed. |
| Structural advantage | The existing bounded progress snapshot and polling loop already provide the required data, so the correction is frontend-only. |
| Architecture decision | Keep the native per-turn `details` disclosure and existing polling reducer; correct its mount and visibility lifecycle instead of adding another status store or transport. |
| Stack rationale | React, TypeScript, Tailwind, and Vitest are the established Generate Design stack and already cover this component. |
| MVP scope | Immediate pending state, persistent current-run disclosure, clear state/count summary, closed-panel working badge, and one-time non-focus-stealing scroll to the submitted turn. |
| Explicit exclusions | No backend/API changes, SSE/WebSockets, raw chain-of-thought, per-event auto-scroll, notification system, or redesign of historical conversation cards. |

## 2. User Experience Contract

### 2.1 Current submitted run

When Generate Design accepts a job, its assistant turn immediately renders a
native disclosure labelled `Thinking & activity`. It is open by default and
shows `Waiting for the first progress update…` until the first safe event
arrives.

The summary always exposes:

- `Thinking & activity`
- state: `Starting`, `Working`, or `Complete`
- the bounded event count as `N updates`

The first progress snapshot replaces the pending copy with the existing
reasoning-summary and safe tool-activity list. Poll updates must not remount the
disclosure.

When the job becomes terminal, the same disclosure instance changes its state
to `Complete` without changing its open state. Therefore:

- an open current run stays open after completion;
- a user-closed current run stays closed after completion;
- terminal completion never overrides the user's disclosure choice.

If a current run reaches terminal state without any progress event, the open
body reads `No activity details were received.` The edit result remains
authoritative.

### 2.2 Historical runs

Conversation history hydrated from the API uses the same disclosure and summary
labels. Terminal historical runs default collapsed, including the just-completed
run after a page reload. Opening or closing one historical disclosure does not
change another.

### 2.3 Conversation visibility

Submitting a job scrolls its new assistant turn into the conversation viewport
once with `block: nearest`. The scroll must not move keyboard focus and must not
repeat for each progress event.

If the conversation is closed while any assistant turn is queued or running,
the existing open-conversation button shows a visible `AI working` badge. The
button's accessible name continues to identify the Generate Design conversation
and includes the working state.

## 3. Component and State Design

`ChatMessage.progressActive` remains the source for queued/running state.
`ChatMessage.progress` remains optional because the API returns `null` before
event 1. A frontend-only `ChatMessage.progressDisclosure` flag marks turns
submitted in the current browser session, remains true after terminal
completion, and is never hydrated from history.

`ProgressActivity` accepts an optional snapshot plus:

- `active`: whether the edit is queued/running;
- `defaultOpen`: true for a current-session run or a hydrated active run;
- no active/terminal value in its React key.

The parent renders `ProgressActivity` when an assistant message is active, has
`progressDisclosure` set, or has at least one progress event. A newly submitted
assistant message sets `progressDisclosure` and `progressActive` immediately.
Hydrated messages set only `progressActive` from their API status, so an active
hydrated run opens by default while terminal history remains collapsed.

The disclosure uses native uncontrolled `details` state. `defaultOpen` applies
only at its first mount, so React updates to progress or terminal status cannot
override a manual toggle.

The one-time scroll targets the newly submitted assistant card through a
message-scoped ref/effect. No effect depends on progress sequence or event
count.

## 4. Accessibility and Privacy

- Retain native `details` and `summary` keyboard and screen-reader behavior.
- Keep the visible pending, working, and complete states in text; color and
  animation are supplementary.
- Keep the existing polite screen-reader announcement bounded to event count,
  generic latest-event label, and sequence.
- Do not announce or add reasoning text, tool targets, prompts, source, raw
  identifiers, or tool arguments/results to accessible status messages.
- Respect manual disclosure toggles and never move focus during automatic
  scrolling.
- Respect `prefers-reduced-motion`; the implementation does not require smooth
  scrolling or mandatory animation.

## 5. Error Handling Matrix

| Condition | Detection | Visible response | Recovery |
|---|---|---|---|
| No event received yet | Active message has no snapshot/events | Open `Starting` disclosure with waiting copy | First valid snapshot replaces the copy |
| Terminal result has no events | Message becomes terminal with no events | `Complete` with `No activity details were received.` | Final edit result remains authoritative |
| Transient status poll failure | Existing polling request rejects | Preserve current disclosure and accumulated events | Existing retry timer continues |
| Stale or duplicate snapshot | Existing merge rejects lower sequence | Keep current event list and state | Later valid snapshot can advance it |
| User closes disclosure | Native `details.open` becomes false | Keep it closed through later progress/terminal renders | User may reopen it |
| Conversation closes during work | Active assistant message exists | Show `AI working` on reopen control | Badge clears after no active turns remain |
| Scroll API unavailable in a unit-test DOM | `scrollIntoView` is absent | No runtime state change or focus movement | Rendering and polling continue |

## 6. Anti-Patterns (Do Not)

| Do not | Do instead | Why |
|---|---|---|
| Hide the progress surface until event 1 | Render an immediate pending state | Worker cold-start time is part of the user wait |
| Remount the disclosure on active/terminal changes | Preserve one uncontrolled `details` instance | Remounting discards user-visible open state |
| Force the disclosure open on every update | Use `defaultOpen` only on first mount | Manual closing must be respected |
| Auto-scroll for every progress event | Scroll once when the assistant turn is submitted | Repeated scrolling fights user review and accessibility |
| Duplicate progress in a global status store | Derive the badge from existing per-turn message state | Avoids divergent job state |
| Add backend fields or another transport | Use existing `progressActive`, snapshots, and polling | The live data path is already healthy |
| Expose raw reasoning or tool payloads | Render only the existing sanitized event contract | Preserves the established privacy boundary |

## 7. Test Case Specifications

### 7.1 Unit and component tests

| ID | Scenario | Expected result |
|---|---|---|
| PV-001 | Newly submitted queued job has `progress: null` | Open `Thinking & activity · Starting · 0 updates` with waiting copy |
| PV-002 | First progress snapshot arrives | Existing disclosure shows `Working`, count, and safe event rows |
| PV-003 | Open current run becomes terminal | Same disclosure remains open and shows `Complete` |
| PV-004 | User closes current disclosure before terminal | It remains closed after later events and completion |
| PV-005 | Terminal history hydrates with progress | Disclosure defaults collapsed with `Complete` and retained count |
| PV-006 | Current run completes without progress | Disclosure remains available with the no-details copy |
| PV-007 | Conversation is closed during an active run | Reopen control visibly exposes `AI working` |
| PV-008 | New assistant turn is submitted | Its card receives one `scrollIntoView({ block: 'nearest' })` call without focus |

### 7.2 Integration tests

| ID | Flow | Verification |
|---|---|---|
| PVI-001 | Submit → delayed first event → multiple events → success | Progress is visible for the entire wait, the same disclosure stays open, and final result handling still runs |
| PVI-002 | Hydrate completed conversation | Historical progress is present but collapsed and can be opened manually |
| PVI-003 | Close conversation during queued/running job → reopen | Active badge is visible while closed and the current progress state is preserved on reopen |

### 7.3 Runtime validation

The full authenticated k3s `live-flow` must run because this changes Generate
Design AI-edit behavior. Browser validation must confirm:

1. the waiting disclosure appears immediately after submission;
2. live safe events replace the waiting copy;
3. completion preserves the disclosure's current open state;
4. closing the conversation during work exposes the badge;
5. final files and compile behavior remain unchanged;
6. console and relevant network requests are clean.

## 8. References

| Topic | Location |
|---|---|
| Original progress architecture and privacy contract | [Pi Progress Events Design](./2026-07-27-pi-progress-events-design.md#architecture) |
| Current Generate Design component | [`GenerateDesignWindow.tsx`](../../../ui/src/workflows/generate/GenerateDesignWindow.tsx) |
| Current Generate Design tests | [`GenerateDesignWindow.test.tsx`](../../../ui/src/workflows/generate/GenerateDesignWindow.test.tsx) |
| Browser validation journey | [Browser Validation](../../harness/browser-validation.md#generate-design-live-ai-edit-and-compile) |
| Local authenticated live flow | [Local Harness](../../harness/local-harness.md) |

## 9. Clarity Gate

| Criterion | Score | Evidence |
|---|---:|---|
| Actionability | 10/10 | Every state has an explicit render and lifecycle rule |
| Specificity | 10/10 | Labels, state transitions, scroll behavior, and fallbacks are exact |
| Consistency | 10/10 | This document is the single source for corrected visibility behavior |
| Structure | 10/10 | Contracts, matrices, tests, and references are separated |
| Disambiguation | 10/10 | Seven anti-patterns and terminal/no-event/manual-close edges are explicit |
| Reference clarity | 10/10 | All dependencies use exact paths and anchors |

**Overall:** 10/10. All 13 foundation and document-architecture checks pass;
the specification is ready for implementation.
