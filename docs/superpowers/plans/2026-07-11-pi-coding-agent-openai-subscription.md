# Pi Coding Agent OpenAI Subscription Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Tertius's direct LLM provider API calls with isolated Pi coding-agent workers authenticated through one operator-owned ChatGPT Plus/Pro subscription.

**Architecture:** Keep the browser contract and FastAPI ownership of persistence. The API validates and durably queues each AI edit over NATS JetStream; a KEDA one-shot worker hydrates a temporary workspace, drives Pi in RPC mode, and publishes a bounded result; an API-side result consumer revalidates file versions, stages updates, records token usage, and completes the existing `LlmEditJob`. Pi's mutable OAuth directory lives on one retained `ReadWriteOnce` PVC shared by worker pods on the current single-node k3s cluster. An ephemeral operator login pod mounts that same claim. Credentials are never copied between pods.

**Tech Stack:** FastAPI/Pydantic/SQLAlchemy, NATS JetStream, KEDA `ScaledJob`, Pi coding agent `@earendil-works/pi-coding-agent@0.80.6`, Node.js 24, Helm, k3s `local-path` storage, React/Vite, OpenTelemetry.

---

**Document type:** Implementation plan

**Plan date:** 2026-07-11

## 1. Executive Decision

Use a manual login pod, but do **not** make copies of its volume or `auth.json` for agent pods.

The current cluster has one Ready node and one default StorageClass:

| Cluster fact | Observed value | Consequence |
|---|---|---|
| Node count | 1 | All Pi workers run on the same node today. |
| StorageClass | `local-path` | The OAuth state is node-local and survives pod replacement. |
| Access mode | `ReadWriteOnce` | Multiple pods may mount it read/write when they are on the same node; RWX is not required for this topology. |
| Binding mode | `WaitForFirstConsumer` | The first login/worker pod binds the claim to its node. Later pods follow PV node affinity. |

Pi stores OAuth credentials in `${PI_CODING_AGENT_DIR}/auth.json`, refreshes them in place, and coordinates concurrent processes with a sibling lock path. Copying the file gives each pod an independent refresh-token history and creates credential divergence. Mount the whole directory, not an `auth.json` `subPath`, because the lock file/directory must be created beside it.

The initial production limit is one active Pi worker. This is both an account-concurrency guard and a conservative response to Pi auth-lock contention reports. The shared RWO claim technically supports more same-node pods, but concurrency is not raised until a controlled lock-contention test passes.

**Multi-node boundary:** this design remains operational on a multi-node cluster only while all Pi workers can schedule onto the local PV's node. Before making worker placement highly available, add an RWX-capable storage backend or replace file-backed OAuth with a supported centralized credential broker. Do not solve that transition by copying `auth.json`.

## 2. Strategic Blueprint

| Question | Decision | Implementation implication |
|---|---|---|
| Exact problem | Direct OpenAI-compatible/Anthropic calls require API keys, provider-specific parsing, retry logic, and pricing configuration. The target is Pi using `openai-codex` subscription OAuth. | Remove provider SDK execution from FastAPI and make Pi the only model execution path. |
| Success metrics | No provider API key in the API pod; 100% of AI file-edit jobs go through JetStream and Pi; OAuth survives worker/login pod deletion; the full authenticated k3s `live-flow` passes; tool tests prove Pi's exposed tools cannot read the auth directory. | Add static secret checks, worker integration tests, PVC restart tests, and the real harness gate. |
| Structural advantage | Tertius already has a DB-free one-shot compile-worker pattern with NATS, KEDA, API-side result persistence, and hardened pods. | Mirror that boundary instead of embedding a long-lived Pi process in FastAPI. |
| Core architecture | Durable command/result messages and one-shot workers; API remains the only DB writer. | Add a dedicated Pi stream, worker, result consumer, image, and chart resources. |
| Stack rationale | Reuse Python/NATS/Helm code already operated by the project; use Node only inside the Pi image because Pi is distributed as a Node package. | Keep the API image free of Node/Pi and publish `tertius-pi-agent` separately. |
| MVP | Existing asynchronous multi-file AI edit, one OpenAI subscription model, token quota reporting, manual login/verify/logout operations, serial worker execution. | Preserve `POST/GET .../files/llm-edit/jobs`; return one configured model. |
| Not building | Per-user OpenAI logins, multi-account routing, bash/test execution by Pi, repository cloning, RWX storage, automatic browser login, or a second synchronous build-script transport. | Remove the unused legacy build-script endpoint and block all tools except bounded workspace file tools. |

## 3. Scope and Preconditions

### In scope

- Replace active direct provider calls used by Intus AI file editing.
- Remove the unused synchronous `POST /projects/{name}/build-script/generate` route and its direct-provider tests.
- Preserve the existing AI-edit submit, status, history, and polling contracts.
- Keep project-level job exclusivity and optimistic file-version checks.
- Preserve token-rate and daily-token quota enforcement.
- Replace dollar-budget UI/API semantics with token usage because subscription consumption is not API spend.
- Add a dedicated Pi worker image, NATS stream, KEDA `ScaledJob`, auth PVC, network policy, scripts, docs, tests, and observability.
- Support canonical k3s and documented Compose development/parity adapters.

### Preconditions

- The operator account has a ChatGPT Plus or Pro subscription and can complete Pi's `openai-codex` OAuth login.
- The deployment owner confirms that the intended internal/multi-user workload is permitted for that account before enabling production traffic. This is a release gate, not an application feature.
- KEDA and the `ScaledJob` CRD remain installed where `piAgent.enabled=true`.
- The current single-node `local-path` topology remains the supported first release topology.

### Explicit non-goals

- Do not expose OAuth login to Tertius end users.
- Do not mount Pi OAuth state into API, UI, compile, NATS, or observability pods.
- Do not let Pi run shell commands, tests, package managers, Git, or arbitrary extensions.
- Do not persist Pi sessions or generated source outside the existing database-backed file workflow.
- Do not rewrite historical plan documents that describe the old API-key architecture.
- Do not add a database migration; existing `LlmEditJob` and `LlmUsageRecord` columns are sufficient.

## 4. Target Architecture

```text
Browser
  |
  | existing POST /files/llm-edit/jobs and GET status/history
  v
FastAPI
  |  validate tenant/project/file versions and token quota
  |  persist LlmEditJob
  |  publish PiAgentCommand
  v
NATS JetStream: TERTIUS_PI_AGENT
  | tertius.pi.request
  v
KEDA one-shot Pi worker (maxReplicaCount: 1)
  | mount RWO auth PVC at /var/lib/pi-agent
  | hydrate selected files into emptyDir /workspace/repo
  | spawn Pi RPC with no session, no bash, explicit guard extension
  | collect changed existing files and session token stats
  | publish PiAgentResult, then ACK command
  v
NATS JetStream
  | tertius.pi.result
  v
FastAPI result consumer
  | revalidate job identity and original file versions
  | publish idempotent billing token event
  | stage file updates and usage in one DB transaction
  | complete LlmEditJob
  v
Browser polling sees existing terminal result
```

### Ownership boundaries

| Concern | Owner | Must not own |
|---|---|---|
| Authentication, tenancy, quota preflight, request validation | API | Pi OAuth files |
| OAuth refresh and model/tool loop | Pi worker | Database credentials or Kubernetes API access |
| Project file persistence, snapshots, usage rows, job status | API result consumer | Provider execution |
| Durable transport and redelivery | NATS JetStream | Source-of-truth job state |
| Worker count | KEDA | Credential provisioning |
| OAuth provisioning/revocation | Cluster operator login pod | User-facing application flow |

## 5. Fixed Runtime Contract

### Pi version and invocation

Pin `@earendil-works/pi-coding-agent` exactly to `0.80.6` in `server/pi/package-lock.json`. Upgrades require a normal dependency PR that reruns the worker, auth-lock, and k3s tests; do not install `latest` during image startup.

The worker launches this argument shape from `/workspace/repo`:

```text
pi
--mode rpc
--no-session
--provider openai-codex
--model gpt-5.5
--thinking high
--tools read,edit,write,grep,find,ls
--no-extensions
--extension /opt/tertius-pi/workspace-guard.ts
--no-skills
--no-prompt-templates
--no-themes
--no-context-files
--no-approve
--append-system-prompt <configured Tertius prompt>
```

The user prompt is sent over stdin as the exact RPC JSONL shape `{ "id": "<correlation-id>", "type": "prompt", "message": "<user prompt>" }`; it is not placed in the process command line or logs. At startup the wrapper requests `get_state` and asserts the resolved provider/model are exactly `openai-codex`/`gpt-5.5`, rather than trusting Pi's fuzzy model selection. The wrapper waits for `agent_settled`, requests `get_session_stats`, maps `tokens.input`, `tokens.output`, `tokens.cacheRead`, `tokens.cacheWrite`, and `tokens.total` into Tertius token usage without consuming Pi's `cost`, closes stdin, sends `SIGTERM`, waits 10 seconds, and sends `SIGKILL` only if Pi remains alive.

Pi 0.80.6 requires Node `>=22.19.0`; the worker image intentionally selects Node 24.

Set these process environment values:

| Variable | Value | Purpose |
|---|---|---|
| `PI_CODING_AGENT_DIR` | `/var/lib/pi-agent` | Shared OAuth/config directory. |
| `PI_SKIP_VERSION_CHECK` | `1` | Prevent unneeded startup traffic. |
| `PI_TELEMETRY` | `0` | Disable Pi install/update telemetry. |
| `HOME` | `/tmp/home` | Keep incidental home writes on bounded `emptyDir`, away from the image layer. |

### Limits

| Limit | Initial value | Enforcement |
|---|---:|---|
| Active workers | 1 | KEDA `maxReplicaCount`. |
| Worker wall timeout | 480 seconds | Python RPC wrapper. |
| Kubernetes active deadline | 540 seconds | Job template. |
| Agent turns | 12 | Count `turn_end`; send RPC `abort` when exceeded. |
| Tool calls | 48 | Count `tool_execution_start`; abort when exceeded. |
| NATS ACK heartbeat | 30 seconds | Call `msg.in_progress()` while Pi runs. |
| NATS ACK wait | 90 seconds | Durable request consumer. |
| NATS max delivery | 2 | One recovery attempt after abrupt pod loss. |
| Request/result message | 524,288 bytes each | Pydantic serializer preflight and stream `max_msg_size`. |
| Selected files | 20 | Existing context selection limit. |
| Selected source characters | 80,000 | Existing context selection limit. |
| Workspace volume | 128 MiB | `emptyDir.sizeLimit`. |
| Auth volume request | 64 MiB | PVC request; actual local-path accounting remains node-local. |
| Pi worker memory | request 512 MiB, limit 1 GiB | Helm resources. |
| Pi worker CPU | request 500m, limit 2 | Helm resources. |

### API configuration

Add these settings and render them consistently through `.env.example`, Helm, Compose dev, and Compose parity:

| Setting | Default |
|---|---|
| `PI_AGENT_ENABLED` | `false` |
| `PI_AGENT_PROVIDER` | `openai-codex` |
| `PI_AGENT_MODEL` | `gpt-5.5` |
| `PI_AGENT_MODEL_LABEL` | `GPT-5.5` |
| `PI_AGENT_THINKING` | `high` |
| `PI_AGENT_TIMEOUT_SECONDS` | `480` |
| `PI_AGENT_MAX_TURNS` | `12` |
| `PI_AGENT_MAX_TOOL_CALLS` | `48` |
| `PI_AGENT_ESTIMATED_OUTPUT_TOKENS` | `65536` |
| `PI_AGENT_STREAM_NAME` | `TERTIUS_PI_AGENT` |
| `PI_AGENT_REQUEST_SUBJECT` | `tertius.pi.request` |
| `PI_AGENT_RESULT_SUBJECT` | `tertius.pi.result` |
| `PI_AGENT_WORKER_QUEUE` | `pi-agent-workers` |
| `PI_AGENT_RESULT_CONSUMER` | `pi-agent-result-api` |
| `PI_AGENT_ACK_WAIT_SECONDS` | `90` |
| `PI_AGENT_MAX_DELIVER` | `2` |
| `PI_AGENT_REQUEST_MAX_BYTES` | `524288` |
| `PI_AGENT_RESULT_MAX_BYTES` | `524288` |
| `PI_AGENT_STREAM_MAX_AGE_SECONDS` | `86400` |
| `PI_AGENT_STREAM_MAX_BYTES` | `67108864` |

Keep `LLM_FILE_EDIT_MAX_CONTEXT_FILES`, `LLM_FILE_EDIT_MAX_CONTEXT_CHARS`, rate limits, and daily token quotas because they are product limits rather than provider configuration.

Remove `LLM_API_KEY`, `LLM_MODELS_JSON`, `LLM_DEFAULT_MODEL_ID`, `LLM_WEEKLY_BUDGET_USD`, `LLM_DAILY_BUDGET_USD`, direct endpoint/pricing fields, `LLM_MAX_OUTPUT_TOKENS`, provider retry settings, and direct-provider generation settings.

### Message schemas

Create strict Pydantic models in `server/core/pi_agent_messages.py` with `extra="forbid"` and `schema_version: Literal[1]`.

`PiAgentCommand` fields:

| Field | Type/constraint |
|---|---|
| `schema_version` | literal `1` |
| `job_id`, `tenant_id`, `project_id` | UUID |
| `provider` | literal `openai-codex` |
| `model` | non-empty string, maximum 200 chars |
| `thinking` | `off|minimal|low|medium|high|xhigh|max` |
| `prompt` | 1..20,000 chars |
| `prior_prompts` | maximum 5 strings, each maximum 20,000 chars |
| `active_file_id` | UUID or null; when present it must identify one command file |
| `files` | 1..20 `PiAgentSourceFile` records |
| `created_at` | timezone-aware datetime |
| `traceparent`, `tracestate` | optional bounded W3C trace strings |

`PiAgentSourceFile` contains `id`, `filename`, `content`, `updated_at`, and `sha256`. Reject duplicate IDs, duplicate normalized filenames, absolute paths, `..`, NUL, non-UTF-8 content, and hash mismatches in both API and worker.

`PiAgentResult` contains identity fields, `status: succeeded|failed`, `outcome: changed|no_changes|null`, provider/model, a bounded assistant summary, changed-file records, usage totals, error fields, retryability, and worker timestamps. It never contains OAuth data, raw Pi stderr, full event streams, or workspace paths.

Use JetStream deduplication IDs:

- Command: `pi-request:{job_id}`
- Result: `pi-result:{job_id}:{status}:{outcome-or-error-code}`
- Billing event: deterministic UUIDv5 derived from `pi-agent-billing:{job_id}`

The worker ACKs the request only after JetStream confirms result publication. A result consumer ACKs only after terminal DB state commits or after recognizing a duplicate terminal result.

Configure the stream with file storage, limits retention, a 24-hour maximum age, a 64 MiB maximum size, and discard-old behavior. NATS logging must not include payloads. This bounds how long prompts and source bundles remain in transport while leaving enough recovery time for an API outage.

### Job state transitions

```text
queued -> running -> succeeded
                 -> failed
queued ----------> failed (dispatch/configuration failure)
```

- The POST endpoint commits `queued`, publishes durably, marks `running`, then returns `202`.
- The result consumer may accept a result for `queued` or `running`; this closes the small publish/status-commit race.
- A terminal job is immutable. Duplicate results are ACKed without restaging files or republishing usage.
- Stale reconciliation uses `(timeout + ack_wait) * max_deliver + 60`, initially 1,200 seconds, so it cannot fail a valid redelivery attempt.

## 6. OAuth Storage and Operator Flow

### Helm values

Add a top-level value block with these semantics:

```yaml
piAgent:
  enabled: false
  image:
    repository: ghcr.io/d-b-w-gain/tertius-pi-agent
    tag: master
    pullPolicy: IfNotPresent
  auth:
    existingClaim: ""
    storage:
      enabled: true
      size: 64Mi
      storageClassName: ""
      retain: true
  runtimeClassName: gvisor
  resources:
    requests:
      cpu: 500m
      memory: 512Mi
    limits:
      cpu: "2"
      memory: 1Gi
```

`values-local.yaml` sets `storageClassName: local-path` and clears `runtimeClassName` where gVisor is unavailable. `existingClaim` takes precedence over chart-created storage. `retain: true` adds `helm.sh/resource-policy: keep`.

The PVC is rendered even while the worker is disabled so authentication can be provisioned before cutover. The API and UI never mount it. The worker mounts the entire claim at `/var/lib/pi-agent` read/write with UID/GID/fsGroup 1000.

### `scripts/pi-agent-auth.sh`

Implement `login`, `verify`, and `logout` subcommands with `--namespace`, `--release`, and optional `--claim` and `--image` flags. Without `--image`, resolve the exact repository/tag from `helm get values --all -o json`; document `kubectl`, `helm`, and `jq` as script prerequisites.

`login` must:

1. Resolve the chart-created or supplied claim and fail if it is missing. Accept `Pending` before first use because `local-path` uses `WaitForFirstConsumer`; reject any other non-Bound state.
2. Refuse to continue unless the Pi ScaledJob is absent, disabled, or explicitly paused, and refuse while any pod with `app.kubernetes.io/component=pi-agent-worker` is active.
3. Create a uniquely named, operator-only ephemeral pod from the pinned Pi agent image, then wait for the claim to become Bound and the pod to become Ready. This first consumer establishes the local PV's node affinity.
4. Apply the same chart release selector labels (`app.kubernetes.io/name` and `app.kubernetes.io/instance`) and `tertius.io/pi-agent-network=true` label as workers so only the matching release's Pi egress NetworkPolicy selects the login pod.
5. Use the same UID/GID/fsGroup, PVC mount, no service-account token, no host namespaces, RuntimeDefault seccomp, dropped capabilities, read-only root filesystem, and bounded `/tmp` and home volumes.
6. Attach a TTY and run Pi interactively with `PI_CODING_AGENT_DIR=/var/lib/pi-agent`, `--no-session`, `--no-tools`, and every resource-discovery `--no-*` flag.
7. Instruct the operator to run `/login`, select OpenAI Codex, and complete the browser/device flow.
8. After interactive Pi exits, run a no-tool canary that must return exactly `PI_AUTH_OK` using `openai-codex/gpt-5.5`.
9. Check only that `auth.json` exists and is mode `0600`; never print, base64, or copy its content.
10. Delete the pod through an EXIT trap.

`verify` repeats the no-tool canary without opening interactive login. `logout` requires `--confirm`, opens Pi against the same claim, and directs the operator through `/logout`; it must not delete the PVC.

### Credential lifecycle

- Pi refreshes OAuth in place on the shared claim.
- A provider-auth failure marks the user job failed with an operator-action message and increments an auth-failure metric.
- Re-authentication is performed only after active workers reach zero.
- Node/PV loss is recovered by creating/binding a replacement claim and logging in again. OAuth state is deliberately excluded from ordinary application backups.
- The retained PVC is deleted only through an explicit documented operator command after logout and workload disablement.

## 7. Worker Security Specification

Pi has no built-in sandbox. The Kubernetes pod is the primary process boundary, and the explicit extension is a second filesystem guard.

### Pod boundary

- Dedicated image; no API/UI process in the pod.
- `runAsNonRoot`, UID/GID/fsGroup 1000.
- `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, RuntimeDefault seccomp, all capabilities dropped.
- `automountServiceAccountToken: false`; no database, Keycloak, application Secret, Docker socket, hostPath, host PID/IPC/network, or host device mounts.
- Production uses the existing gVisor runtime class policy; local values may clear it explicitly.
- Writable mounts are only `/workspace`, `/tmp`, `/tmp/home`, and `/var/lib/pi-agent`.
- The workspace is a fresh `emptyDir` populated from validated database content, not a Git clone or user-controlled archive.

### Tool boundary

`server/pi/workspace-guard.ts` handles every `tool_call` before execution:

- Block unknown tools and `bash` even if a later pinned Pi release changes its defaults.
- Resolve each `path` against the canonical workspace root.
- Block absolute paths, traversal, NUL, empty paths, and any canonical target outside the workspace.
- Reject symlinks in every existing path component.
- Permit `read`, `grep`, `find`, and `ls` only within the workspace.
- Permit `edit` and `write` only for files from the immutable input manifest.
- Block creation, deletion, rename, and writes to directories.
- Catch guard-internal exceptions and return `{block: true, reason: "TERTIUS_GUARD_FAILURE"}`. The worker treats that stable blocked-tool sentinel as fatal and discards all files. Do not depend on `extension_error`: Pi 0.80.6 blocks a thrown `tool_call` handler but does not emit that RPC event for handler exceptions.

After Pi settles, the Python worker independently scans the workspace and rejects any added, removed, renamed, symlinked, non-regular, oversized, or non-UTF-8 file. Only content changes to manifest files become result records.

### Network boundary

Add `pi-agent-networkpolicy.yaml`, selecting both workers and login pods through `tertius.io/pi-agent-network=true`, with only:

- DNS TCP/UDP 53 to cluster DNS.
- NATS TCP 4222 to the release-local NATS pods/service.
- OTLP gRPC TCP 4317 to the release-local collector when observability is enabled.
- Public TCP 443 for the ChatGPT/OpenAI subscription backend, excluding private/link-local IPv4 and IPv6 ranges.

Standard Kubernetes NetworkPolicy cannot allow by DNS name, so the public-443 rule is intentionally broader than `chatgpt.com`. Record that limitation in `docs/harness/runtime-parity.md`. An egress proxy/FQDN policy is explicitly excluded from this implementation.

## 8. Usage and UI Contract

Pi reports token statistics and a list-price estimate, but a ChatGPT subscription is not metered to Tertius as API-dollar spend. Do not use Pi's `cost` field for quotas or UI budget claims.

- Keep per-minute request limits and tenant/user daily token quotas.
- Record Pi RPC session token totals in `LlmUsageRecord` with provider `openai-codex`, model `gpt-5.5`, and null provider request ID.
- Continue publishing the existing billing token event with `cost_usd=0.0`; it remains a usage event, not an assertion of zero provider value.
- Remove weekly/daily USD checks and cost calculations.
- Change `/llm-usage/models` to return one configured model without endpoint, API type, or pricing fields.
- Change `/llm-usage/today` to return token quota/use/remaining and last-edit fields only.
- Rename the visible `AiBudgetGauge` to `AiUsageGauge`, display `used / quota` tokens, and remove dollar labels/pricing copy from Generate Design.
- Keep `model_id` in the edit request for browser compatibility, but accept only blank or the configured model ID. Return `400 unsupported_model` for anything else.

## 9. File Change Map

### Create

| Path | Purpose |
|---|---|
| `server/core/llm_file_edit.py` | Provider-neutral edit types, context selection, estimates, and validation moved out of `llm_client.py`. |
| `server/core/pi_agent_messages.py` | Strict command/result schemas, size checks, message IDs. |
| `server/core/pi_agent_rpc.py` | Async RPC subprocess client, event/state limits, stats extraction, error classification. |
| `server/workflows/intus/pi_agent_job.py` | One-shot NATS worker and temporary workspace lifecycle. |
| `server/workflows/intus/pi_agent_result_consumer.py` | API-side result persistence, stale recovery, and idempotency. |
| `server/start-pi-agent-job.sh` | One-shot image entrypoint. |
| `server/pi/package.json` | Pinned Pi runtime/test package. |
| `server/pi/package-lock.json` | Reproducible Pi dependency graph. |
| `server/pi/workspace-guard.ts` | Explicit Pi tool-call guard. |
| `server/pi/workspace-guard.test.ts` | Node path/tool security tests. |
| `server/tests/test_pi_agent_messages.py` | Schema/size/message-ID tests. |
| `server/tests/test_pi_agent_rpc.py` | Fake-process RPC protocol and limit tests. |
| `server/tests/test_pi_agent_job.py` | Workspace, command handling, result publication, ACK tests. |
| `server/tests/test_pi_agent_result_consumer.py` | DB staging/idempotency/failure tests. |
| `server/tests/test_llm_file_edit_domain.py` | Provider-neutral context/estimate/result tests. |
| `infra/charts/tertius/templates/pi-agent-auth-pvc.yaml` | Retained RWO claim. |
| `infra/charts/tertius/templates/pi-agent-worker.yaml` | KEDA one-shot worker. |
| `infra/charts/tertius/templates/pi-agent-networkpolicy.yaml` | Worker egress policy. |
| `scripts/pi-agent-auth.sh` | Login/verify/logout helper. |
| `docs/operations/pi-agent-auth.md` | Operator runbook, node-loss recovery, rotation, removal. |
| `docs/harness/queries/pi-agent.promql` | Bounded worker health/latency/failure queries. |
| `ui/src/workflows/generate/AiUsageGauge.tsx` | Token-only usage gauge. |
| `ui/src/workflows/generate/AiUsageGauge.test.tsx` | Token-only gauge tests. |

### Modify

| Path group | Required change |
|---|---|
| `server/core/config.py`, `server/.env.example`, `server/tests/test_config.py` | Replace direct-provider and USD settings with the fixed Pi settings. |
| `server/core/nats_client.py`, `server/tests/test_nats_client.py` | Add Pi stream, durable request/result consumers, pull helpers, and update behavior. |
| `server/core/repositories.py` | Add idempotent Pi result lookup/terminal transition helpers without weakening tenant/project filters. |
| `server/core/llm_usage.py`, `server/core/usage_messages.py`, `server/core/billing_messages.py` | Token-only limits/models and Pi provider recording. |
| `server/workflows/intus/intus_server.py` | Publish commands, remove background provider loop and legacy build-script route, preserve HTTP job contract. |
| `server/workflows/intus/usage_server.py`, `server/main.py` | Serve fixed model/token contract and run the Pi result consumer in lifespan. |
| `server/tests/test_llm_file_edit.py`, `server/tests/test_llm_usage.py`, `server/tests/test_usage_endpoints.py`, `server/tests/test_billing_messages.py` | Replace provider mocks/cost assertions with queue/result/token behavior. |
| `pyproject.toml`, `uv.lock` | Remove the direct `openai` dependency and regenerate the lock. |
| `Dockerfile.api` | Add named `api` and `pi-agent` targets; copy Node/Pi only into the latter. |
| `.github/workflows/tests.yml`, `.github/workflows/images.yml`, `.github/workflows/chart-tests.yml` | Test Pi package and build/push the third image; include new paths in chart triggers. |
| `infra/charts/tertius/values.yaml`, `values-local.yaml`, `_helpers.tpl`, `configmap.yaml`, `api.yaml`, `secrets.yaml`, `README.md` | Add Pi values/resources, remove API-key injection, and keep system prompt worker-only. |
| `infra/clusters/production/flux-system/image-repositories.yaml`, `image-policies.yaml` | Add `tertius-pi-agent`; add Flux setters to chart values. |
| `docker-compose.yml`, `docker-compose.parity.yml` | Add serial Pi worker and named auth volume; remove direct-provider env. |
| `scripts/test-deployment-config.sh`, `scripts/check-runtime-parity.sh`, `scripts/test-k3s-deployment.sh`, `scripts/harness-k3s.sh`, `scripts/harness-compose.sh`, `scripts/smoke-live-flow.sh`, `ci/k3s-images.txt` | Render/security checks, image import, auth preflight, and full live-flow coverage. |
| `scripts/local-k3s-start-wsl.sh`, `scripts/local-k3s-patch-api.ps1`, `scripts/README-local-k3s.md` | Remove API-key sync and document/patch the Pi worker image path. |
| `docs/configuration-and-secrets.md`, `docs/harness/local-harness.md`, `docs/harness/browser-validation.md`, `docs/harness/runtime-parity.md`, `docs/harness/observability-validation.md`, `docs/observability/alerts.md`, `docs/observability/dashboards.md` | Replace key-based operations with OAuth/PVC and worker validation. |
| `ui/src/App.tsx`, `ui/src/App.test.tsx`, `ui/src/workflows/generate/GenerateDesignWindow.tsx`, its tests, `ui/src/workflows/shared/projectStorage.ts`, its tests | Token-only model/usage types and renamed gauge. |

### Delete

| Path | Reason |
|---|---|
| `server/core/llm_client.py` | No direct provider execution remains after neutral helpers move. |
| `server/tests/test_llm_client.py` | Replaced by domain, RPC, worker, and consumer tests. |
| `server/tests/test_build_script_generation.py` | The unused direct synchronous route is removed. |
| `scripts/set-k3s-llm-api-key.sh` | API keys are no longer provisioned. |
| `scripts/local-k3s-sync-llm-env-wsl.sh` | Direct model/key/budget synchronization is obsolete. |
| `ui/src/workflows/generate/AiBudgetGauge.tsx` | Replaced by token-only `AiUsageGauge`. |
| `ui/src/workflows/generate/AiBudgetGauge.test.tsx` | Replaced with token-only tests. |

## 10. Implementation Tasks

### Task 1: Establish provider-neutral domain and Pi contracts

**Files:** `server/core/llm_file_edit.py`, `server/core/pi_agent_messages.py`, `server/core/config.py`, `server/.env.example`, `server/tests/test_llm_file_edit_domain.py`, `server/tests/test_pi_agent_messages.py`, `server/tests/test_config.py`.

- [x] Write failing tests U-001 through U-008 from the test table.
- [x] Move only provider-neutral request/result/context/token-estimate helpers from `llm_client.py` into `llm_file_edit.py`.
- [x] Implement strict command/result schemas, cross-field validators, deterministic message IDs, and byte-size enforcement.
- [x] Add the fixed Pi settings and remove direct provider/model-price/USD settings.
- [x] Run:

```bash
rtk uv run pytest server/tests/test_llm_file_edit_domain.py server/tests/test_pi_agent_messages.py server/tests/test_config.py -q
```

- [x] Expected: all focused tests pass; `.env.example` exactly matches Settings fields.
- [ ] Commit: `feat(pi-agent): define worker contracts and settings`

### Task 2: Add the dedicated JetStream transport

**Files:** `server/core/nats_client.py`, `server/tests/test_nats_client.py`, `server/tests/test_pi_agent_messages.py`.

- [x] Write failing tests U-009 through U-013.
- [x] Add `ensure_pi_agent_stream`, request/result pull subscriptions, consumer update behavior, and stream max-message-size reconciliation.
- [x] Configure request consumer ACK wait/max delivery and a separate durable result consumer.
- [x] Preserve compile and billing stream behavior unchanged.
- [x] Run:

```bash
rtk uv run pytest server/tests/test_nats_client.py server/tests/test_pi_agent_messages.py -q
```

- [ ] Commit: `feat(pi-agent): add durable command and result transport`

### Task 3: Build and test the Pi workspace guard

**Files:** `server/pi/package.json`, `server/pi/package-lock.json`, `server/pi/workspace-guard.ts`, `server/pi/workspace-guard.test.ts`, `.github/workflows/tests.yml`.

- [x] Add the exact `0.80.6` dependency and Node 24 test script.
- [x] Write failing tests U-014 through U-023 before implementing the guard.
- [x] Export pure canonical-path/manifest helpers for unit tests and register a fail-closed `tool_call` listener.
- [x] Ensure `--no-extensions -e /opt/tertius-pi/workspace-guard.ts` is the only extension load path.
- [x] Add a `pi-agent` CI job that runs the hardened install and `npm test` from `server/pi`.
- [x] Run:

```bash
cd server/pi
npm ci
npm test
```

- [ ] Commit: `feat(pi-agent): enforce workspace-only file tools`

### Task 4: Implement the bounded Pi RPC client and one-shot worker

**Files:** `server/core/pi_agent_rpc.py`, `server/workflows/intus/pi_agent_job.py`, `server/start-pi-agent-job.sh`, `server/tests/test_pi_agent_rpc.py`, `server/tests/test_pi_agent_job.py`.

- [x] Write fake-executable tests U-024 through U-036; fixtures must never call a real provider.
- [x] Implement strict LF-delimited JSONL parsing, response correlation, startup `get_state` provider/model assertion, `agent_settled`, explicit Pi token-stat mapping, abort, timeout, and process cleanup.
- [x] Hydrate a fresh manifest workspace with `0700` directories and `0600` regular files.
- [x] Add independent post-run file-set/hash/content validation.
- [x] Publish a bounded success/failure result and ACK only after result publish confirmation.
- [x] Send `msg.in_progress()` every 30 seconds; NAK on transient result-publish failure.
- [x] Add bounded telemetry without prompts, source, raw IDs, or raw stderr.
- [x] Run:

```bash
rtk uv run pytest server/tests/test_pi_agent_rpc.py server/tests/test_pi_agent_job.py -q
```

- [ ] Commit: `feat(pi-agent): run isolated one-shot edit workers`

### Task 5: Dispatch jobs and consume results in FastAPI

**Files:** `server/workflows/intus/intus_server.py`, `server/workflows/intus/pi_agent_result_consumer.py`, `server/core/repositories.py`, `server/main.py`, `server/tests/test_llm_file_edit.py`, `server/tests/test_pi_agent_result_consumer.py`.

- [x] Write failing tests U-037 through U-046 and I-001 through I-006.
- [x] Refactor submit-time validation/context selection/quota preflight into provider-neutral helpers.
- [x] Replace `BackgroundTasks` provider execution with durable `PiAgentCommand` publication.
- [x] Keep the existing `202 {success, job_id, status}` response and project exclusivity behavior.
- [x] Implement result identity checks, optimistic version revalidation, stage/update transaction, deterministic billing event, usage row, terminal job result, duplicate handling, and stale reconciliation.
- [x] Start/stop the result consumer in the API lifespan beside the compile result consumer.
- [x] Ensure Pi command/result contracts contain no database or auth-BFF credentials; rendered pod verification remains Task 8.
- [x] Run:

```bash
rtk uv run pytest server/tests/test_llm_file_edit.py server/tests/test_pi_agent_result_consumer.py -q
```

- [ ] Commit: `feat(pi-agent): route ai edits through worker queue`

### Task 6: Remove direct provider and dollar-budget behavior

**Files:** `server/core/llm_client.py` (delete), `server/tests/test_llm_client.py` (delete), `server/tests/test_build_script_generation.py` (delete), `server/workflows/intus/intus_server.py`, `server/core/llm_usage.py`, `server/core/usage_messages.py`, `server/core/billing_messages.py`, `server/workflows/intus/usage_server.py`, `pyproject.toml`, `uv.lock`, UI usage/model files from the file map.

- [x] Write/update tests U-047 through U-054 and I-007 through I-009.
- [x] Remove the synchronous build-script route and all direct OpenAI/Anthropic client construction/parsing/retry code.
- [x] Remove the `openai` Python dependency and regenerate `uv.lock` with `rtk uv lock`.
- [x] Return one fixed Pi model; reject unsupported model IDs.
- [x] Remove USD quota calculations and emit `cost_usd=0.0` only where the existing billing schema requires it.
- [x] Rename the gauge and update Generate Design to display token use without model pricing.
- [x] Run:

```bash
rtk uv run pytest server/tests/test_llm_usage.py server/tests/test_usage_endpoints.py server/tests/test_billing_messages.py server/tests/test_llm_file_edit.py -q
cd ui
npm test -- AiUsageGauge.test.tsx GenerateDesignWindow.test.tsx projectStorage.test.ts
npm run typecheck
```

- [x] Run the static removal check:

```bash
rtk rg -n "AsyncOpenAI|OPENAI_API_KEY|LLM_API_KEY|anthropic-messages|openai-chat-completions|weekly_budget_usd|input_price_per_million" server ui/src pyproject.toml
```

- [x] Expected: no active-code matches; historical plan docs are excluded intentionally.
- [ ] Commit: `refactor(llm): remove direct provider api path`

### Task 7: Build and publish a dedicated Pi image

**Files:** `Dockerfile.api`, `.github/workflows/images.yml`, `.github/workflows/tests.yml`, `infra/clusters/production/flux-system/image-repositories.yaml`, `infra/clusters/production/flux-system/image-policies.yaml`, `infra/charts/tertius/values.yaml`, `ci/k3s-images.txt`.

- [x] Refactor `Dockerfile.api` into a shared Python app base plus named `api` and `pi-agent` targets.
- [x] Build Pi dependencies in a `node:24-bookworm-slim` stage and copy only Node, locked `node_modules`, guard source, and worker entrypoint into `pi-agent`.
- [x] Keep Node and OAuth tooling out of the final `api` target.
- [x] Build/push configuration for `ghcr.io/d-b-w-gain/tertius-pi-agent` with the same immutable tags as API/UI.
- [x] Set `target: api` and `target: pi-agent` explicitly in the two image workflow build steps.
- [x] Add Flux repository/policy and setters for Pi image name/tag.
- [x] Add image-content checks: API has no `pi`; Pi image runs `pi --version`, starts as UID 1000, and has no app DB secret.
- [x] Run:

```bash
docker build --target api -t tertius-api:pi-plan -f Dockerfile.api .
docker build --target pi-agent -t tertius-pi-agent:pi-plan -f Dockerfile.api .
docker run --rm tertius-pi-agent:pi-plan pi --version
docker run --rm tertius-api:pi-plan sh -c '! command -v pi'
```

- [ ] Commit: `build(pi-agent): publish dedicated worker image`

### Task 8: Add Helm PVC, ScaledJob, and network isolation

**Files:** `infra/charts/tertius/values.yaml`, `values-local.yaml`, `_helpers.tpl`, `templates/pi-agent-auth-pvc.yaml`, `templates/pi-agent-worker.yaml`, `templates/pi-agent-networkpolicy.yaml`, `templates/configmap.yaml`, `templates/api.yaml`, `templates/secrets.yaml`, `infra/charts/tertius/README.md`, `scripts/test-deployment-config.sh`.

- [x] Add render tests I-010 through I-017 before templates.
- [x] Render a retained RWO PVC independently from worker enablement, with `existingClaim` support.
- [x] Mirror compile-worker pod hardening and KEDA NATS scaler behavior; set `maxReplicaCount: 1`.
- [x] Mount the whole auth claim and bounded workspace/tmp/home `emptyDir` volumes.
- [x] Remove API `LLM_API_KEY` and provider config injection; mount optional `PI_AGENT_SYSTEM_PROMPT` only in the worker.
- [x] Add DNS/NATS/OTLP/public-443 egress rules and no ingress.
- [x] Render the Pi egress policy while auth storage is enabled even when the ScaledJob is disabled, so first-time login pods are selected and restricted.
- [x] Assert no DB, Keycloak, API session, provider-key, service-account-token, host, or privileged access in rendered worker YAML.
- [x] Run:

```bash
rtk bash scripts/test-deployment-config.sh
helm lint infra/charts/tertius
helm template tertius infra/charts/tertius -f infra/charts/tertius/values-local.yaml >/tmp/tertius-pi-render.yaml
```

- [ ] Commit: `feat(helm): deploy isolated pi agent workers`

### Task 9: Add OAuth operations and remove API-key operations

**Files:** `scripts/pi-agent-auth.sh`, `docs/operations/pi-agent-auth.md`, `scripts/set-k3s-llm-api-key.sh` (delete), `scripts/local-k3s-sync-llm-env-wsl.sh` (delete), `scripts/local-k3s-start-wsl.sh`, `scripts/local-k3s-patch-api.ps1`, `scripts/README-local-k3s.md`, `docs/configuration-and-secrets.md`.

- [x] Add shell tests/static assertions I-018 through I-021 to `scripts/test-deployment-config.sh`.
- [x] Implement login/verify/logout exactly as Section 6 specifies, including cleanup traps and active-worker refusal.
- [x] Document provisioning, refresh failure, node-loss recovery, PVC replacement, logout, and explicit PVC deletion.
- [x] Remove API-key scripts and all callers; local start must print the login/verify command when Pi is enabled but unverified.
- [x] Ensure no command displays or copies `auth.json`.
- [x] Run `shellcheck` on changed shell scripts when installed; otherwise run `bash -n` and report the missing optional tool.
- [ ] Commit: `ops(pi-agent): manage subscription oauth on retained pvc`

### Task 10: Restore Compose and harness parity

**Files:** `docker-compose.yml`, `docker-compose.parity.yml`, `scripts/check-runtime-parity.sh`, `scripts/test-k3s-deployment.sh`, `scripts/harness-k3s.sh`, `scripts/harness-compose.sh`, `scripts/smoke-live-flow.sh`, `ci/k3s-images.txt`, harness docs from the file map.

- [x] Add a serial `pi-agent-worker` service built from the `pi-agent` target and a named `pi-agent-auth` volume.
- [x] Document `docker compose run --rm --entrypoint pi pi-agent-worker` as the Compose login path; do not bind-mount host `~/.pi`.
- [x] Add k3s image import, Pi values, PVC preservation, and auth preflight to the canonical harness.
- [x] Make full `live-flow` fail early with a precise auth/PVC error when AI edit is in scope; do not silently switch to compile-only.
- [x] Preserve the retained auth PVC on ordinary `down`; delete it only under explicit `delete-data` confirmation.
- [x] Run:

```bash
rtk bash scripts/check-runtime-parity.sh
rtk bash scripts/test-k3s-deployment.sh --help
docker compose -f docker-compose.yml -f docker-compose.parity.yml config >/tmp/tertius-compose-pi.yaml
```

- [ ] Commit: `test(pi-agent): cover runtime and harness parity`

### Task 11: Add safe observability

**Files:** `server/workflows/intus/pi_agent_job.py`, `server/workflows/intus/pi_agent_result_consumer.py`, `docs/harness/queries/pi-agent.promql`, `docs/harness/queries/traces.md`, `docs/harness/observability-validation.md`, `docs/observability/alerts.md`, `docs/observability/dashboards.md`.

- [x] Emit bounded counters/histograms for queued, started, terminal status, failure category, duration, turns, tool calls, and token classes.
- [x] Use only bounded labels: operation, provider, model, status, failure category, retryable.
- [x] Propagate W3C trace context through command/result messages.
- [x] Add alerts for auth failures, no result consumer, stale jobs, repeated worker loss, and queue lag.
- [x] Add static tests that reject prompts, source, filenames, auth tokens, and raw tenant/user/project/job IDs in log/metric attribute calls.
- [ ] Commit: `obs(pi-agent): trace and alert worker execution`

### Task 12: Execute full verification and staged cutover

- [x] Run every command in Section 14.
- [x] Deploy plumbing with `piAgent.enabled=false` and auth PVC enabled.
- [x] Run `scripts/pi-agent-auth.sh login`, then `verify`.
- [x] Enable the worker with `maxReplicaCount: 1` and deploy the API/UI cutover.
- [x] Run one no-change canary and one single-file edit canary.
- [x] Delete the login pod and a worker pod; verify the next edit succeeds from the same PVC.
- [x] Run the full authenticated k3s `live-flow`, not compile-only.
- [ ] **Manual post-deployment exit gate:** observe the first 20 AI edit jobs or 24 hours, whichever is longer, with zero auth-lock, stale-job, or duplicate-stage incidents. This soak is intentionally completed by the operator after the automated implementation and canary work.
- [ ] Keep the old external API-key Secret unmounted during that observation window, then delete it after the exit criteria pass.
- [ ] Commit any documentation corrections discovered during the real run before merge.

## 11. Anti-Patterns (Do Not)

| Do not | Do instead | Why |
|---|---|---|
| Copy `auth.json` or clone the login PVC per worker | Mount one mutable RWO claim on same-node pods | OAuth refresh rotates state; copies diverge. |
| Put OAuth JSON into a Kubernetes Secret | Keep Pi's mutable directory on a retained filesystem claim | Pi must rewrite it and coordinate with a sibling lock. |
| Mount only `auth.json` via `subPath` | Mount `/var/lib/pi-agent` as a directory | Lock/settings writes occur beside the file. |
| Mount OAuth state into FastAPI | Mount it only into login and worker pods | Reduces credential blast radius. |
| Run Pi inside the API process/pod | Use one-shot NATS workers | Isolates untrusted tool execution and provider latency. |
| Enable Pi's `bash` tool | Allow only guarded file tools | Shell access bypasses manifest and auth-path controls. |
| Trust prompt instructions as a security boundary | Enforce canonical paths in an extension and post-run scanner | Pi explicitly has no built-in sandbox. |
| Load project/global Pi resources automatically | Use all `--no-*` discovery flags plus one explicit extension | Prevents workspace files from changing agent behavior. |
| Let worker pods access the DB or Kubernetes API | Publish results to NATS; API persists them | Maintains the established DB-free worker boundary. |
| Treat Pi's list-price estimate as subscription spend | Enforce/report tokens only | Subscription usage is not an API invoice. |
| Start with multiple workers because RWO permits it | Start at one and validate auth locking | Avoids account concurrency and lock-contention surprises. |
| Log Pi JSON events or stderr verbatim | Parse, classify, and emit bounded categories | Events can contain prompts, source, and provider details. |
| ACK before result publication | Publish/flush result, then ACK | Prevents silent job loss. |
| Persist worker output without revalidation | Recheck tenant/job/file identity and versions in API | Worker results are untrusted input. |
| Delete the auth PVC during normal Helm uninstall/harness down | Retain by default; explicit logout/delete only | Pod/release lifecycle must not erase OAuth state. |

## 12. Test Case Specifications

### Unit tests

| ID | Component | Input | Expected result | Edge case |
|---|---|---|---|---|
| U-001 | Domain filename validation | nested valid Python path | accepted | absolute, `..`, NUL rejected |
| U-002 | Context selection | 20 files, active file, 80k cap | stable selected order/cap | active file always retained |
| U-003 | Token estimate | prompt, prior prompts, selected source | deterministic positive reservation | empty prior prompts |
| U-004 | Config | no env | fixed Pi defaults | invalid thinking/limits rejected |
| U-005 | Config cleanup | `.env.example` keys | exact Settings parity | removed direct keys absent |
| U-006 | Command model | valid identity/files | round trip | extra fields rejected |
| U-007 | Command model | duplicate ID/name, bad hash | validation error | normalized name collision |
| U-008 | Result model | changed/no-change/failure | valid cross-field states | changed without files rejected |
| U-009 | NATS setup | absent stream | creates two subjects/two consumers | max size uses larger bound |
| U-010 | NATS setup | stale stream config | updates safely | preserves messages |
| U-011 | Request consumer | settings | ACK wait 90/max deliver 2 | durable name stable |
| U-012 | Result consumer config | settings | explicit-ack durable filter | independent from request durable |
| U-013 | Message IDs | same job/result | deterministic IDs | error code changes result ID |
| U-014 | Guard read | manifest path | allowed | relative nested path |
| U-015 | Guard read | `/var/lib/pi-agent/auth.json` | blocked | absolute path |
| U-016 | Guard read | `../../var/lib/pi-agent/auth.json` | blocked | normalized traversal |
| U-017 | Guard read | in-workspace symlink outward | blocked | symlinked parent directory |
| U-018 | Guard write | existing manifest file | allowed | canonical path match |
| U-019 | Guard write | new file | blocked | missing parent/file |
| U-020 | Guard tool | `bash` or unknown tool | blocked | CLI allowlist regression |
| U-021 | Guard path | NUL/empty/directory | blocked | guard exception fails closed |
| U-022 | Guard discovery | explicit extension invocation | only guard loads | project `.pi` ignored |
| U-023 | Guard error | thrown canonicalizer | tool blocked | no extension UI wait |
| U-024 | RPC startup | fake Pi ready | prompt sent only on stdin | prompt absent from argv/log |
| U-025 | RPC lifecycle | settled event | stats requested and parsed | cache token classes retained |
| U-026 | RPC protocol | CRLF line | trailing CR accepted | Unicode separators not split |
| U-027 | RPC protocol | malformed JSON | classified worker failure | bounded diagnostic |
| U-028 | RPC correlation | interleaved events/responses | correct response matched | events have no ID |
| U-029 | RPC turn limit | 13th turn | abort then failure result | exact 12 allowed |
| U-030 | RPC tool limit | 49th call | abort then failure result | exact 48 allowed |
| U-031 | RPC timeout | no settled event | terminate/kill sequence | cleanup within 10 seconds |
| U-032 | RPC auth failure | provider auth error event | `provider_auth` non-retryable | raw token absent |
| U-033 | RPC rate limit | final retry failure | `provider_rate_limit` retryable | no app sleep loop |
| U-034 | RPC guard failure | blocked tool result with `TERTIUS_GUARD_FAILURE` | `tool_guard_failure` | no files returned |
| U-035 | RPC stderr | secret-like/raw text | not logged or returned | diagnostic length bounded |
| U-036 | RPC no change | no hash changes | `no_changes` success | assistant text empty |
| U-037 | Worker hydrate | valid command | exact files/modes/hash | nested directories |
| U-038 | Worker hydrate | duplicate/escape path | failure before Pi spawn | no result source leak |
| U-039 | Worker scan | one existing file edited | one changed result | unchanged files omitted |
| U-040 | Worker scan | add/delete/rename/symlink | invalid workspace result | all changes rejected |
| U-041 | Worker ACK | result publish succeeds | request ACK | flush confirmed |
| U-042 | Worker ACK | result publish transient failure | request NAK | no ACK |
| U-043 | Worker heartbeat | long fake run | in-progress every 30s | heartbeat task cancelled |
| U-044 | Result persistence | valid changed result | staged snapshot/job success | version checked again |
| U-045 | Result persistence | terminal duplicate | ACK/no writes | billing not repeated |
| U-046 | Result persistence | identity mismatch | reject/ACK invalid result | tenant/project mismatch |
| U-047 | Usage limit | token quota exceeded | reject before publish | no dollar calculation |
| U-048 | Usage record | Pi stats | provider/model/tokens stored | request ID null |
| U-049 | Models API | configured Pi model | one model, no price/endpoint | unsupported model rejected |
| U-050 | Today API | usage rows | token-only response | no cost fields |
| U-051 | Billing event | Pi success | token event, cost 0 | deterministic event ID |
| U-052 | UI gauge | server summary | token used/quota display | local fallback |
| U-053 | Generate Design | model response | fixed model, no pricing | empty model response error |
| U-054 | Legacy route | POST build-script generate | 404 | no provider call/import |

### Integration tests

| ID | Flow | Setup | Verification | Teardown |
|---|---|---|---|---|
| I-001 | Submit to JetStream | authenticated API, fake NATS | job commits and command matches selected files | purge stream/DB |
| I-002 | Dispatch failure | NATS publish error | job becomes failed; API returns retryable 503 | delete job |
| I-003 | Worker success | real NATS, fake Pi RPC | result published, command ACKed | purge stream |
| I-004 | Worker abrupt loss | kill first worker before ACK | command redelivers once to replacement | delete jobs/pods |
| I-005 | Result apply | persisted job/files plus success result | snapshot/files/usage/job commit atomically | rollback fixtures |
| I-006 | Concurrent edit conflict | mutate file after command | result fails `file_conflict`, no stage | restore file |
| I-007 | UI polling | mocked submit/running/success | existing conversation flow completes | test cleanup |
| I-008 | Usage refresh | completed Pi result | token gauge refresh event updates display | localStorage cleanup |
| I-009 | Project exclusivity | active Pi job then second submit | second submit rejected by existing contract | finish first job |
| I-010 | Helm PVC default | worker off, storage on | retained RWO PVC renders | temp render |
| I-011 | Helm existing claim | `existingClaim=external` | no PVC; worker mounts external | temp render |
| I-012 | Helm worker | enabled local values | KEDA ScaledJob max 1 and NATS scaler | temp render |
| I-013 | Helm hardening | enabled production values | gVisor, non-root, RO root, no token/caps | temp render |
| I-014 | Helm secrets | all app secrets enabled | OAuth/API key absent; prompt worker-only | temp render |
| I-015 | Helm volumes | rendered worker | whole auth dir plus bounded emptyDirs | temp render |
| I-016 | Helm network | rendered policy | only DNS/NATS/OTLP/public 443 egress | temp render |
| I-017 | Helm disabled KEDA | Pi enabled, `keda.enabled=false` | render fails with worker precondition error | temp render |
| I-018 | Login cleanup | mocked kubectl success | pod deleted on normal exit | no pod remains |
| I-019 | Login interruption | send signal | trap deletes pod | no pod remains |
| I-020 | Login active worker | worker pod present | script refuses before creating login pod | delete fixture pod |
| I-021 | Login output safety | command trace capture | no auth content/base64/cat | delete fixture files |

### End-to-end tests

| ID | Flow | Required result |
|---|---|---|
| E-001 | k3s login and verify | OAuth canary succeeds; `auth.json` remains on Bound RWO claim after login pod deletion. |
| E-002 | Authenticated no-change edit | HTTP job reaches `succeeded/no_changes`; token usage is recorded. |
| E-003 | Authenticated file edit | Pi edits an existing selected file; API stages it; downstream compile/live UI succeeds. |
| E-004 | Worker pod deletion | in-flight command redelivers no more than once and reaches one terminal DB result. |
| E-005 | PVC persistence | delete/recreate worker and verify next edit without login. |
| E-006 | Tool escape canary | adversarial prompt asks for `/var/lib/pi-agent/auth.json`; guard blocks it and no credential appears in output/logs. |
| E-007 | Full harness | `scripts/harness-k3s.sh live-flow` passes with AI edit enabled; compile-only mode is not used. |
| E-008 | Telemetry safety | Pi metrics/traces exist and contain no prompts, source, filenames, OAuth data, or raw IDs. |

## 13. Error Handling Matrix

| Failure | Detection | Job/API behavior | Retry | Logging/alerting |
|---|---|---|---|---|
| Pi disabled | `PI_AGENT_ENABLED=false` at submit | `503 pi_agent_disabled`, no command | user may retry after enable | WARN counter, no alert during planned disable |
| PVC missing or still Pending after a consumer is scheduled | worker/login startup or script timeout | worker pod stays Pending; an accepted job reaches stale `worker_lost` if deployment is not repaired | operator action | alert on pending worker/PVC |
| OAuth missing | Pi auth classification | failed `provider_auth_required` with operator-action message | false | ERROR category only; immediate alert |
| OAuth revoked/refresh failed | Pi final provider error | failed `provider_auth` | false until re-login | ERROR and auth alert |
| Normal auth lock contention | Pi startup lock error | worker NAKs command without provider call | one redelivery | WARN; alert only after repeated failures |
| Malformed/stale lock path | classified lock filesystem error | failed `auth_storage_invalid`; do not delete automatically | false | ERROR with runbook link |
| Provider rate limit | Pi `auto_retry_end success=false` | failed `provider_rate_limit` | true for user resubmit; no hidden app retry | WARN rate-limit metric |
| Provider 5xx/network timeout | Pi final error or worker timeout | failed `provider_unavailable`/`provider_timeout` | true | WARN/ERROR bounded category |
| Max turns/tool calls | wrapper counter | abort; failed `agent_limit_exceeded` | true after prompt adjustment | WARN with bounded limit name |
| Guard blocks a requested path | tool result plus settled run | Pi may recover; final scan decides success | none | count blocked tools, no path label |
| Guard extension fails internally | blocked tool result with `TERTIUS_GUARD_FAILURE` | fail `tool_guard_failure`; discard all files | false pending code fix | ERROR and alert |
| Unexpected workspace mutation | post-run scanner | fail `invalid_workspace_change`; discard all files | false | ERROR bounded mutation category |
| Pi malformed JSONL/process crash | parser/exit status | publish failed result when possible | abrupt loss uses one NATS redelivery | ERROR, worker-loss alert threshold |
| Worker OOM/node loss | command remains unacked | JetStream redelivers after heartbeat expires | one automatic redelivery | K8s/worker-loss alert |
| NATS unavailable at submit | publish error | job marked failed; HTTP retryable 503 | client resubmit | ERROR NATS category |
| Result publish unavailable | worker publish/flush error | NAK command; do not ACK | JetStream redelivery | ERROR NATS category |
| Invalid/oversize command | worker schema/size check | publish sanitized failure when identity parses; terminally ACK poison message | no | ERROR schema version/category |
| Invalid/oversize result | API consumer check | ACK poison result; stale watchdog later fails job | no | ERROR and alert |
| Unknown/terminal job result | DB lookup/status | ACK and no mutation | no | INFO/WARN counter |
| Tenant/project identity mismatch | result validation | ACK invalid result; no mutation | no | ERROR security counter |
| File changed after dispatch | optimistic version check | failed `file_conflict`, no partial write | false; UI reload/resubmit | INFO bounded conflict metric |
| Billing publish fails | deterministic event publish | rollback DB and NAK result | consumer retry | ERROR billing category |
| DB commit fails after billing publish | DB exception | NAK result; repeated billing message dedupes | consumer retry | ERROR DB category |
| No result after both deliveries | 1,200-second stale reconciliation | failed `worker_lost`, retryable | user resubmit | stale-job alert |

User-facing errors remain concise and never include provider response bodies, OAuth details, NATS subjects, local paths, or stack traces.

## 14. Verification Commands

Run from repository root unless a command changes directory:

```bash
rtk uv lock --check
rtk uv run mypy
rtk uv run pytest

cd server/pi
npm ci
npm test
cd ../..

cd ui
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
cd ..

rtk bash scripts/test-deployment-config.sh
rtk bash scripts/check-runtime-parity.sh
helm lint infra/charts/tertius

docker build --target api -t tertius-api:pi-verification -f Dockerfile.api .
docker build --target pi-agent -t tertius-pi-agent:pi-verification -f Dockerfile.api .
docker run --rm tertius-pi-agent:pi-verification pi --version
docker compose -f docker-compose.yml -f docker-compose.parity.yml config >/tmp/tertius-compose-pi.yaml

rtk rg -n "AsyncOpenAI|OPENAI_API_KEY|LLM_API_KEY|anthropic-messages|openai-chat-completions" \
  server ui/src infra/charts docker-compose.yml docker-compose.parity.yml scripts pyproject.toml
```

The final `rg` must have no active-runtime matches. Historical documents under `docs/superpowers/plans/` are intentionally not scanned.

Canonical live validation:

```bash
KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
KEDA_ENABLED=true scripts/harness-k3s.sh up

scripts/pi-agent-auth.sh login \
  --namespace tertius \
  --release tertius-live-flow-smoke

scripts/pi-agent-auth.sh verify \
  --namespace tertius \
  --release tertius-live-flow-smoke

KUBECONFIG=/home/johnson/.kube/config \
NAMESPACE=tertius RELEASE_NAME=tertius-live-flow-smoke \
scripts/harness-k3s.sh live-flow
```

Do not set `LIVE_FLOW_COMPILE_ONLY=true` for this change.

## 15. Rollout and Rollback

### Rollout

1. Merge/publish API, UI, and Pi images with the chart defaulting the worker off and the retained PVC on.
2. Reconcile Flux and confirm the PVC exists. It may remain Pending until the login pod triggers `WaitForFirstConsumer` binding.
3. Run operator login and no-tool verification, then confirm the PVC is Bound on the intended node.
4. Confirm account-use approval is recorded by the deployment owner.
5. Enable the Pi result consumer and worker at `maxReplicaCount: 1` in one cutover release.
6. Run no-change, one-file, adversarial-path, pod-restart, and full live-flow tests.
7. Monitor the first 20 jobs or 24 hours, whichever is longer.
8. Remove the old unmounted external API-key Secret only after the observation gate passes.

### Rollback

1. Set `piAgent.enabled=false` and reconcile; leave the auth PVC retained.
2. Roll API/UI back together to the previous image tags.
3. Reattach the still-retained old API-key Secret only for the previous release.
4. Confirm existing jobs are reconciled terminally and no Pi worker pods remain.
5. Do not delete/logout the Pi claim during incident rollback; preserve it for diagnosis and retry.

No database downgrade is required because this plan adds no schema migration.

## 16. Acceptance Criteria

- [x] API and UI pods contain no OpenAI/Anthropic API key or Pi OAuth mount.
- [x] Active source contains no direct provider SDK call path.
- [x] Existing AI edit submit/status/history UI behavior remains functional.
- [x] All model execution occurs in the dedicated Pi worker image.
- [x] Worker has no bash, DB credentials, service-account token, host access, or unrestricted filesystem tools.
- [x] Auth state uses one retained RWO claim; no credential copies exist.
- [x] Login, verify, logout, node-loss recovery, and PVC deletion are documented and tested.
- [x] Token quotas and usage reporting work without dollar-budget claims.
- [x] Command/result delivery and DB writes are idempotent under duplicate/redelivery tests.
- [x] Full Python, Node, UI, chart, parity, image, and authenticated k3s live-flow gates pass.
- [x] Metrics/traces contain bounded operational fields only.
- [x] Rollback has been rehearsed while the old Secret is still retained but unmounted.

## 17. References

### External

| Topic | Deep link |
|---|---|
| Pi subscription providers and OAuth storage | [Pi Providers: Subscriptions and OpenAI Codex](https://pi.dev/docs/latest/providers#subscriptions) |
| Pi CLI flags and exact resource loading | [Pi Usage: CLI Reference](https://pi.dev/docs/latest/usage#cli-reference) |
| Pi RPC framing, prompt, abort, settled event, and stats | [Pi RPC Mode](https://pi.dev/docs/latest/rpc) |
| Pi tool-call blocking extension contract | [Pi Extensions: tool_call](https://pi.dev/docs/latest/extensions#tool_call) |
| Pi has no built-in sandbox | [Pi Security: No Built-in Sandbox](https://pi.dev/docs/latest/security#no-built-in-sandbox) |
| Container isolation pattern | [Pi Containerization: Plain Docker](https://pi.dev/docs/latest/containerization#plain-docker) |
| Selected subscription model | [Pi Model: openai-codex/gpt-5.5](https://pi.dev/models/openai-codex/gpt-5-5) |
| Kubernetes access-mode semantics | [Kubernetes Persistent Volumes: Access Modes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/#access-modes) |

### Repository

| Topic | Deep link |
|---|---|
| Existing AI job API and persistence flow | [`server/workflows/intus/intus_server.py`](../../../server/workflows/intus/intus_server.py) |
| Existing job repository and stale reconciliation | [`server/core/repositories.py`](../../../server/core/repositories.py) |
| Compile command/result contract precedent | [`server/core/compile_messages.py`](../../../server/core/compile_messages.py) |
| JetStream setup precedent | [`server/core/nats_client.py`](../../../server/core/nats_client.py) |
| DB-free one-shot worker precedent | [`server/workflows/intus/compile_job.py`](../../../server/workflows/intus/compile_job.py) |
| API-side result consumer precedent | [`server/workflows/intus/compile_result_consumer.py`](../../../server/workflows/intus/compile_result_consumer.py) |
| Hardened KEDA worker precedent | [`infra/charts/tertius/templates/compile-worker.yaml`](../../../infra/charts/tertius/templates/compile-worker.yaml) |
| Canonical local k3s harness | [`docs/harness/local-harness.md`](../../harness/local-harness.md#k3s-harness) |
| Runtime parity contract | [`docs/harness/runtime-parity.md`](../../harness/runtime-parity.md) |
| Telemetry safety and validation | [`docs/harness/observability-validation.md`](../../harness/observability-validation.md) |

## 18. Clarity Gate

### Thirteen checks

- [x] Actionable: every section fixes behavior, values, files, or verification.
- [x] Current: cluster/storage facts and Pi docs were verified on 2026-07-11.
- [x] Single source: detailed contracts live in this implementation plan; historical plans remain historical.
- [x] Decision, not wish: provider, model, worker count, storage mode, limits, and failure behavior are selected.
- [x] Prompt-ready: tasks identify exact files, tests, commands, and expected outcomes.
- [x] No deferred-state ambiguity: multi-node/RWX and multi-account behavior are explicit non-goals/boundaries.
- [x] No fluff: sections are implementation or operational requirements.
- [x] Type identified: implementation plan.
- [x] Anti-patterns placed here: this is the implementation source of truth.
- [x] Test cases placed here: unit, integration, and end-to-end cases are explicit.
- [x] Error handling placed here: provider, transport, worker, storage, and persistence failures are mapped.
- [x] Deep links present: external contracts and repository precedents have exact links.
- [x] No strategic duplication: the blueprint states decisions; later sections define their implementation once.

### AI coder understandability score

| Criterion | Weight | Score | Evidence |
|---|---:|---:|---|
| Actionability | 25% | 10/10 | Twelve ordered tasks with files, tests, commands, and commits. |
| Specificity | 20% | 10/10 | Exact versions, values, timeouts, limits, schemas, and state transitions. |
| Consistency | 15% | 9/10 | Reuses compile-worker/NATS/API persistence patterns; no DB migration. |
| Structure | 15% | 10/10 | Decisions, contracts, task sequence, matrices, and gates are separated. |
| Disambiguation | 15% | 10/10 | Fifteen anti-patterns plus edge, retry, and rollback behavior. |
| Reference clarity | 10% | 10/10 | All references are direct URLs or repository paths. |

**Weighted score:** 9.85/10. Ready for implementation without an architecture clarification.
