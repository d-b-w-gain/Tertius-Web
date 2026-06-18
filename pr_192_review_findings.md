# PR #192 Review: `[codex] wire Artus AI edit to Intus files`

Repository: `d-b-w-gain/Tertius-Web`  
PR: https://github.com/d-b-w-gain/Tertius-Web/pull/192  
Head: `codex/artus-intus-ai-edit-integration`  
Status at review: Draft PR, CI green

## Summary

The PR is a focused change that routes Artus AI Design Modification through the authenticated Intus project storage LLM file edit helper. It adds frontend tests for endpoint routing, request shape, file cap behavior, success refresh, and conflict refresh.

I found two issues that should be fixed or explicitly accepted before merge.

---

## Finding 1: Artus AI edit will not auto-compile unless the Intus tab is active

### Problem

The PR changes Artus AI edit to call the Intus `/files/llm-edit` endpoint, then refreshes Artus features after success. It does not queue an Intus compile directly.

The backend `llm-edit` route stages file updates and returns changed files, but it does not publish a compile command.

The PR changes Intus `autoCompile` to default to `true`, but the compiler external-change polling exits early when the Intus compiler tab is not active.

In the app, the default active tab is `extus`, and Intus receives `isActive={activeTab === 'intus'}`. The Artus sidebar is permanent, so users can run AI edits while viewing Extus.

### Impact

A user can use Artus AI edit from the permanent sidebar while viewing Extus. The source files can update successfully, but the viewport/model may remain stale until the user switches to Intus and polling catches up.

This is likely a regression from the old Artus path if users expect AI design modifications to refresh the visible model.

### Suggested fix

After a successful Artus AI edit, either:

1. Queue an Intus compile directly from Artus after the file edit succeeds, or
2. Dispatch a shared `project file changed` / `compile requested` event that Intus or central compile orchestration listens to even when the Intus tab is inactive.

### Suggested test

Add a test that simulates:

1. Active UI tab is Extus.
2. User applies Artus AI edit.
3. The changed design is compiled or the model refresh path is triggered without requiring the user to activate Intus.

---

## Finding 2: AI edit can target a stale project immediately after changing the project selector

### Problem

`ProjectSelector` activates a project and dispatches `tertius:active-project-changed`.

However, `FeatureTreeTab` updates its local `activeProject` only from the `/features` polling/refresh path. `handleAiModify` then uses that local `activeProject` to list Intus file metadata and call the LLM edit endpoint.

This creates a race:

1. User changes project via the selector.
2. Selector displays or activates the new project.
3. Before Artus refreshes `/features`, the user clicks `Apply AI`.
4. `handleAiModify` can still use the previous `activeProject`.

The old `/api/artus/ai_modify` flow used the backend active project at request time, so this client-side project-name dependency introduces a regression risk.

### Impact

An AI edit may be sent to the wrong project if the user quickly applies an edit after changing the project selector.

### Suggested fix

Have `FeatureTreeTab` listen for `ACTIVE_PROJECT_CHANGED_EVENT` and immediately either:

1. Call `loadFeatures()` and disable AI edit until it completes, or
2. Set `activeProject` from the event and mark features/file metadata stale until refreshed.

The safest behavior is to disable `Apply AI` while the selected project and Artus feature snapshot are not known to be in sync.

### Suggested test

Add a test that simulates:

1. Initial project is `project_a`.
2. User changes selector to `project_b`.
3. User immediately clicks `Apply AI`.
4. Request goes to `/api/intus/projects/project_b/files/llm-edit`, not `project_a`, or the button is disabled until refresh completes.

---

## Non-blocking notes

- PR changes 3 files:
  - `ui/src/workflows/artus/ui/FeatureTreeTab.authenticated.test.tsx`
  - `ui/src/workflows/artus/ui/FeatureTreeTab.tsx`
  - `ui/src/workflows/intus/ui/CompilerTab.tsx`
- The new Artus authenticated tests cover useful request-shape and conflict-refresh behavior.
- CI for the reviewed head commit showed both `Integration Tests` and `Tests` passing.
- I would not merge until the compile/refresh path is fixed or documented as a known limitation.

## Recommended Codex task

Please update PR #192 to address the two review findings:

1. Ensure an Artus AI edit triggers compile/model refresh even when the Intus compiler tab is inactive.
2. Prevent stale-project AI edits immediately after a project selector change.

Add regression tests for both behaviors.
