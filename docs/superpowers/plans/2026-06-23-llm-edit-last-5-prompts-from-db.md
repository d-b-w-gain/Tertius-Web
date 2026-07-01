# LLM Edit: pass last 5 DB prompts to LLM endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the last 5 persisted LLM prompts from `llm_edit_jobs` as part of server-side context when calling the LLM, while keeping UI request payloads file-only and preventing stale/malicious client-side conversation state.

**Architecture:** Backend constructs the full LLM message context from trusted DB state in `start_llm_file_edit_job` / `_run_llm_file_edit_job` before calling `llm_client.build_file_edit_messages`. The UI continues to send current file pointers and active prompt only.

**Tech Stack:** FastAPI (Python), SQLAlchemy, Pydantic, pytest, React/Vite TypeScript.

---

## Current State

- `server/core/repositories.py` already stores each LLM request as `LlmEditJob.request_payload["prompt"]` and exposes `LlmEditRepository.list_jobs_for_project()` (ascending order, default limit 200).
- `server/workflows/intus/intus_server.py` currently passes only request prompt + file payload into `generate_file_edits`.
- `server/core/llm_client.py` currently builds LLM messages from a single request prompt and selected file contents.
- UI already loads conversation display from `GET /projects/{name}/files/llm-edit/jobs`; there is no strict requirement to send history in the submit request.

## Required Behavior

- For every LLM edit call, include up to the 5 most recent prompts from the same project in chronological order (oldest→newest) plus the current request prompt.
- Prompts must be loaded by project from DB at request time, not provided by UI.
- If fewer than 5 exist, include all available.
- If no prior jobs exist, call remains unchanged except for explicit empty history context.

## Files to modify

- `server/core/repositories.py`
  - Add helper for recent edit prompts.
- `server/core/llm_client.py`
  - Extend message builder to accept prior prompts.
- `server/workflows/intus/intus_server.py`
  - Read recent prompts before call and pass into edit generator.
- `server/tests/test_repositories.py`
  - Add tests for ordering, project scoping, and limit behavior.
- `server/tests/test_llm_file_edit.py`
  - Add tests for server-side context pass-through and that payload is independent of UI prompts.
- `server/tests/test_llm_client.py`
  - Add message-builder tests for 0/3/5 prompt history.

## Anti-Patterns (DO NOT)

- Do not trust UI to provide prior conversation payload.
- Do not reuse global job history with no project filter.
- Do not include most-recent first in message context when you want conversational order oldest→newest.
- Do not fetch >5 prompts or include non-prompt user-entered metadata.
- Do not change existing job list ordering consumed by history UI unless that endpoint is intentionally updated too.

## Test Case Specifications

### Unit Tests

- **UT-001** `server/tests/test_repositories.py`
  - `test_list_recent_prompts_returns_five_latest_in_chronological_order`
  - Setup: create 6 prompts for one project.
  - Expected: returned list has 5 oldest-first entries of last six.
- **UT-002** `server/tests/test_repositories.py`
  - `test_list_recent_prompts_filters_tenant_and_project`
  - Expected: cross-project and cross-tenant rows are excluded.
- **UT-003** `server/tests/test_llm_client.py`
  - `test_build_file_edit_messages_includes_prior_prompts`
  - Expected: system/user message contains 3 prior prompts in order then current request.
- **UT-004** `server/tests/test_llm_client.py`
  - `test_build_file_edit_messages_with_no_prior_prompts`
  - Expected: message body still includes selected files and active prompt.
- **UT-005** `server/tests/test_llm_file_edit.py`
  - `test_llm_file_edit_uses_db_prompt_history_not_request_payload`
  - Expected: route asks repo for recent prompts and passes to llm client.

### Integration Tests

- **IT-001** `server/tests/test_llm_file_edit.py`
  - `test_async_llm_edit_job_includes_last_5_prompts`
  - Verify background path reads recent prompts before provider call.
- **IT-002** `server/tests/test_llm_file_edit.py`
  - `test_history_limit_is_project_specific`
  - Verify prompts from other project are not included.

## Task 1: Add repository prompt-history accessor

**Files:**
- Modify: `server/core/repositories.py`
- Test: `server/tests/test_repositories.py`

- [ ] **Step 1: Write tests**

```python
from core.models import LlmEditJob
from uuid import uuid4


def test_list_recent_prompts_returns_five_latest_in_chronological_order(db_session):
    project = seed_project(...)
    repo = LlmEditRepository(db_session, project.tenant_id)

    for i in range(6):
        repo.start_job(
            project_name=project.name,
            payload={"prompt": f"prompt-{i}"},
            file_ids=[],
            requested_by=project.owner_id,
            active_file_id=None,
            request_metadata={},
            status="done",
        )
    db_session.commit()

    prompts = repo.list_recent_prompts(project.name, limit=5)
    assert prompts == ["prompt-1", "prompt-2", "prompt-3", "prompt-4", "prompt-5"]
```

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

- [ ] **Step 2: Implement repository helper**

```python
def list_recent_prompts(self, project_name: str, limit: int = 5) -> list[str]:
    project = self.get_project(project_name)
    if not project:
        return []
    rows = (
        self.db.query(LlmEditJob)
        .filter(
            LlmEditJob.tenant_id == self.tenant_id,
            LlmEditJob.project_id == project.id,
            LlmEditJob.request_payload.op("?", "$.prompt").as_string().isnot(None),
        )
        .order_by(LlmEditJob.created_at.desc(), LlmEditJob.id.desc())
        .limit(limit)
        .all()
    )
    return list(
        filter(
            None,
            (job.request_payload.get("prompt") for job in reversed(rows)),
        )
    )
```

- [ ] **Step 3: Run repository tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_repositories.py -q
```

## Task 2: Pass prior prompts into message builder

**Files:**
- Modify: `server/core/llm_client.py`
- Test: `server/tests/test_llm_client.py`

- [ ] **Step 1: Write failing tests**

```python
def test_build_file_edit_messages_includes_prior_prompts():
    request = LlmFileEditInput(prompt="now", files=[], active_file_id=None)
    result = build_file_edit_messages(request, prior_prompts=["old-1", "old-2"], files=...)
    user_msg = result[1]["content"]
    assert "History (5 most recent prompts):\n1. old-1\n2. old-2" in user_msg
    assert "Current prompt:\nnow" in user_msg
```

- [ ] **Step 2: Update builder signature and implementation**

```python
def build_file_edit_messages(
    request: LlmFileEditInput,
    files: list[LlmEditableFile],
    prior_prompts: list[str],
) -> list[dict[str, str]]:
    ...
    history_block = ""
    if prior_prompts:
        numbered = "\n".join(f"{i+1}. {p}" for i, p in enumerate(prior_prompts))
        history_block = f"Conversation history (up to 5):\n{numbered}\n\n"
    user_message = f"{history_block}User prompt:\n{request.prompt}"
```

- [ ] **Step 3: Run llm-client tests**

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_client.py -q
```

## Task 3: Load last 5 prompts from DB in async job execution

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Test: `server/tests/test_llm_file_edit.py`

- [ ] **Step 1: Write endpoint tests**

```python

def test_async_llm_edit_job_includes_last_5_prompts(db_client, mock_llm_client):
    # seed 6 jobs for project
    # call POST /projects/{name}/files/llm-edit/jobs and poll job status
    # assert mock_llm_client.build_file_edit_messages was called with prior prompts prompt-1..prompt-5
```

- [ ] **Step 2: Thread recent prompts into async job path**

```python
recent_prompts = LlmEditRepository(db, ctx.tenant_id).list_recent_prompts(project.name, limit=5)
result = await llm_client.generate_file_edits(
    request=request,
    files=file_payloads,
    prior_prompts=recent_prompts,
    ...
)
```

- [ ] **Step 3: Thread recent prompts into async path**

```python
recent_prompts = project_repo.llm_edit_jobs().list_recent_prompts(project_name, limit=5)
result = await generate_file_edits(..., prior_prompts=recent_prompts, ...)
```

- [ ] **Step 4: Add contract tests for async path and tenant scoping**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest server/tests/test_llm_file_edit.py -q
```

## Task 4: Optional hygiene

- Keep request payload to LLM edit endpoint unchanged (`prompt`, `files`, `active_file_id`, `metadata`).
- Add request shape comments in types/tests clarifying no `previous_prompts` field.

## Completion Criteria

- Backend always includes up to 5 most recent prompts from DB for each LLM call.
- History order in prompt is oldest→newest (most recent 5).
- No UI payload fields for conversation history are required.
- Old conversation listing endpoint behavior remains unchanged.
- Added/updated tests cover async route wiring, polling/history behavior, and builder formatting.
