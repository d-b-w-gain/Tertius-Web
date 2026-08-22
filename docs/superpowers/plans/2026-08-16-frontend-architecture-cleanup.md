# Frontend Architecture Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the frontend app shell and the Generate, Artus, and Extus large components as a behavior-preserving four-PR train.

**Architecture:** Application composition moves to `ui/src/app`; workflow decomposition stays feature-local. Pure model/scene helpers and presentational UI receive focused tests while existing public component contracts remain stable.

**Tech Stack:** React 19, TypeScript 6, Vite 8, Vitest, Testing Library, Three.js.

---

### Task 1: App shell PR

**Branch:** `codex/frontend-app-shell` from `master`

**Files:**
- Create: `ui/src/app/AppShell.tsx`
- Create: `ui/src/app/AboutMenu.tsx`
- Create: `ui/src/app/GuestImportBanner.tsx`
- Create: `ui/src/app/WorkbenchNavigation.tsx`
- Create: `ui/src/app/WorkbenchHost.tsx`
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/App.test.tsx`

- [x] Add an App test that opens and closes About through React-visible state; run it and confirm failure because the current menu remains hidden by an imperative class toggle.
- [x] Implement `AboutMenu` with an `isOpen` state and render the existing version, commit, and GitHub link markup unchanged.
- [x] Extract the guest banner and navigation into prop-driven components; preserve labels, access checks, and handlers.
- [x] Extract the sidebar and main layout into `AppShell`, and shared viewport/workbench composition into `WorkbenchHost`.
- [x] Reduce `App.tsx` to auth/application state and composition without `document.getElementById`.
- [x] Run `npm test -- src/App.test.tsx`, lint, typecheck, full tests, and build.
- [ ] Commit, review, push, and open draft PR 1 against `master` (push and PR intentionally deferred for this task).

### Task 2: Generate PR

**Branch:** `codex/frontend-generate-decomposition` from Task 1

**Files:**
- Create: `ui/src/workflows/generate/model/conversation.ts`
- Create: `ui/src/workflows/generate/model/conversation.test.ts`
- Create: `ui/src/workflows/generate/ui/ProgressActivity.tsx`
- Create: `ui/src/workflows/generate/ui/ConversationPanel.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.tsx`
- Modify: `ui/src/workflows/generate/GenerateDesignWindow.test.tsx`

- [ ] Add failing unit tests for ordering editable files, terminal-status classification, compile-repair prompt construction, and progress snapshot merging through exported feature-local helpers.
- [ ] Move those pure helpers and their types to `model/conversation.ts`; preserve exact output strings and ordering.
- [ ] Extract `ProgressActivity` and conversation message/prompt rendering into prop-driven UI components with no API calls.
- [ ] Keep polling timers, request IDs, project synchronization, and submission orchestration in `GenerateDesignWindow`.
- [ ] Run focused Generate tests, lint, typecheck, full tests, and build.
- [ ] Commit, review, push, and open draft PR 2 against PR 1's branch.

### Task 3: Artus PR

**Branch:** `codex/frontend-artus-decomposition` from Task 2

**Files:**
- Create: `ui/src/workflows/artus/model/featureTree.ts`
- Create: `ui/src/workflows/artus/model/featureTree.test.ts`
- Create: `ui/src/workflows/artus/ui/FeatureTree.tsx`
- Create: `ui/src/workflows/artus/ui/FeatureTreeNode.tsx`
- Modify: `ui/src/workflows/artus/ui/FeatureTreeTab.tsx`

- [ ] Add failing tests for assembly-node filtering and tree/BOM naming helpers exported from a feature-local model module.
- [ ] Move pure tree/BOM helper functions and types to `model/featureTree.ts` without changing their return values.
- [ ] Extract recursive node UI to `FeatureTreeNode.tsx` and tree-section composition to `FeatureTree.tsx` using explicit callbacks for visibility, transparency, targeting, and selection.
- [ ] Keep authenticated loading, storage synchronization, AI edit, compile queueing, and transaction-like UI sequencing in `FeatureTreeTab`.
- [ ] Run focused Artus tests, lint, typecheck, full tests, and build.
- [ ] Commit, review, push, and open draft PR 3 against PR 2's branch.

### Task 4: Extus PR

**Branch:** `codex/frontend-extus-decomposition` from Task 3

**Files:**
- Create: `ui/src/workflows/extus/scene/materials.ts`
- Create: `ui/src/workflows/extus/scene/batching.ts`
- Create: `ui/src/workflows/extus/ui/ViewerControls.tsx`
- Modify: `ui/src/workflows/extus/ui/ViewerTab.tsx`
- Modify: `ui/src/workflows/extus/ui/ViewerTab.materials.test.ts`
- Modify: `ui/src/workflows/extus/ui/ViewerTab.active.test.tsx`

- [ ] Change material tests to import a not-yet-created `scene/materials` module and confirm the focused test fails.
- [ ] Move material inspection, cloning, variants, and disposal helpers to `scene/materials.ts` while keeping existing exports re-exported from `ViewerTab` for compatibility.
- [ ] Move batch construction and hidden-object selection helpers to `scene/batching.ts` with existing behavior intact.
- [ ] Extract the toolbar/quality/grid/rotation controls into `ViewerControls.tsx`; keep Three.js lifecycle, refs, observers, and animation ownership in `ModelViewerCanvas`.
- [ ] Run focused Extus tests, lint, typecheck, full tests, build, and the compile-only live-flow if the local harness is available.
- [ ] Commit, review, push, and open draft PR 4 against PR 3's branch.
