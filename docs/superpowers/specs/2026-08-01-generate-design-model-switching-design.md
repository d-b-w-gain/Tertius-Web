# Generate Design Model Switching Design

**Document type:** Implementation design

**Status:** Design approved; written specification pending user review

## 1. Strategic Blueprint

| Question | Decision | Implementation implication |
|---|---|---|
| What exact problem is being solved? | Authenticated Generate Design users can currently submit only the single deployment-default Pi model and can manually choose source-context tiers. They need to choose GPT-5.6 Sol, Luna, or Terra while Tertius owns a fixed 300,000-character source-context policy. | Add a model selector backed by a deployment-owned catalog; remove the context selector and request field. |
| What are the success metrics? | The models endpoint returns exactly the configured ordered catalog; Sol is selected by default; normal generation and automatic repair dispatch the selected model; unsupported models fail before publish; no client can alter the 300,000-character product budget; focused UI, API, worker, retry, Helm/Compose parity, and authenticated live-flow checks pass. | Tests cover every boundary and live-flow exercises a non-default model. |
| Why will this implementation remain coherent? | The API and one-shot Pi worker read the same validated `PI_AGENT_MODELS_JSON` ConfigMap value, and Compose carries the identical variable for local parity. | Do not maintain a second allowed-model list in frontend or worker code. |
| What is the core architecture decision? | Model availability is deployment configuration; request selection is job data; context size is server-owned product policy. | Parse one catalog in shared Python code, persist the resolved model per job, and use one fixed context constant. |
| What is the stack rationale? | Keep the current React/Vite frontend, FastAPI/Pydantic backend, NATS command, Pi RPC, Helm, and Compose boundaries. | This is an extension of existing contracts, not a framework change. |
| What are the MVP features? | Configured Sol/Luna/Terra catalog, Sol default, accessible selector, selected-model dispatch and retry, fixed 300,000-character context, and runtime-parity documentation/tests. | No pricing, per-model reasoning, or model capability UI. |
| What is explicitly not being built? | No user-defined models, user-defined context size, per-model reasoning selector, pricing display, provider switcher, model health probing, database migration, or changes to OAuth provisioning. | Reject unconfigured IDs and retain global `PI_AGENT_THINKING=medium`. |

## 2. Decisions

### ADR-001: Deployment-owned model catalog

`PI_AGENT_MODELS_JSON` is the single allowed-model catalog for the API and Pi worker. Kubernetes renders it into the existing application ConfigMap and injects it into both processes. Compose defines the same variable for development and parity runtimes.

The ordered default value is:

```json
[
  {"id":"gpt-5.6-sol","label":"GPT-5.6 Sol"},
  {"id":"gpt-5.6-luna","label":"GPT-5.6 Luna"},
  {"id":"gpt-5.6-terra","label":"GPT-5.6 Terra"}
]
```

`PI_AGENT_MODEL=gpt-5.6-sol` remains the default selection. `PI_AGENT_MODEL_LABEL` is removed because the catalog owns labels. `PI_AGENT_THINKING=medium` remains one deployment-wide setting supported by all three pinned Pi 0.80.6 model definitions.

### ADR-002: Server-owned fixed context policy

Generate Design always selects source using `LLM_FILE_EDIT_CONTEXT_CHARS = 300_000`. The existing `LLM_FILE_EDIT_MAX_CONTEXT_CHARS` operational hard cap remains a fail-safe and may only reduce that limit:

```python
max_chars = min(settings.llm_file_edit_max_context_chars, LLM_FILE_EDIT_CONTEXT_CHARS)
```

The `context_tier` request field, tier type, tier lookup function, frontend state, and frontend control are removed. Legacy clients that send an extra `context_tier` property receive normal Pydantic extra-field behavior, but the property does not influence selection.

### ADR-003: Selected model is immutable job provenance

The API resolves `req.model_id or settings.pi_agent_model`, validates catalog membership, and uses that value for the persisted `dispatched_model`, NATS `PiAgentCommand.model`, usage estimate, progress provenance, result provenance, and queued-job republication. The worker validates catalog membership before Pi starts. A deployment-default change does not rewrite an already queued job's model.

## 3. Configuration Contract

### Catalog schema

| Field | Type | Constraint |
|---|---|---|
| Catalog | JSON array | 1 to 20 entries, ordered, no extra top-level value |
| `id` | string | 1 to 200 characters; matches `^[a-z0-9][a-z0-9._-]*$`; unique |
| `label` | string | 1 to 80 trimmed characters; unique model ID remains authoritative |

Configuration validation fails during settings construction when JSON is malformed, the array is empty or oversized, an entry is invalid, IDs are duplicated, or `PI_AGENT_MODEL` is absent from the catalog. Error messages identify the setting and violated rule without echoing the raw JSON.

The response from `GET /llm-usage/models` preserves catalog order:

```json
{
  "default_model_id": "gpt-5.6-sol",
  "models": [
    {"id":"gpt-5.6-sol","model":"gpt-5.6-sol","label":"GPT-5.6 Sol","enabled":true},
    {"id":"gpt-5.6-luna","model":"gpt-5.6-luna","label":"GPT-5.6 Luna","enabled":true},
    {"id":"gpt-5.6-terra","model":"gpt-5.6-terra","label":"GPT-5.6 Terra","enabled":true}
  ]
}
```

When `PI_AGENT_ENABLED=false`, the same models are returned with `enabled:false`. Generate Design disables those options and submission; direct job submission retains the current `503 AI editing is not configured` response.

## 4. Component and Data Flow

```text
Helm values / Compose environment
              |
              v
     PI_AGENT_MODELS_JSON
              |
       shared parser/validator
          /             \
         v               v
GET /llm-usage/models   Pi worker allowlist
         |               ^
         v               |
Generate Design select   |
         | model_id      |
         v               |
POST llm-edit/jobs -> persisted dispatched_model -> NATS command
         |
         v
fixed 300,000-character source selection
```

### Frontend

- Replace the static model label row with a labeled `<select aria-label="AI model">`.
- Populate options only from `/llm-usage/models`; do not hardcode Sol, Luna, or Terra in TypeScript.
- Select `default_model_id` when it exists and is enabled; otherwise select the first enabled model.
- Disable unavailable options. Disable Generate when no enabled model is selected.
- Keep the selected model across project refreshes while it remains in the refreshed catalog.
- Send the selected `model_id` for both normal generation and automatic compile repair.
- Remove the context-tier constant, type import, state, control, request property, and callback dependencies.

### API submission

- Parse and validate the catalog through a shared `server/core/pi_agent_models.py` module used from `Settings` consumers.
- Resolve one model before file selection or job creation.
- Return `400 {"success":false,"error":"unsupported_model"}` for any non-empty ID outside the catalog.
- Store and dispatch the resolved model rather than `settings.pi_agent_model`.
- Select source with the fixed product budget and operational hard cap.

### Worker and retry

- Accept a command model only when it is present in the worker's parsed catalog.
- Preserve the existing provider and thinking-level equality checks.
- Queued reconciliation accepts the persisted model when it remains in the current catalog; otherwise it fails closed through the existing bounded terminal configuration-failure path.
- Result and progress provenance continue comparing against the persisted dispatch dimensions, not the current default.

## 5. File Map

| File | Responsibility |
|---|---|
| `server/core/pi_agent_models.py` | Catalog model, safe JSON parsing, bounds, uniqueness, default-membership validation, and lookup. |
| `server/core/config.py` | Add `pi_agent_models_json`; remove the duplicate default label setting. |
| `server/core/llm_file_edit.py` | Replace context tiers with the fixed 300,000-character product constant and remove the request field. |
| `server/workflows/intus/usage_server.py` | Return all configured model options. |
| `server/workflows/intus/intus_server.py` | Resolve, validate, persist, dispatch, and estimate the selected model; apply fixed context. |
| `server/workflows/intus/pi_agent_job.py` | Validate worker commands against catalog membership. |
| `server/workflows/intus/pi_agent_result_consumer.py` | Reconcile queued jobs against catalog membership while preserving persisted provenance. |
| `ui/src/workflows/shared/projectStorage.ts` | Remove `context_tier` from the browser request type. |
| `ui/src/workflows/generate/GenerateDesignWindow.tsx` | Add model selector and remove context configuration. |
| `infra/charts/tertius/values.yaml` | Define the ordered Sol/Luna/Terra catalog and Sol default. |
| `infra/charts/tertius/templates/configmap.yaml` | Render `PI_AGENT_MODELS_JSON`. |
| `infra/charts/tertius/templates/pi-agent-worker.yaml` | Inject the shared catalog into the one-shot worker. |
| `docker-compose.yml`, `docker-compose.parity.yml` | Define the identical catalog for API and Pi worker services. |
| `server/.env.example` | Document the local catalog and remove the duplicate label setting. |
| `scripts/check-runtime-parity.sh` | Require the model catalog and fixed context contract across runtimes. |
| `scripts/test-deployment-config.sh` | Verify ConfigMap/worker catalog wiring and default membership. |
| `docs/configuration-and-secrets.md` | Document catalog ownership, default selection, and fixed context policy. |

## 6. Error Handling Matrix

| Failure | Detection | Response | Recovery | Telemetry rule |
|---|---|---|---|---|
| Malformed catalog JSON | Settings/catalog parser | Process configuration fails before serving or consuming jobs. | Correct ConfigMap/Compose value and restart. | Log only the setting name and bounded rule; never raw JSON. |
| Empty, oversized, duplicate, or invalid catalog | Catalog validation | Process configuration fails. | Correct catalog. | No catalog content in metric labels. |
| Default model absent from catalog | Settings/catalog validation | Process configuration fails. | Add the default or change `PI_AGENT_MODEL`. | Bounded configuration category only. |
| Models endpoint unavailable | Browser request failure | Existing Generate Design error surface; no selectable model. | Retry refresh after API recovery. | No user/project identifier added. |
| Pi disabled | All options have `enabled:false`; submission also checks server setting | UI disables Generate; direct API returns current 503. | Enable Pi and restart/reconcile. | Existing bounded availability metric/logging. |
| Unsupported request model | API membership check | `400 unsupported_model`; no job row or NATS publish. | Refresh catalog and select an offered model. | Model value must not be copied into unbounded failure labels. |
| Queued model removed from catalog | Reconciler membership check | Existing bounded terminal configuration failure; no republish. | Restore model and resubmit as a new job if required. | Persisted bounded model provenance remains available. |
| Operational hard cap below 300,000 | `min()` at selection | Use the lower safety cap. | Raise the operational cap to at least 300,000. | Do not emit raw source size identifiers. |

## 7. Anti-Patterns (Do Not)

| Do not | Do instead | Reason |
|---|---|---|
| Hardcode the three models in React. | Render the API catalog. | Prevents frontend/runtime disagreement. |
| Keep `PI_AGENT_MODEL_LABEL` beside catalog labels. | Resolve the default label from the catalog. | Removes duplicate configuration. |
| Accept any request model and trust Pi to reject it. | Validate API, worker, and retry boundaries against the shared catalog. | Fails before work is queued and protects worker configuration. |
| Replace a persisted queued model with the current default. | Republish the persisted model when still allowed. | Preserves job provenance and user intent. |
| Keep a hidden or hardcoded `context_tier` browser field. | Remove the field and select with the server constant. | Hidden client policy remains configurable by direct callers. |
| Remove the operational source-size hard cap. | Bound the fixed product budget by the existing cap. | Retains an emergency resource-safety control. |
| Put prompts, source, tokens, user IDs, project IDs, or job IDs in model telemetry. | Use existing bounded labels and persisted job data. | Preserves telemetry safety and cardinality bounds. |
| Treat the Sol OAuth canary as proof that Luna and Terra complete edits. | Run authenticated live-flow with a non-default selected model. | Provider authentication and real model execution are different checks. |

## 8. Test Case Specifications

### Unit and component tests

| ID | Component | Input | Expected result | Edge case |
|---|---|---|---|---|
| U-001 | Catalog parser | Default three-entry JSON | Ordered validated catalog. | Whitespace around JSON. |
| U-002 | Catalog parser | Malformed, empty, 21-entry, duplicate-ID, invalid-ID, and overlong-label inputs | Bounded validation error for each. | Raw JSON absent from error. |
| U-003 | Settings/catalog | Default not present | Settings validation fails. | Catalog ordering does not change default. |
| U-004 | Models endpoint | Pi enabled/disabled | Three ordered records with matching `enabled` value and Sol default. | Labels come from catalog. |
| U-005 | Submit endpoint | Sol, Luna, Terra | Each selected ID is persisted and placed in the command. | Blank ID resolves to default. |
| U-006 | Submit endpoint | Unknown model | `400 unsupported_model`; no publish. | Injection-shaped string remains bounded. |
| U-007 | Context selection | Request omits or includes legacy `context_tier` | Same 300,000-character product limit is applied. | Operational cap below 300,000 wins. |
| U-008 | Worker | Catalog non-default model | Worker starts Pi with the command model. | Unknown model fails before Pi spawn. |
| U-009 | Reconciler | Queued Luna/Terra job | Persisted model is republished. | Removed model fails closed. |
| U-010 | Generate Design | Three enabled models | Sol initially selected; user can select Luna or Terra. | Refresh preserves valid selection. |
| U-011 | Generate Design submit | Terra selected | Normal request and automatic repair contain `model_id:gpt-5.6-terra`. | No `context_tier` property. |
| U-012 | Generate Design availability | All models disabled or response empty | No enabled selection; Generate disabled; existing error guidance remains visible. | Context-size control is absent. |

### Integration and runtime tests

| ID | Flow | Setup | Verification | Teardown |
|---|---|---|---|---|
| I-001 | Helm render | Default values | API ConfigMap and Pi worker receive identical valid JSON; Sol default belongs to catalog; obsolete label variable absent. | Temporary render directory removed by script. |
| I-002 | Compose parity | Development and parity configs | API and worker services receive the same catalog and fixed context contract. | `config` command only; no services retained. |
| I-003 | Authenticated Generate Design | Isolated local-values k3s smoke release with Pi credentials | Select Terra or Luna in the UI, submit a real edit, observe terminal success and compile artifact, and confirm the job/result model matches the selection. | Stop port-forwards and remove disposable release if created for this change. |
| I-004 | Automatic repair | Repairable first compile failure under selected non-default model | Repair request and worker execution retain the original selected model; no context control appears. | Complete terminal job and normal harness cleanup. |

## 9. Validation and Rollout

Run the focused UI, config/catalog, API submission, Pi worker, result-consumer, Helm render, deployment-config, and runtime-parity suites first. Then run UI build and the complete relevant Python test suite.

Because this changes Generate Design and AI-edit behavior, final validation must use an isolated local-values k3s smoke release and full `scripts/harness-k3s.sh live-flow`; `LIVE_FLOW_COMPILE_ONLY=true` is not acceptable. Browser evidence must include the three-option selector, absence of the context-size control, selected non-default model submission, terminal edit, compiled artifact, console, and network inspection.

The existing Sol authentication canary remains unchanged. It verifies OAuth/provider access only and is not reported as a Luna/Terra edit.

## 10. References

| Topic | Location | Exact section or symbol |
|---|---|---|
| Existing Generate Design behavior | [`../plans/2026-06-19-historical-model-artifact-endpoint.md`](../plans/2026-06-19-historical-model-artifact-endpoint.md#generate-design-tab) | Generate Design Tab |
| Existing Pi integration contract | [`../plans/2026-07-11-pi-coding-agent-openai-subscription.md`](../plans/2026-07-11-pi-coding-agent-openai-subscription.md#7-test-case-specifications) | Test Case Specifications U-049 and U-053 |
| Existing model endpoint | [`../../../server/workflows/intus/usage_server.py`](../../../server/workflows/intus/usage_server.py) | `llm_models` |
| Existing submit boundary | [`../../../server/workflows/intus/intus_server.py`](../../../server/workflows/intus/intus_server.py) | `start_llm_file_edit_job` |
| Existing request/context domain | [`../../../server/core/llm_file_edit.py`](../../../server/core/llm_file_edit.py) | `LlmFileEditInput`, `llm_edit_context_chars_for_tier` |
| Existing worker validation | [`../../../server/workflows/intus/pi_agent_job.py`](../../../server/workflows/intus/pi_agent_job.py) | `process_command` configuration check |
| Existing retry validation | [`../../../server/workflows/intus/pi_agent_result_consumer.py`](../../../server/workflows/intus/pi_agent_result_consumer.py) | queued reconciliation configuration check |
| Existing UI model/context controls | [`../../../ui/src/workflows/generate/GenerateDesignWindow.tsx`](../../../ui/src/workflows/generate/GenerateDesignWindow.tsx) | `selectedModel`, `contextTier`, settings rows |
| Runtime validation requirement | [`../../harness/quality-gates.md`](../../harness/quality-gates.md#change-to-validation-matrix) | Change-to-validation matrix |
| Authenticated browser flow | [`../../harness/browser-validation.md`](../../harness/browser-validation.md#journey-c-authenticated-full-workflow) | Journey C |
| Runtime parity | [`../../harness/runtime-parity.md`](../../harness/runtime-parity.md#parity-checklist) | Parity checklist |

## 11. Clarity Gate

### Foundation checks

- [x] Actionable: every requirement maps to a file, contract, or verification.
- [x] Current: inspected against `master` at `5035949` on 2026-08-01.
- [x] Single source: model catalog, default, labels, context policy, and operational cap each have one owner.
- [x] Decision, not wish: all behavior and failure outcomes are fixed.
- [x] Prompt-ready: an implementation agent can execute without choosing product behavior.
- [x] No future state: deferred features are explicit non-goals.
- [x] No fluff: sections contain implementation implications only.

### Document architecture checks

- [x] Type identified: implementation design.
- [x] Anti-patterns are in this implementation document.
- [x] Test cases are in this implementation document.
- [x] Error handling is in this implementation document.
- [x] Deep links identify paths and sections or symbols.
- [x] Historical plans are referenced, not rewritten or duplicated.

### AI coder understandability score

| Criterion | Score | Evidence |
|---|---:|---|
| Actionability (25%) | 10/10 | Exact configuration, flow, files, and outcomes. |
| Specificity (20%) | 10/10 | IDs, bounds, regex, errors, and test inputs are concrete. |
| Consistency (15%) | 10/10 | One ConfigMap catalog and one server context constant. |
| Structure (15%) | 9/10 | Tables and contracts isolate responsibilities. |
| Disambiguation (15%) | 10/10 | Eight anti-patterns and legacy/disabled/removal edge cases. |
| Reference clarity (10%) | 9/10 | Exact paths plus source symbols and documentation anchors. |

**Weighted score:** 9.7/10. The specification passes the 9/10 implementation threshold.
