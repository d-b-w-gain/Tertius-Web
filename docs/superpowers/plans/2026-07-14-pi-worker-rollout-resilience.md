# Pi Worker Rollout Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve in-flight Pi requests across Flux ScaledJob updates and promptly redeliver requests when a worker receives SIGTERM.

**Architecture:** Render KEDA's gradual rollout strategy for the Pi ScaledJob. Bridge SIGTERM into asyncio cancellation, settle cancellation by stopping result publication and NAKing before bounded provider cleanup, and retain existing ACK-after-publish semantics.

**Tech Stack:** Python asyncio, NATS JetStream, KEDA ScaledJob, Helm, pytest, Bash deployment fixtures.

---

### Task 1: Specify Cancellation Settlement

**Files:**
- Modify: `server/tests/test_pi_agent_job.py`
- Modify: `server/workflows/intus/pi_agent_job.py`

- [x] Add a test that cancels provider execution and blocks its cleanup, asserting NAK happens before cleanup is released.
- [x] Change the in-flight publication cancellation test to require one NAK and no ACK.
- [x] Run the focused tests and confirm they fail because cancellation currently does not NAK.
- [x] Handle `asyncio.CancelledError` separately: cancel publication and heartbeat, NAK once, await provider cleanup, then re-raise.
- [x] Run the focused worker tests and confirm they pass.

### Task 2: Bridge SIGTERM to Async Cancellation

**Files:**
- Modify: `server/tests/test_pi_agent_job.py`
- Modify: `server/workflows/intus/pi_agent_job.py`

- [x] Add a test that captures the SIGTERM callback, invokes it while `run_once` blocks, and verifies cancellation cleanup plus handler removal.
- [x] Run the test and confirm it fails because no SIGTERM wrapper exists.
- [x] Add a small async wrapper that installs and removes the SIGTERM handler, cancels `run_once`, and converts expected shutdown cancellation to exit code zero.
- [x] Run focused tests and confirm success, publish failure, cancellation, and shutdown behavior all pass.

### Task 3: Preserve Jobs During ScaledJob Updates

**Files:**
- Modify: `infra/charts/tertius/templates/pi-agent-worker.yaml`
- Modify: `infra/charts/tertius/README.md`
- Modify: `scripts/test-deployment-config.sh`

- [x] Extend I-012 to require `rollout.strategy: gradual`; run it and confirm the render test fails.
- [x] Add the gradual rollout invariant to the Pi ScaledJob template.
- [x] Document that ordinary ScaledJob updates drain existing Jobs while future Jobs use the new template.
- [x] Run deployment configuration and runtime parity checks.

### Task 4: Verify and Publish

**Files:**
- Update checkboxes in: `docs/superpowers/plans/2026-07-14-pi-worker-rollout-resilience.md`

- [x] Run the complete Pi worker test module and backend type checks.
- [x] Run Helm deployment configuration, runtime parity, and diff checks.
- [x] Validate gradual rollout behavior in isolated k3s without invoking a provider request.
- [x] Review the final diff for scope, telemetry safety, and existing auth-helper guarantees.
- [ ] Commit, push, open a PR, watch CI, merge when green, and verify Flux reconciliation.
