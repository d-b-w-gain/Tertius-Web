# Codex Handoff: Keep 3MF Lean, Extract Generic Compile Input Assets (Implementation)

**Execution plan:** [`docs/superpowers/plans/2026-08-28-compile-input-assets-refactor.md`](superpowers/plans/2026-08-28-compile-input-assets-refactor.md)
# Codex Handoff: Keep 3MF Lean, Extract Generic Compile Input Assets

## Repository

- Repo: `https://github.com/d-b-w-gain/Tertius-Web`
- Relevant stacked PRs:
  - `#356` — `feat: load 3MF geometry in compile sandbox`
  - `#357` — `feat: add lean 3MF project import API`
  - `#358` — `feat: add lean 3MF import UI`
- Stack order:
  - `#356` depends on the existing sidecar branch
  - `#357` depends on `#356`
  - `#358` depends on `#357`

## Goal

Keep the current 3MF feature architecture lean.

Do **not** perform a general refactor of the AI / Pi-agent / LLM generation pipeline as part of this work.

Instead, make one focused architectural improvement in the `#357` layer:

> Extract the new compile-time binary asset preparation/snapshot logic into a format-neutral compile input abstraction so that 3MF does not become hard-coded throughout the compile lifecycle.

The finished architecture should continue to treat imported 3MF projects as normal Python projects plus binary compile inputs.

## Architectural Decision

The desired conceptual flow is:

```text
3MF import
   |
   +--> durable project asset: source_3mf
   |
   +--> generated design.py
           |
           v
     normal project editing / AI editing
           |
           v
        compile job
           |
           +--> project files snapshot
           |
           +--> binary input asset snapshot
                    |
                    v
             CompileBinaryAsset
                    |
                    v
              compile sandbox
                    |
                    v
      tertius_imports.load_3mf_model()
```

The AI layer should remain unaware of the raw 3MF unless a future feature explicitly introduces bounded asset metadata as AI context.

## Do Not Refactor

Do not broaden this task into any of the following:

- `pi_agent_rpc.py`
- Pi-agent provider abstractions
- LLM provider handling
- AI edit request/response models
- Artus/Intus AI edit routing
- general workflow server restructuring
- a new import worker
- asynchronous 3MF conversion
- BREP persistence
- manifests
- generic plugin systems
- a repository-wide DDD rewrite
- moving unrelated routes out of `intus_server.py`
- changing the `#356` sandbox loader design
- changing the `#358` UI flow beyond what is required to keep the stack green

If a broader cleanup is desirable, note it separately rather than implementing it in this branch.

---

# Current Problem to Improve

`#357` introduces binary compile input handling in two compile paths:

1. normal compile submission in `server/workflows/intus/intus_server.py`
2. stale queued job republish in `server/workflows/intus/compile_result_consumer.py`

The current implementation is 3MF-specific.

Normal compile roughly does:

```python
source_artifact = compile_repo.project_source_artifact(project_id)
if source_artifact is not None:
    source_snapshot = compile_repo.record_artifact(
        project_id,
        job.id,
        "source_3mf",
        source_artifact.content,
        content_type=source_artifact.content_type,
    )
    assets.append(
        CompileBinaryAsset(
            logical_filename="source.3mf",
            object_ref=await store_compile_sidecar(source_snapshot.content),
        )
    )
```

Republish separately does roughly:

```python
source_artifact = CompileRepository(...).source_artifact_for_job(job.id)
if source_artifact is not None:
    assets.append(
        CompileBinaryAsset(
            logical_filename="source.3mf",
            object_ref=await put_compile_sidecar(source_artifact.content, settings),
        )
    )
```

Repository methods are also named around one source format:

```python
project_source_artifact(...)
source_artifact_for_job(...)
delete_job_source_artifacts(...)
```

This is acceptable for one feature but is the point most likely to accumulate format-specific branches when STEP, OBJ, STL, reference files, or other binary project inputs are added.

---

# Target Design

Introduce a small compile-input abstraction.

Do not create a large framework.

A suitable design would have two distinct responsibilities:

1. **Snapshot durable project compile inputs onto a compile job**
2. **Materialize the job-pinned inputs into `CompileBinaryAsset` values**

Suggested vocabulary:

```python
project_input_artifacts(...)
job_input_artifacts(...)
snapshot_project_input_artifacts(...)
delete_job_input_artifacts(...)
materialize_job_binary_assets(...)
```

Names may differ if the repository already has a better convention.

The abstraction must be format-neutral.

## Input Kind Mapping

Use one explicit mapping for supported binary compile inputs.

For example:

```python
COMPILE_INPUT_KINDS = {
    "source_3mf": "source.3mf",
}
```

Or a small frozen model/dataclass if metadata is useful:

```python
@dataclass(frozen=True)
class CompileInputKind:
    artifact_kind: str
    logical_filename: str

COMPILE_INPUT_KINDS = (
    CompileInputKind("source_3mf", "source.3mf"),
)
```

Do not introduce speculative entries for formats not yet supported.

---

# Required Behaviour

## Normal compile

The normal compile flow should become approximately:

```text
create job
snapshot source files
snapshot project binary inputs
materialize job binary inputs
publish CompileCommand
```

The command must continue to contain:

- normal source files
- `CompileBinaryAsset(logical_filename="source.3mf", ...)` for imported 3MF projects
- no binary assets for ordinary Python-only projects

## Stale queued job republish

Republish must use only the **job-pinned snapshot**, never the current durable project asset.

This is critical.

If the durable project-level 3MF has changed after the compile job was created, republishing the stale job must still use the original bytes pinned to that job.

## Job lifecycle cleanup

Terminal jobs must remove only ephemeral job-level input snapshots.

They must not remove durable project-level source assets.

Existing cleanup semantics should remain:

- succeeded → remove job input snapshots
- failed → remove job input snapshots
- stale/reconciled terminal state → remove job input snapshots
- durable project asset remains

## Failure semantics

Preserve existing user-visible behaviour.

Examples:

- missing durable content during initial compile should fail deterministically
- missing pinned job input during stale republish should mark the job failed rather than silently using a newer project asset
- object-store failure behaviour should remain consistent with the current compile publication path

Do not hide errors by falling back from job-pinned input to project-level input.

---

# Suggested File Changes

Likely files:

## `server/core/repositories.py`

Refactor the 3MF-specific job/project source artifact methods.

Possible direction:

```python
def project_input_artifacts(self, project_id: UUID) -> list[Artifact]:
    ...

def job_input_artifacts(self, job_id: UUID) -> list[Artifact]:
    ...

def delete_job_input_artifacts(self, job_id: UUID) -> None:
    ...
```

If generic list-returning methods are unnecessary, a mapping-aware helper is also acceptable.

Avoid exposing `"source_3mf"` repeatedly outside the compile-input module.

## New small module, if useful

A small module such as:

```text
server/core/compile_inputs.py
```

is preferred if it keeps orchestration out of `intus_server.py` and `compile_result_consumer.py`.

Potential responsibilities:

```python
snapshot_project_compile_inputs(...)
materialize_job_compile_inputs(...)
```

This module may depend on:

- `CompileRepository`
- `CompileBinaryAsset`
- object-store sidecar persistence
- the compile input kind mapping

Keep it narrow.

## `server/workflows/intus/intus_server.py`

Replace inline 3MF-specific snapshot/materialization logic with the new abstraction.

Do not otherwise restructure the file.

## `server/workflows/intus/compile_result_consumer.py`

Replace the separate 3MF republish asset construction with the same materialization path used by normal compile.

Republish must operate on job snapshots only.

## Tests

Update or add focused tests in the existing suites.

Likely files:

- `server/tests/test_lean_3mf_import_api.py`
- `server/tests/test_compile_result_consumer.py`
- `server/tests/test_repositories.py`

Add a dedicated `test_compile_inputs.py` only if the new module warrants isolated unit tests.

---

# Tests That Must Remain or Be Added

## 1. Python-only compile remains unchanged

Create/compile a project with no binary source asset.

Assert:

```python
command.assets == []
```

No regression to normal projects.

## 2. Imported 3MF compile creates a job-pinned input

Given a durable project-level `source_3mf`:

- compile project
- assert a job-level `source_3mf` snapshot exists
- assert the published command has one binary asset
- assert its logical filename is `source.3mf`

## 3. Snapshot is immutable with respect to later project changes

Create:

```text
durable project 3MF = A
create compile job → snapshot A
replace/mutate durable project 3MF = B
republish stale queued job
```

Assert the bytes uploaded for republish are **A**, not B.

This is one of the most important tests.

## 4. Terminal cleanup removes only job inputs

After success/failure:

- job-level input snapshot is gone
- durable project-level `source_3mf` still exists

## 5. Missing job snapshot cannot fall back

For an imported project's stale queued job:

- remove/corrupt the job input snapshot
- leave durable project source present
- run stale republish

Assert:

- job becomes failed with the existing missing-snapshot semantics
- current project asset is **not** silently substituted

## 6. Multiple compile-input entries are structurally supported

Do not invent new product formats, but structure tests/helpers so the abstraction naturally handles an iterable of input definitions rather than assuming exactly one item.

A small unit test may temporarily use test-only dummy mappings if useful.

## 7. Tenant isolation remains intact

A repository query for project/job input artifacts must not return another tenant's artifacts.

---

# 3MF-Specific Behaviour That Must Not Change

## `#356`

Do not change the architectural intent of the sandbox helper.

`tertius_imports.py` remains injected into the compile sandbox.

`load_3mf_model("source")` remains the generated project's API.

The loader continues to:

- validate the bounded ZIP envelope
- reject unsupported build graphs
- use Build123D `Mesher.read()`
- normalize units to millimetres
- return parts, name map, and compound

## `#357`

Keep:

- authenticated multipart `POST /projects/imports/3mf`
- preflight 3MF validation before DB commit
- atomic project + `design.py` + durable `source_3mf` creation
- ordinary Python-only projects unaffected
- source archive persisted durably at project level
- compile binary asset transported by verified object-store reference
- no import job / converter worker / BREP persistence

## `#358`

Keep:

- browser `FormData`
- browser-managed multipart boundary
- authenticated-only import
- refresh project list after persistence
- use existing project activation path
- imported project remains selectable even if first activation attempt fails
- Cancel disabled while upload is pending
- no conversion polling/progress UI

---

# Generated 3MF Project Contract

The imported project should still use a generated `design.py` equivalent to:

```python
import build123d as bd
from tertius_imports import load_3mf_model

imported = load_3mf_model("source")
parts = imported.parts
parts_by_name = imported.parts_by_name
model = imported.compound
```

The significance of this contract is that imported projects remain ordinary Python-editable projects.

Do not replace this with an AI-specific imported-model representation.

---

# AI Pipeline Boundary

The AI/Pi-agent system should continue to edit project files.

It should not need to understand raw `source.3mf` to complete this refactor.

Desired boundary:

```text
Project Python files
        |
        v
AI / Pi agent
        |
        v
updated Python files
        |
        +-------------------+
                            |
binary project inputs ------+
                            |
                            v
                      compile command
```

A future feature may intentionally expose a bounded geometry summary to AI, but that should be designed separately.

Do not build that now.

---

# Optional Cleanup Only If Trivial

If it is essentially free after the abstraction is introduced, rename:

```python
delete_job_source_artifacts
```

to something like:

```python
delete_job_input_artifacts
```

and centralize terminal cleanup around that terminology.

Similarly prefer:

```python
project_input_artifacts
job_input_artifacts
```

over public APIs whose names mention 3MF.

Do not perform churn solely for naming consistency if it materially increases the diff.

---

# Implementation Strategy

1. Start from the current `#357` branch/head.
2. Run the focused backend tests before changing code.
3. Add/refine tests for generic compile input behaviour.
4. Extract the minimal abstraction.
5. Replace normal compile inline 3MF asset preparation.
6. Replace stale-republish inline 3MF asset preparation.
7. Generalize job-input cleanup naming/queries if it remains a small change.
8. Run focused tests.
9. Run type/lint checks.
10. Rebase/restack `#358` only if `#357` head changes require it.
11. Do not modify `#356` unless a compile-input API compatibility issue genuinely requires it.

---

# Verification

Run the repository's current equivalent of the following.

Backend focused tests:

```bash
uv run pytest   server/tests/test_lean_3mf_import_api.py   server/tests/test_compile_result_consumer.py   server/tests/test_repositories.py   server/tests/test_object_store_connection.py   server/tests/test_tertius_imports_runtime.py   server/tests/test_three_mf_archive.py
```

Static checks:

```bash
uv run mypy
uv run ruff check server
git diff --check
```

Runtime parity / deployment checks already used by the stacked PRs should also remain green where available:

```bash
bash scripts/check-runtime-parity.sh
helm lint infra/charts/tertius
```

Frontend should not require source changes for this focused backend refactor, but after restacking `#358` run:

```bash
npm --prefix ui test -- ProjectSelector.test.tsx projectStorage.test.ts
npm --prefix ui run typecheck
npm --prefix ui run build
```

If some existing checks require Docker/k3s and the environment lacks them, report the exact missing prerequisite rather than weakening tests or changing code to accommodate the local environment.

---

# Acceptance Criteria

The work is complete when all of the following are true:

- [ ] No broad AI/Pi-agent refactor was introduced.
- [ ] `#356` retains its existing lean sandbox loader design.
- [ ] `#358` retains its existing lean UI/import behaviour.
- [ ] Normal Python-only compilation still produces no binary assets.
- [ ] Imported 3MF projects still compile through `source.3mf`.
- [ ] Durable project input assets are snapshotted onto compile jobs.
- [ ] Stale-job republish uses only the job-pinned binary input snapshot.
- [ ] Job terminal cleanup removes only ephemeral job input snapshots.
- [ ] Durable project 3MF remains after compile completion/failure.
- [ ] Compile input kind → logical filename mapping is centralized.
- [ ] Compile orchestration no longer contains duplicated 3MF-specific asset construction.
- [ ] The compile-input abstraction can naturally support another binary input kind later without another copy/paste branch.
- [ ] No speculative support for STEP/OBJ/etc. was added.
- [ ] Existing 3MF validation/security limits remain unchanged.
- [ ] Existing tenant isolation remains intact.
- [ ] Focused tests and static checks are green.
- [ ] `#357` and descendant `#358` remain correctly stacked.

---

# Final Deliverable to Report

When finished, provide:

1. a concise summary of the extracted abstraction
2. files changed
3. tests added/updated
4. verification commands and results
5. confirmation that AI/Pi-agent code was intentionally left unchanged
6. any broader refactoring opportunities noticed but deliberately deferred
7. updated PR/branch SHAs if the stack was rebased
