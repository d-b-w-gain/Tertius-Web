# LLM System Prompt Kubernetes Secret Migration Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the backend AI file-edit system prompt out of hardcoded Python source and into an API-only Kubernetes Secret, while preserving local development defaults and existing Helm deployment patterns.

**Architecture:** The FastAPI backend continues to construct LLM chat messages server-side. The file-edit system prompt becomes a Pydantic settings value read from environment. Helm extends the existing dedicated LLM Secret, currently used for `LLM_API_KEY`, with a new `LLM_FILE_EDIT_SYSTEM_PROMPT` key and injects it only into the API Deployment.

**Tech Stack:** Python, FastAPI, Pydantic Settings, Helm, Kubernetes Secret, pytest, shell Helm render tests.

---

## Success Criteria

- The hardcoded file-edit system prompt in `server/core/llm_client.py` is no longer the only runtime source of truth.
- The backend reads `LLM_FILE_EDIT_SYSTEM_PROMPT` from environment when present.
- Local development and tests still work with the current prompt as the default fallback.
- Helm renders `LLM_FILE_EDIT_SYSTEM_PROMPT` into a Secret, not the ConfigMap.
- Only the API pod receives the prompt secret; UI pods and compile jobs do not.
- Tests cover default prompt behavior, env override behavior, and Helm rendering.

## Non-Goals

- Do not expose the system prompt to the browser.
- Do not move non-sensitive LLM settings, such as base URL or model, into the Secret.
- Do not change the LLM provider, request payload, response parsing, quota logic, or billing flow.
- Do not add a new secret object unless the existing LLM Secret cannot be reused.

## Recommended Approach

Use the existing chart-level LLM Secret as the secret boundary. It already exists for `LLM_API_KEY`, and the system prompt has the same API-only scope. This keeps deployment simple and avoids another secret name/value path.

## Implementation Steps

- [x] Add `llm_file_edit_system_prompt` to `Settings` in `server/core/config.py`.
  - Default it to the current file-edit system prompt text so local dev remains zero-config.
  - Consider exporting the default from `server/core/llm_client.py` or moving the default text to a small helper module if that avoids circular imports.

- [x] Update file-edit prompt construction in `server/core/llm_client.py`.
  - Replace direct use of `FILE_EDIT_SYSTEM_PROMPT` with an explicit prompt parameter or settings-backed helper.
  - Keep `FILE_EDIT_SYSTEM_PROMPT` as the default constant unless the implementation moves it into config.
  - Ensure `build_file_edit_messages(...)` and `estimate_file_edit_tokens(...)` use the same prompt source so token estimation matches provider requests.

- [x] Thread settings through the LLM file-edit call path.
  - Find the endpoint/service call that invokes file-edit generation.
  - Pass `settings.llm_file_edit_system_prompt` into message construction and token estimation.
  - Avoid changing public API request or response models.

- [x] Extend Helm values in `infra/charts/tertius/values.yaml`.
  - Add `app.llmSecret.fileEditSystemPrompt: ""`.
  - Keep `app.llmSecret.create: false` as the production-safe default.
  - Mirror any needed local defaults in `infra/charts/tertius/values-local.yaml` only if local chart tests require it.

- [x] Extend `infra/charts/tertius/templates/secrets.yaml`.
  - Add `LLM_FILE_EDIT_SYSTEM_PROMPT` under the existing LLM Secret `stringData`.
  - Only render this key when `.Values.app.llmSecret.create` is true.

- [x] Extend `infra/charts/tertius/templates/api.yaml`.
  - Add an `env` entry for `LLM_FILE_EDIT_SYSTEM_PROMPT` using `secretKeyRef`.
  - Use the same secret name expression as `LLM_API_KEY`.
  - Mark the key optional if preserving the current optional LLM secret behavior is desired.

- [x] Add or update backend tests.
  - Verify the default prompt is used when no env var is set.
  - Verify `LLM_FILE_EDIT_SYSTEM_PROMPT` overrides the default in generated messages.
  - Verify token estimation uses the overridden prompt.

- [x] Add or update deployment config tests.
  - Render the Helm chart with `app.llmSecret.create=true` and a sample prompt.
  - Assert the prompt appears in the Secret.
  - Assert the prompt does not appear in the ConfigMap.
  - Assert the API Deployment reads `LLM_FILE_EDIT_SYSTEM_PROMPT` from the LLM Secret.

- [x] Update operational notes if the repo has a deployment README for secrets.
  - Document that production should manage the LLM Secret outside committed values.
  - Include a safe `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -` example.

## Production Rollout

1. Create or update the existing LLM Secret in the target namespace:

   ```bash
   kubectl -n tertius create secret generic tertius-llm \
     --from-literal=LLM_API_KEY='...' \
     --from-literal=LLM_FILE_EDIT_SYSTEM_PROMPT='...' \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

2. Deploy the chart version that reads `LLM_FILE_EDIT_SYSTEM_PROMPT`.

3. Restart or roll the API Deployment so pods receive the updated environment.

4. Smoke test an authenticated AI file edit request.

5. Confirm the ConfigMap and non-API workloads do not contain `LLM_FILE_EDIT_SYSTEM_PROMPT`.

## Validation Commands

```bash
pytest server/tests/test_llm_client.py server/tests/test_config.py
./scripts/test-deployment-config.sh
helm template tertius infra/charts/tertius \
  --set app.llmSecret.create=true \
  --set-string app.llmSecret.apiKey=test-key \
  --set-string app.llmSecret.fileEditSystemPrompt='test prompt'
```

## Risks And Mitigations

- **Risk:** Large multi-line prompts are awkward as env vars.
  - **Mitigation:** Kubernetes Secrets support multi-line string data. If prompt size grows significantly, mount the secret key as a file and add a `LLM_FILE_EDIT_SYSTEM_PROMPT_FILE` setting later.

- **Risk:** Prompt value could leak through Helm render logs or GitOps values.
  - **Mitigation:** Keep `app.llmSecret.create=false` in production and manage the Secret with an external secret mechanism.

- **Risk:** Token estimates diverge from actual provider prompts.
  - **Mitigation:** Centralize prompt selection so message construction and estimation use the same value.
