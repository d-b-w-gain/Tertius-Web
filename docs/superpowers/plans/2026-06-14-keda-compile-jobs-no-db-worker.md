# KEDA Compile Jobs With No Database Worker Access

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for implementation when tool policy allows sub-agents, or `superpowers:executing-plans` when implementing task-by-task locally. Track progress by updating the checkbox steps in this file.

**Goal:** Replace the long-lived compile-worker Deployment with KEDA-created Kubernetes Jobs that run one compile each, then exit. Compile Job pods must never receive database credentials or write directly to Postgres. They only read compile commands from NATS JetStream and publish compile results back to NATS. The trusted API process consumes results and performs all database writes.

**Architecture:** FastAPI remains the only database writer for compile state and artifacts. NATS JetStream becomes the worker input and output transport. KEDA watches JetStream consumer lag and creates one-shot compile Jobs. Each Job runs untrusted code inside the existing gVisor boundary, publishes a bounded result message, acknowledges its request message, and terminates.

**Tech Stack:** Python, FastAPI, SQLAlchemy 2, Postgres, NATS JetStream, KEDA `ScaledJob`, Helm, gVisor, pytest, k3s.

---

## Current Repo Facts

- The current Helm chart renders a long-lived compile-worker `Deployment` in `infra/charts/tertius/templates/compile-worker.yaml`.
- That deployment currently runs the API image with `command: ["sh", "/app/server/start-compile-worker.sh"]`.
- The current compile worker receives app DB environment through `envFrom` and explicit `APP_DB_PASSWORD` / `APP_DB_OWNER` secret refs.
- The current worker imports `workflows.intus.compile_executor.execute_compile_job`, which reads compile snapshots, runs the sandbox, records artifacts, prunes artifacts, and finishes DB job state.
- Current compile source snapshots are persisted in `compile_job_files`.
- Current artifacts are DB-backed in `artifacts.content`.
- Live database sizing on 2026-06-14:
  - Latest artifact: `180,060 bytes` GLB for `default_purlin`.
  - Latest source snapshot for that compile: `3,280 bytes`.
  - Largest current artifact: `180,060 bytes`.
  - Largest current project source file: `40,775 bytes`.
  - All artifacts total: `1,619,880 bytes`.
  - All project files total: `81,526 bytes`.
  - All compile snapshot files total: `19,727 bytes`.

## Design Decision

Compile Jobs are untrusted execution pods. They must not receive:

- `DATABASE_URL`
- `APP_DB_HOST`
- `APP_DB_NAME`
- `APP_DB_OWNER`
- `APP_DB_PASSWORD`
- the general app Secret via `envFrom`
- Kubernetes service account tokens
- any credential that can mutate database state outside a single compile-result capability

Compile Jobs may receive:

- internal NATS URL
- compile request stream/subject names
- compile result stream/subject names
- timeout and payload-size limits
- one-shot process configuration

The API owns:

- creating `compile_jobs`
- snapshotting source
- publishing compile commands
- consuming compile results
- validating result identity and idempotency
- recording artifacts
- marking jobs terminal
- pruning old artifacts
- recovering stale queued/running jobs

## NATS Payload Constraint

NATS enforces `max_payload` at the server. Official NATS FAQ states the default is `1 MB`, it can be increased up to `64 MB`, and the recommended practical maximum is closer to `8 MB`. JetStream streams also support `MaxMsgSize`; a stream can reject messages above that per-stream size.

Plan-level limits:

- Set a Tertius compile message limit to `8 MiB` or lower.
- Set JetStream `MaxMsgSize` for compile streams to the same or lower value.
- Reject compile source snapshots that exceed the configured request limit.
- Reject compile result artifacts that exceed the configured result limit.
- Truncate stdout/stderr/error text before publishing result messages.
- Keep a future escape hatch for large artifacts: NATS carries metadata and the worker uploads bytes through a separate constrained blob path. Do not implement that fallback in this pass unless real artifacts exceed the configured NATS limit.

Given the current live database sizes, a NATS-only worker input/output path is viable today.

## Scope

In scope:

- Add NATS message contracts for request source bundles and result payloads.
- Add a one-shot compile Job entrypoint that does not import DB modules or settings that require DB credentials.
- Add an API-side compile result consumer that writes artifacts and terminal job state.
- Replace the compile-worker Deployment with a KEDA `ScaledJob`.
- Remove DB env and app Secret env injection from compile Job pods.
- Add NetworkPolicy and pod security controls for compile Jobs.
- Add local/k3s validation that proves compile Jobs have no DB env and no DB network egress.
- Update tests for message contracts, one-shot worker behavior, API result ingestion, and Helm rendering.

Out of scope:

- Public NATS routing.
- NATS authentication beyond existing internal cluster access.
- Object storage for artifacts larger than NATS limits.
- Changing the UI compile polling contract.
- Replacing Postgres-backed artifact storage.
- Replacing NATS with another queue.
- General multi-workflow compile architecture beyond the current Intus compile path.

## Target Message Flow

1. UI calls `POST /projects/{name}/compile`.
2. API validates auth, stages submitted source, creates a queued `compile_jobs` row, snapshots source files, and commits.
3. API publishes `CompileCommand` to `tertius.compile.request`.
4. KEDA sees pending JetStream lag for durable consumer `compile-workers`.
5. KEDA creates one Kubernetes Job.
6. Job starts, pulls one request message, and validates command payload.
7. Job hydrates source from the NATS payload into a temporary directory.
8. Job runs `run_compile_sandbox()`.
9. Job publishes `CompileResult` to `tertius.compile.result`.
10. Job acks the request message only after the result publish succeeds.
11. Job exits.
12. API result consumer receives the result message, validates it against DB job state, records artifact/error, marks job terminal, publishes any existing success/failed event if still needed, commits, then acks the result message.
13. UI continues polling the existing job status endpoint and sees the DB-backed terminal state.

## Message Contracts

### Compile Request

Modify `server/core/compile_messages.py`.

`CompileCommand` should include enough source data for the worker to avoid API and DB reads:

```python
class CompileSourceFile(BaseModel):
    filename: str
    content: str


class CompileCommand(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    requested_by: UUID
    export_format: str
    created_at: datetime
    files: list[CompileSourceFile]
    request_id: str
```

`request_id` is a deterministic idempotency key, for example `compile-request:{job_id}`. Continue using `Nats-Msg-Id` when publishing.

### Compile Result

Add a worker-to-API result contract:

```python
class CompileResultPayload(BaseModel):
    job_id: UUID
    tenant_id: UUID
    project_id: UUID
    export_format: str
    status: Literal["succeeded", "failed"]
    artifact_content_base64: str | None = None
    artifact_byte_size: int | None = None
    artifact_content_type: str | None = None
    error_code: str | None = None
    user_message: str | None = None
    error: str | None = None
    retryable: bool = False
    worker_started_at: datetime
    worker_finished_at: datetime
```

Use base64 for artifact bytes in JSON. If result payload size becomes a concern, switch only the result subject to a binary envelope later. Do not make the worker write DB as an optimization.

## KEDA Design

Use `ScaledJob`, not `ScaledObject`.

Rationale:

- `ScaledObject` scales a long-lived Deployment.
- `ScaledJob` creates batch Jobs for queued work.
- The goal is one untrusted compile per pod lifetime.

Create a new Helm template replacing `infra/charts/tertius/templates/compile-worker.yaml` with a KEDA `ScaledJob`.

Representative shape:

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledJob
metadata:
  name: {{ include "tertius.apiName" . }}-compile
spec:
  pollingInterval: {{ .Values.compileJobs.pollingInterval }}
  maxReplicaCount: {{ .Values.compileJobs.maxReplicaCount }}
  successfulJobsHistoryLimit: {{ .Values.compileJobs.successfulJobsHistoryLimit }}
  failedJobsHistoryLimit: {{ .Values.compileJobs.failedJobsHistoryLimit }}
  scalingStrategy:
    strategy: eager
  jobTargetRef:
    parallelism: 1
    completions: 1
    backoffLimit: 0
    activeDeadlineSeconds: {{ .Values.compileJobs.activeDeadlineSeconds }}
    ttlSecondsAfterFinished: {{ .Values.compileJobs.ttlSecondsAfterFinished }}
    template:
      metadata:
        labels:
          app.kubernetes.io/component: compile-job
      spec:
        restartPolicy: Never
        runtimeClassName: {{ .Values.compileJobs.runtimeClassName | quote }}
        automountServiceAccountToken: false
        enableServiceLinks: false
        containers:
          - name: compile
            image: "{{ .Values.api.image.repository }}:{{ .Values.api.image.tag }}"
            imagePullPolicy: {{ .Values.api.image.pullPolicy }}
            command: ["sh", "/app/server/start-compile-job.sh"]
            envFrom:
              - configMapRef:
                  name: {{ include "tertius.configName" . }}
            env:
              - name: COMPILE_JOB_MODE
                value: "nats-only"
            resources:
              {{- toYaml .Values.compileJobs.resources | nindent 14 }}
  triggers:
    - type: nats-jetstream
      metadata:
        natsServerMonitoringEndpoint: "{{ include "tertius.natsMonitoringEndpoint" . }}"
        account: "$G"
        stream: {{ .Values.app.config.compileStreamName | quote }}
        consumer: {{ .Values.app.config.compileWorkerQueue | quote }}
        lagThreshold: "1"
        activationLagThreshold: "0"
        useHttps: "false"
```

Do not include `secretRef` to `tertius-app` in the compile Job template.

## Security Boundary

The compile Job pod must use:

- `runtimeClassName: gvisor` in production defaults.
- `restartPolicy: Never`.
- `automountServiceAccountToken: false`.
- `enableServiceLinks: false`.
- `hostNetwork: false`.
- `hostPID: false`.
- `hostIPC: false`.
- `runAsNonRoot: true`.
- `allowPrivilegeEscalation: false`.
- `capabilities.drop: ["ALL"]`.
- `seccompProfile.type: RuntimeDefault`.
- `readOnlyRootFilesystem: true` if compatible with Python/build123d runtime.
- size-limited `emptyDir` for `/tmp` and compile workdir.
- CPU, memory, and ephemeral-storage limits.
- no PVC.
- no `hostPath`.
- no Docker/containerd socket.

NetworkPolicy:

- Deny all ingress.
- Allow egress only to:
  - DNS if required.
  - NATS service port `4222`.
  - NATS monitoring is not required by worker pods; KEDA needs monitoring access, not the worker.
- Do not allow compile Job egress to Postgres.
- Do not allow compile Job egress to Keycloak, Valkey, API, or the public internet in this NATS-only design.

## Anti-Patterns

| Do not | Do instead | Why |
|---|---|---|
| Do not pass `APP_DB_PASSWORD`, `DATABASE_URL`, or app DB owner env to compile Jobs. | Let API consume NATS results and write DB. | Compile pods run untrusted code and must not hold DB credentials. |
| Do not keep a long-lived compile-worker Deployment. | Use KEDA `ScaledJob` with one request per Job. | The isolation goal is pod death after each compile. |
| Do not make the worker call internal API to write results in this pass. | Worker publishes result to NATS; API consumes NATS. | NATS-only worker IO gives a smaller network and credential boundary. |
| Do not ack the request before result publish succeeds. | Ack only after result publish success. | A pod crash before result publish must redeliver the request. |
| Do not let the worker mark DB jobs failed on malformed commands. | Worker terms invalid NATS messages; API stale-job recovery handles DB state. | Invalid queue input is not a reason to expose DB writes to worker. |
| Do not put unbounded artifact bytes in NATS. | Enforce configured payload limits and fail oversized results cleanly. | NATS message size is finite and configurable. |
| Do not rely on Kubernetes Job retries for compile retry. | Use JetStream redelivery and API idempotency. | `backoffLimit: 0` avoids multiple pods fighting over one message. |
| Do not import `core.db`, `SessionLocal`, or SQLAlchemy models in the one-shot worker. | Keep worker code limited to NATS, message models, runtime hydration, and sandbox execution. | Import boundaries prevent accidental DB coupling. |

## Error Handling Matrix

| Scenario | Worker action | API result consumer action | User-visible result |
|---|---|---|---|
| Request message is invalid JSON | `term` message and exit non-zero or zero with error log | Stale queued recovery eventually handles DB job if one exists | Job may be retried or failed by API recovery |
| Request has no source files | Publish failed result with `missing_snapshot`, ack request | Mark job failed if still non-terminal | UI shows retryable compile failure |
| Sandbox succeeds under size limit | Publish succeeded result with base64 artifact, ack request | Record artifact, mark succeeded, ack result | UI shows artifact |
| Sandbox succeeds but artifact exceeds result limit | Publish failed result with `artifact_too_large`, ack request | Mark failed with non-secret error | UI shows size-limit failure |
| Sandbox times out | Publish failed result with `timeout`, ack request | Mark failed retryable | UI shows timeout |
| Worker crashes before result publish | No ack | No result to consume; JetStream redelivers after `ack_wait` | Job remains queued/running until retry |
| Worker publishes result but crashes before request ack | Request redelivers | API idempotently ignores duplicate terminal result | UI remains correct |
| API result consumer crashes before DB commit | Result message remains unacked | API consumes result again | Eventually terminal |
| Duplicate result for terminal job | No worker decision | API acks and ignores duplicate | UI unchanged |
| Result identity does not match job | Worker has already published | API marks suspicious result ignored or failed according to policy, then acks | UI follows DB-trusted state |

## Task 1: Define NATS-Only Compile Message Contracts

**Files:**

- Modify: `server/core/compile_messages.py`
- Modify: `server/tests/test_nats_client.py`
- Modify or add: `server/tests/test_compile_messages.py`

- [x] **Step 1: Add source-file and result payload models**

Add `CompileSourceFile` and `CompileResultPayload`.

- [x] **Step 2: Extend `CompileCommand`**

Add `files` and `request_id`. Keep existing identity fields.

- [x] **Step 3: Add validation helpers**

Add helper logic to compute serialized payload size and reject over-limit request/result messages before publish.

- [x] **Step 4: Test serialization**

Cover:

- request includes file names and source contents
- result includes artifact byte size metadata
- oversized request is rejected before publish
- oversized result is represented as a failed result, not published as oversized success

## Task 2: Publish Source Bundles From the API

**Files:**

- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_compile_flow.py`

- [x] **Step 1: Keep DB snapshot creation**

Continue snapshotting `compile_job_files` for API-trusted history, status inspection, and recovery.

- [x] **Step 2: Include source files in `CompileCommand`**

After `repo.files_for_runtime(name)`, build `CompileSourceFile` entries into the command.

- [x] **Step 3: Set deterministic request id**

Use `compile-request:{job.id}` as command `request_id` and NATS `Nats-Msg-Id`.

- [x] **Step 4: Enforce request size**

If serialized command exceeds `COMPILE_REQUEST_MAX_BYTES`, fail the job before publish with `source_bundle_too_large`.

- [x] **Step 5: Update tests**

Assert compile enqueue publishes source files and does not publish if source bundle exceeds the configured limit.

## Task 3: Add API-Side Result Consumer

**Files:**

- Create: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/main.py` or the server startup path used for background tasks
- Modify: `server/core/repositories.py`
- Modify: `server/tests/test_compile_result_consumer.py`

- [x] **Step 1: Add result subject config**

Add `COMPILE_RESULT_SUBJECT`, default `tertius.compile.result`.

- [x] **Step 2: Ensure stream includes result subject**

Update `ensure_compile_stream()` subjects to include:

- request subject
- result subject
- existing succeeded/failed subjects if still used by the UI/event path

- [x] **Step 3: Add durable API result consumer**

Use a separate durable consumer name, for example `compile-result-api`.

- [x] **Step 4: Implement idempotent result application**

Given `CompileResultPayload`:

- find matching job by `job_id`, `tenant_id`, `project_id`, `export_format`
- if job is terminal, ack and ignore
- if status is `succeeded`, base64-decode artifact, validate byte size, record artifact, finish job succeeded, prune old artifacts
- if status is `failed`, finish job failed with structured error fields
- commit, then ack result message
- rollback and nak on transient DB errors

- [x] **Step 5: Preserve UI contract**

The existing job status endpoint remains DB-backed and unchanged from the UI perspective.

## Task 4: Add One-Shot NATS-Only Compile Job Entrypoint

**Files:**

- Create: `server/workflows/intus/compile_job.py`
- Create: `server/start-compile-job.sh`
- Modify: `server/tests/test_compile_worker.py` or create `server/tests/test_compile_job.py`

- [x] **Step 1: Implement one-shot runner**

The process must:

- connect to NATS
- ensure stream/consumer
- pull one request message
- if no message is fetched within a short timeout, exit `0`
- validate `CompileCommand`
- hydrate files from `command.files`
- run `run_compile_sandbox()`
- publish `CompileResultPayload`
- ack request message only after result publish succeeds
- close NATS connection
- exit

- [x] **Step 2: Prevent DB imports**

The one-shot module must not import:

- `core.db`
- `SessionLocal`
- SQLAlchemy models
- `CompileRepository`
- `execute_compile_job`

- [x] **Step 3: Sanitize environment**

Reuse or keep the existing `run_compile_sandbox()` environment filtering. Add a regression test proving `APP_DB_PASSWORD` is not visible to user code even if accidentally present in the parent process.

- [x] **Step 4: Test request/result behavior**

Cover:

- no message exits cleanly
- valid command publishes success and acks
- sandbox failure publishes failed result and acks
- result publish failure does not ack request
- invalid command terms or naks according to chosen policy

## Task 5: Replace Helm Deployment With KEDA ScaledJob

**Files:**

- Replace: `infra/charts/tertius/templates/compile-worker.yaml`
- Add or modify: `infra/charts/tertius/templates/compile-job-networkpolicy.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Modify: `infra/charts/tertius/README.md`
- Modify: `scripts/test-deployment-config.sh`

- [x] **Step 1: Rename values**

Move from `compileWorker` to `compileJobs` values:

- `runtimeClassName`
- `maxReplicaCount`
- `pollingInterval`
- `activeDeadlineSeconds`
- `ttlSecondsAfterFinished`
- history limits
- resources
- payload limits

- [x] **Step 2: Render KEDA `ScaledJob`**

Use `nats-jetstream` scaler metadata:

- monitoring endpoint: release-local NATS monitoring port `8222`
- stream: `TERTIUS_COMPILE`
- consumer: `compile-workers`
- `lagThreshold: "1"`
- `activationLagThreshold: "0"`

- [x] **Step 3: Remove DB secrets from compile Job**

Do not render:

- `secretRef: tertius-app`
- `APP_DB_PASSWORD`
- `APP_DB_OWNER`
- any `DATABASE_URL`

- [x] **Step 4: Add NetworkPolicy**

Compile Job pods may egress to NATS only. They must not egress to Postgres.

- [x] **Step 5: Update render tests**

`scripts/test-deployment-config.sh` should fail unless Helm output contains:

- `kind: ScaledJob`
- `type: nats-jetstream`
- `app.kubernetes.io/component: compile-job`
- `runtimeClassName: "gvisor"` in default values
- `automountServiceAccountToken: false`
- `backoffLimit: 0`
- `activeDeadlineSeconds`
- NATS-only egress NetworkPolicy

It should also fail if Helm output contains DB env vars in the compile Job template.

## Task 6: Local Docker Compose Parity

**Files:**

- Modify: `docker-compose.yml`
- Modify: relevant README/dev docs

- [x] **Step 1: Rename local service**

Rename `compile-worker` to `compile-job-runner` or keep name with new command `start-compile-job.sh`.

- [x] **Step 2: Remove DB env from worker service**

Delete from compile service:

- `APP_DB_HOST`
- `APP_DB_NAME`
- `APP_DB_OWNER`
- `APP_DB_PASSWORD`
- `DATABASE_URL`

- [x] **Step 3: Add API result consumer in local stack**

Ensure the backend process starts the API-side result consumer, or document a second trusted process if background startup is not used.

## Task 7: Recovery and Idempotency

**Files:**

- Modify: `server/workflows/intus/compile_result_consumer.py`
- Modify: `server/workflows/intus/compile_worker.py` or replace stale recovery location
- Modify: `server/tests/test_compile_result_consumer.py`

- [x] **Step 1: Move stale queued recovery to API-owned code**

The old long-lived worker recovery loop must not remain in an untrusted worker. Move stale queued/running recovery into:

- API background task, preferred
- or a trusted CronJob/Deployment that has DB access but does not execute user code

- [x] **Step 2: Handle stale running jobs**

If a job is `running` past `activeDeadlineSeconds + ack_wait margin`, the trusted API recovery path may republish its original source snapshot or mark it failed retryable according to the chosen policy.

- [x] **Step 3: Idempotency**

API result consumer must ack and ignore duplicate terminal results.

- [x] **Step 4: Redelivery**

Worker request consumer must keep explicit ack and `ack_wait` larger than:

`pod startup + compile timeout + result publish + request ack margin`

Recommended starting values:

- `compileTimeoutSeconds: 600`
- `activeDeadlineSeconds: 720`
- `compileAckWaitSeconds: 900`

## Task 8: Verification

- [x] **Step 1: Unit tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache rtk uv run pytest \
  server/tests/test_compile_flow.py \
  server/tests/test_compile_job.py \
  server/tests/test_compile_result_consumer.py \
  server/tests/test_nats_client.py \
  server/tests/test_config.py \
  -q
```

- [x] **Step 2: Helm render gate**

Run:

```bash
rtk scripts/test-deployment-config.sh
```

Expected:

- `ScaledJob` renders.
- no compile-worker `Deployment` renders.
- compile Job has no DB env.
- compile Job has no app Secret env.
- compile Job has gVisor and restricted pod settings.

- [x] **Step 3: Helm lint**

Run:

```bash
rtk helm lint infra/charts/tertius
```

- [ ] **Step 4: Local k3s smoke**

Run:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk scripts/test-k3s-deployment.sh
```

Then inspect:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk kubectl get scaledjob -n tertius
KUBECONFIG=/home/johnson/.kube/config rtk kubectl get jobs -n tertius -l app.kubernetes.io/component=compile-job
KUBECONFIG=/home/johnson/.kube/config rtk kubectl get pods -n tertius -l app.kubernetes.io/component=compile-job
```

- [ ] **Step 5: Prove no DB env in compile pods**

Run against a live compile Job pod before it exits, or use a debug render/pod:

```bash
KUBECONFIG=/home/johnson/.kube/config rtk kubectl exec -n tertius "$COMPILE_POD" -- env | rg 'DATABASE_URL|APP_DB_|POSTGRES|PG'
```

Expected: no matches.

- [ ] **Step 6: Prove no Postgres egress**

Use NetworkPolicy validation or a temporary command in the compile Job image to attempt TCP connect to `tertius-postgres-rw:5432`.

Expected: connection blocked.

## Rollout Plan

1. Implement API result consumer behind a feature flag while old worker still exists.
2. Publish compile commands with source files while old worker ignores extra fields.
3. Add one-shot worker and local tests.
4. Switch local docker-compose compile service to one-shot NATS-only mode.
5. Replace Helm Deployment with KEDA `ScaledJob`.
6. Run Helm render and k3s smoke tests.
7. Deploy through PR/GitOps.
8. Verify in production:
   - no compile-worker Deployment exists
   - KEDA ScaledJob exists
   - compile Job pods are short-lived
   - compile Job pods have no DB env
   - API records artifacts from NATS results
   - UI polling contract still works

## Open Questions

- Should result artifacts be base64 JSON initially, or should result messages use a binary envelope immediately?
- Should `tertius.compile.result` live in the existing `TERTIUS_COMPILE` stream or a separate `TERTIUS_COMPILE_RESULTS` stream?
- Should KEDA be installed as a chart dependency or documented as a cluster prerequisite?
- Should local k3s install KEDA in `scripts/test-k3s-deployment.sh`, or should the smoke script fail fast with a clear prerequisite message?
- What exact result payload limit should be used first: `1 MiB`, `4 MiB`, or `8 MiB`?

## Chosen Defaults

- Start with NATS-only request and result payloads.
- Use `8 MiB` configured limit for request and result messages unless cluster NATS `max_payload` remains default `1 MiB`; in that case either raise NATS `max_payload` or set app limits to `1 MiB`.
- Keep source snapshots in DB for trusted audit/recovery.
- Keep artifact bytes in DB, written only by API.
- Use KEDA `ScaledJob` with `maxReplicaCount: 4` initially.
- Use `backoffLimit: 0`; JetStream redelivery handles worker crashes.
- Use `activeDeadlineSeconds: 720` and `compileAckWaitSeconds: 900`.
