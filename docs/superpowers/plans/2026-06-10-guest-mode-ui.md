# Guest Mode UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let visitors use the UI without logging in, keep guest work only in browser `localStorage`, show a login state widget with display name plus login/logout controls, and migrate guest work into the user's account after signup/login.

**Architecture:** Keep authenticated persistence on the existing tenant-scoped Intus APIs. Add a frontend guest project store that mirrors the minimum Intus project/file model in `localStorage`. UI components branch on auth mode: guest mode reads/writes local Intus drafts, while server-backed Artus, Extus, and Timus workflows render guest-safe disabled/empty states instead of polling authenticated APIs. After login, a migration service imports local guest projects into the authenticated account through the same Intus API base resolver used by the app, then clears or archives the local draft after confirmed upload.

**Tech Stack:** Existing React/Vite/TypeScript/Tailwind UI, `oidc-client-ts`, browser `localStorage`, FastAPI/SQLAlchemy backend only if endpoint gaps are found during implementation.

---

## Stream Coding Clarity Gate

1. **Problem:** Anonymous users are forced to login before trying Tertius, and any pre-account work needs a clear path into account-backed persistence.
2. **Success:** Anonymous users reach the app without redirect, can create/edit at least one local project, login/signup from the app header, and see their local draft imported into their authenticated account.
3. **Win condition:** No guest data is sent to the backend until the user authenticates.
4. **Core decision:** Frontend-local guest store first; server remains authenticated and tenant-scoped.
5. **Stack rationale:** Reuse current React UI and auth provider instead of introducing SvelteKit during a focused feature.
6. **MVP:** Guest shell, login widget, local project/file save, login-time import, focused tests.
7. **Out of scope:** Shared anonymous server sessions, cloud sync before login, guest artifact retention, billing, collaboration, and full offline support.

## Decisions From Plan Review

These decisions resolve review findings before implementation starts.

### Finding 1: Guest Mode Still Mounts Authenticated Workflows

Options considered:

| Option | Trade-off | Decision |
| --- | --- | --- |
| Adapt every workflow API call to a guest backend/store | Most complete, but turns the MVP into a broad offline-mode implementation | Reject for MVP |
| Render only Intus in guest mode and hide Artus, Extus, Timus | Simple, but changes the app shape and navigation too much | Reject for MVP |
| Keep the full shell and tabs, but gate server-backed panes with guest-safe states | Preserves navigation while preventing token errors and backend calls | **Use this** |

Implementation requirement: in guest mode, Artus, Extus, and Timus must not call `apiFetch`, `getAccessToken`, or any authenticated polling path. They should show compact disabled states such as `Log in to inspect features`, `Log in to view compiled models`, and `Log in to generate drawings`.

### Finding 2: Import Path Must Not Hardcode `/proxy`

Options considered:

| Option | Trade-off | Decision |
| --- | --- | --- |
| Keep `/proxy/api/intus` in guest import | Works only in Vite dev proxy and fails production routing | Reject |
| Pass `serverUrl` from `CompilerTab` into import | Works locally, but couples migration to one mounted component | Reject |
| Reuse the shared workflow API resolver for `intus` | Matches existing app routing in dev and production | **Use this** |

Implementation requirement: `guestImport.ts` must resolve the Intus API base through `resolveWorkflowServerUrl('intus')`, then call `${intusBase}/projects/...`. Do not use literal `/proxy`.

### Finding 3: UI Tests Need Tooling

Options considered:

| Option | Trade-off | Decision |
| --- | --- | --- |
| Skip UI tests and rely on build/lint | Fast, but weak for localStorage/import/auth transitions | Reject |
| Use Jest | Common, but more setup friction in a Vite/React app | Reject |
| Add Vitest with jsdom and React Testing Library | Fits Vite, supports component and unit tests | **Use this** |

Implementation requirement: add `vitest`, `jsdom`, `@testing-library/react`, and `@testing-library/jest-dom`; add `npm run test` and use it in verification.

### Finding 4: Guest `activeFile` After Import

Options considered:

| Option | Trade-off | Decision |
| --- | --- | --- |
| Add a backend active-file endpoint | Precise, but backend change is not necessary for MVP | Defer |
| Ignore `activeFile` after import | Simple, but contradicts storing it in the guest contract | Reject |
| Preserve active file in frontend state after successful import | Keeps MVP frontend-only and avoids backend changes | **Use this** |

Implementation requirement: after import, activate the imported project on the backend and update the frontend editor/storage adapter selection to the guest `activeFile` if that file exists in the imported project. A page refresh may still return `design.py` until a backend active-file endpoint is added; document this as an accepted MVP limitation.

### Finding 5: Compile Gating Includes Auto-Compile

Options considered:

| Option | Trade-off | Decision |
| --- | --- | --- |
| Only relabel the manual compile button | Leaves auto-compile and status polling paths active | Reject |
| Disable the whole Compiler tab in guest mode | Prevents local draft editing, which is the MVP value | Reject |
| Disable authenticated compile/status polling while keeping local editing | Matches privacy and no-backend-call goals | **Use this** |

Implementation requirement: in guest mode, the manual compile button, auto-compile toggle, external status polling, and any compile-triggering effect must be disabled or skipped. The editor and local file/project operations remain usable.

## Open Questions

No blocking questions. The plan assumes the MVP should keep guest work entirely frontend-local, keep the existing React/Vite stack for this feature, and avoid backend changes unless the current authenticated Intus endpoints cannot import project files without data loss.

## Data Contracts

### Guest LocalStorage

Use a versioned key so migration and future cleanup are explicit:

```ts
type GuestWorkspace = {
  version: 1
  activeProject: string
  projects: Record<string, {
    files: Record<string, string>
    activeFile: string
    updatedAt: string
  }>
}
```

Recommended key: `tertius_guest_workspace_v1`.

### Authenticated Import

For each guest project, resolve the Intus API base through the existing workflow API resolver and then:

1. `GET {intusBase}/projects` to detect name collisions.
2. `POST {intusBase}/projects/{safeName}/new`.
3. `POST {intusBase}/projects/{safeName}/save` for each `.py` file, including `design.py`. This intentionally overwrites the backend default template that `new` creates.
4. `POST {intusBase}/projects/{safeName}/activate` for the guest active project.
5. Update the frontend editor selection to the guest `activeFile` if it exists in the imported project.

If the project already exists, import under a suffix such as `{name}-guest-{YYYYMMDD-HHmm}` rather than overwriting account data.

---

## Anti-Patterns

| Don't | Do Instead | Why |
| --- | --- | --- |
| Auto-redirect anonymous users to Keycloak on app load | Render the app in guest mode and let the widget start login | Guest mode must be reachable |
| Send guest drafts to backend before login | Keep guest drafts in `localStorage` only | Matches the privacy and persistence requirement |
| Treat logout as deleting guest drafts | Preserve guest drafts unless the user explicitly clears them | Logout should not lose local work |
| Overwrite existing authenticated projects during import | Use collision-safe renamed imports | Avoid account data loss |
| Thread nullable auth checks through every workflow ad hoc | Add small shared guest/auth persistence adapters | Keeps behavior consistent and testable |
| Compile guest drafts through authenticated endpoints silently | Disable or gate compile until login, unless a deliberate guest compile endpoint is added later | Current compile persists artifacts and requires auth |
| Leave hidden tabs polling authenticated APIs in guest mode | Gate Artus, Extus, Timus, status polling, and auto-compile effects before they call `apiFetch` | Hidden React trees still run effects |
| Hardcode `/proxy` in application code | Use the shared workflow API resolver | `/proxy` is a dev-server detail |

## Task 1: Add UI Test Tooling

**Files:**
- Modify: `ui/package.json`
- Modify: `ui/package-lock.json`
- Modify: `ui/vite.config.ts`
- Create: `ui/src/test/setup.ts`

- [ ] Add Vitest, jsdom, React Testing Library, and jest-dom dev dependencies.
- [ ] Add scripts:
  - `test`: `vitest run --passWithNoTests`
  - `test:watch`: `vitest`
- [ ] Add `ui/src/test/setup.ts` with `import '@testing-library/jest-dom/vitest'`.
- [ ] Add `test: { environment: 'jsdom', setupFiles: './src/test/setup.ts' }` to `defineConfig` in `ui/vite.config.ts`.
- [ ] Run `cd ui && npm run test`.
- [ ] Expected result before feature tests are added: PASS because `--passWithNoTests` allows the empty suite.

## Task 2: Make Auth State Optional

**Files:**
- Modify: `ui/src/auth/AuthProvider.tsx`
- Modify: `ui/src/api/client.ts`
- Modify: `ui/src/App.tsx`
- Test: `ui/src/auth/AuthProvider.test.tsx`

- [ ] Remove the top-level anonymous auto-login redirect in `App`.
- [ ] Add `authMode: "guest" | "authenticated"` to `AuthState`.
- [ ] Keep `getAccessToken()` strict: it should still throw if an authenticated API call is attempted without a valid token.
- [ ] Add a focused test proving anonymous load renders the app shell instead of "Redirecting to login...".
- [ ] Add a focused test proving `getAccessToken()` rejects when no valid user exists.

## Task 3: Add Login State Widget

**Files:**
- Create: `ui/src/auth/LoginStateWidget.tsx`
- Modify: `ui/src/App.tsx`

- [ ] Build a compact header widget that displays `Guest` when anonymous.
- [ ] When authenticated, display the user's best available name: `profile.name`, `profile.preferred_username`, `profile.email`, then fallback to `Account`.
- [ ] Show `Log in` for guests and `Log out` for authenticated users.
- [ ] Keep the About menu behavior separate from auth state.
- [ ] Make the widget fit the existing dense, work-focused header style.

## Task 4: Add Guest Workspace Store

**Files:**
- Create: `ui/src/workflows/shared/guestWorkspace.ts`
- Test: `ui/src/workflows/shared/guestWorkspace.test.ts`

- [ ] Implement load/save helpers for `tertius_guest_workspace_v1`.
- [ ] Seed a default `default_purlin` project with `design.py` from a frontend-safe fallback template.
- [ ] Validate project names with the backend-compatible pattern: `^[A-Za-z0-9_.-]{1,80}$`.
- [ ] Validate filenames with the backend-compatible pattern: `^[A-Za-z0-9_.-]+\.py$`.
- [ ] Return normalized data if localStorage is missing, corrupt, or from an unsupported version.
- [ ] Add unit tests for corrupt JSON, name validation, file CRUD, active project selection, and persistence.

## Task 5: Route Intus Project UI Through Guest/Auth Adapter

**Files:**
- Modify: `ui/src/workflows/shared/ui/ProjectSelector.tsx`
- Modify: `ui/src/workflows/intus/ui/CompilerTab.tsx`
- Create: `ui/src/workflows/shared/projectStorage.ts`
- Test: `ui/src/workflows/shared/projectStorage.test.ts`
- Test: `ui/src/workflows/intus/ui/CompilerTab.guest.test.tsx`

- [ ] Add a small adapter with the operations the UI already uses: list projects, create project, activate project, list files, load code, save code, delete file, get status, and get history.
- [ ] In authenticated mode, delegate to the existing Intus API calls.
- [ ] In guest mode, delegate to `guestWorkspace`.
- [ ] For guest mode, mark Git history as unavailable and show a neutral "Local draft" status instead of "No Git".
- [ ] Persist editor changes locally on file switch, new file, delete file, and a 500 ms debounced editor change.
- [ ] Disable the compile/export button for guests and label it `Log in to compile`.
- [ ] In guest mode, skip Intus status polling and auto-compile effects before they call `apiFetch`.
- [ ] In guest mode, disable the auto-compile toggle and force `autoCompile` false.
- [ ] Add tests proving guest editing persists to `localStorage` without calling `apiFetch`.
- [ ] Add tests proving guest compile/status paths do not call `getAccessToken()`.

## Task 6: Gate Server-Backed Workflows In Guest Mode

**Files:**
- Modify: `ui/src/workflows/artus/ui/FeatureTreeTab.tsx`
- Modify: `ui/src/workflows/extus/ui/ViewerTab.tsx`
- Modify: `ui/src/workflows/timus/ui/DraftingTab.tsx`
- Create: `ui/src/workflows/shared/ui/GuestWorkflowNotice.tsx`
- Test: `ui/src/workflows/artus/ui/FeatureTreeTab.guest.test.tsx`
- Test: `ui/src/workflows/extus/ui/ViewerTab.guest.test.tsx`
- Test: `ui/src/workflows/timus/ui/DraftingTab.guest.test.tsx`

- [ ] Add a small shared guest notice component with props for title, message, and login action.
- [ ] In Artus guest mode, do not fetch `/features`, `/update_features`, `/ai_modify`, or Extus model/status data. Render `Log in to inspect and modify features`.
- [ ] In Extus guest mode, do not fetch `/status` or `/model`. Render `Log in to view compiled models`.
- [ ] In Timus guest mode, do not fetch project name, settings, drafting status, drafting builds, or PDFs. Render `Log in to generate drawings`.
- [ ] Keep tab navigation visible so users can understand what is available after login.
- [ ] Add tests that mount each guest workflow and assert no authenticated API/token calls occur.

## Task 7: Migrate Guest Work After Login

**Files:**
- Create: `ui/src/workflows/shared/guestImport.ts`
- Modify: `ui/src/App.tsx`
- Test: `ui/src/workflows/shared/guestImport.test.ts`

- [ ] Detect first transition from guest to authenticated during the current browser session.
- [ ] If guest workspace has unsynced projects, show a dismissible import banner in `App`.
- [ ] Import projects through existing authenticated Intus endpoints using `resolveWorkflowServerUrl('intus')` from `ui/src/workflows/shared/apiConfig.ts`. Do not use literal `/proxy`.
- [ ] Resolve name collisions by first calling `GET {intusBase}/projects`, then suffixing imported project names.
- [ ] After `POST /new`, save every guest `.py` file, including `design.py`, so the backend default template is replaced by guest content.
- [ ] After all uploads succeed, activate the imported equivalent of the guest active project.
- [ ] Restore the frontend editor selection to the guest `activeFile` if that file exists. Accept that a full page refresh may return `design.py` until backend active-file persistence exists.
- [ ] Clear the guest workspace only after successful import; on partial failure, preserve local data and show retry.
- [ ] Add tests for collision suffixing, no `/proxy` URLs, `design.py` overwrite, active-file restoration metadata, and partial-failure preservation.

## Task 8: Backend Gap Check

**Files:**
- Prefer no backend changes.
- Possible modify: `server/workflows/intus/intus_server.py`
- Possible test: `server/tests/test_intus_endpoints.py`

- [ ] Confirm existing `new`, `save`, `activate`, `files`, and `code` endpoints can import all guest data.
- [ ] If collision detection needs a cleaner contract, add a narrow import endpoint only after proving the current endpoint sequence is insufficient.
- [ ] Do not add a backend active-file endpoint for the MVP unless frontend-only active-file restoration proves unusable.
- [ ] Keep all backend guest-import routes authenticated with `Depends(get_auth_context)`.

## Task 9: Verification

**Files:**
- No source edits.

- [ ] Run `cd ui && npm run lint`.
- [ ] Run `cd ui && npx tsc --noEmit`.
- [ ] Run `cd ui && npm run test`.
- [ ] Run `cd ui && npm run build`.
- [ ] If backend changed, run `UV_CACHE_DIR=.uv-cache uv run pytest server/tests/test_intus_endpoints.py server/tests/test_auth.py -q`.

## Manual Smoke Test

- [ ] Open the UI in a fresh browser profile with no login session.
- [ ] Confirm the app loads as guest and the widget shows `Guest` plus `Log in`.
- [ ] Create a project, edit `design.py`, refresh, and confirm the draft remains.
- [ ] Visit Artus, Extus, and Timus while still guest and confirm they show login-required states without triggering login redirects or token errors.
- [ ] Confirm the Intus compile button and auto-compile toggle are disabled or relabeled for guest mode.
- [ ] Click `Log in`, authenticate or create account, and import the local draft.
- [ ] Confirm the imported project appears in the account-backed selector and survives refresh.
- [ ] Confirm the imported project content matches the guest `design.py` content, not the backend default template.
- [ ] Log out and confirm the widget returns to guest mode without deleting local guest drafts.
