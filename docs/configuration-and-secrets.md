# Configuration and Secrets

This document is the operating reference for Tertius runtime configuration.
Helm values are the source for ConfigMap-backed settings. Kubernetes Secrets
are the source for credentials and prompts that must not be committed.

## ConfigMap

The Helm chart renders `app.config` into the `tertius-config` ConfigMap.
Only the API receives LLM settings. UI pods and compile jobs must not receive
LLM provider settings, API keys, or prompts.

| Helm value | Environment variable | Used by | Purpose |
| --- | --- | --- | --- |
| `app.environment` | `APP_ENV` | API, UI | Deployment environment label. |
| `app.config.apiBasePath` | `API_BASE_PATH` | UI | Base API path exposed by nginx. |
| `app.config.keycloakIssuerUrl` | `KEYCLOAK_ISSUER` | API | Token issuer. |
| `app.config.keycloakAudience` | `KEYCLOAK_AUDIENCE` | API | API audience claim. |
| `app.config.keycloakAuthorizedParty` | `KEYCLOAK_AUTHORIZED_PARTY` | API | UI authorized party claim. |
| `app.config.keycloakJwksUrlOverride` | `KEYCLOAK_JWKS_URL_OVERRIDE` | API | Optional internal JWKS URL. |
| `app.config.oidcIssuerUrl` | `OIDC_ISSUER_URL` | UI | Browser OIDC issuer. |
| `app.config.oidcClientId` | `OIDC_CLIENT_ID` | UI | Browser OIDC client id. |
| `app.config.oidcAudience` | `OIDC_AUDIENCE` | UI | Browser-requested API audience. |
| `app.config.natsUrl` | `NATS_URL` | API, compile worker | NATS connection URL. |
| `app.config.compileStreamName` | `COMPILE_STREAM_NAME` | API, compile worker | Compile stream. |
| `app.config.compileRequestSubject` | `COMPILE_REQUEST_SUBJECT` | API, compile worker | Compile command subject. |
| `app.config.compileResultSubject` | `COMPILE_RESULT_SUBJECT` | API, compile worker | Compile result subject. |
| `app.config.compileWorkerQueue` | `COMPILE_WORKER_QUEUE` | API, compile worker | Compile worker queue. |
| `app.config.compileResultConsumer` | `COMPILE_RESULT_CONSUMER` | API | Result consumer durable. |
| `app.config.compileAckWaitSeconds` | `COMPILE_ACK_WAIT_SECONDS` | API, compile worker | NATS ack window. |
| `app.config.compileMaxDeliver` | `COMPILE_MAX_DELIVER` | API, compile worker | NATS redelivery limit. |
| `app.config.compileTimeoutSeconds` | `COMPILE_TIMEOUT_SECONDS` | API, compile worker | Compile timeout. |
| `app.config.compileRequestMaxBytes` | `COMPILE_REQUEST_MAX_BYTES` | API, compile worker | Max compile command size. |
| `app.config.compileResultMaxBytes` | `COMPILE_RESULT_MAX_BYTES` | API, compile worker | Max compile result size. |
| `app.config.llmModels` | `LLM_MODELS_JSON` | API | Full selectable LLM model catalog. |
| `app.config.llmDefaultModelId` | `LLM_DEFAULT_MODEL_ID` | API | Default selected model id. |
| `app.config.llmDailyBudgetUsd` | `LLM_DAILY_BUDGET_USD` | API | Tenant daily AI spend limit in USD. |
| `app.config.llmTimeoutSeconds` | `LLM_TIMEOUT_SECONDS` | API | Provider HTTP timeout. |
| `app.config.llmMaxOutputTokens` | `LLM_MAX_OUTPUT_TOKENS` | API | Build-script generation output cap. |
| `app.config.llmFileEditMaxOutputTokens` | `LLM_FILE_EDIT_MAX_OUTPUT_TOKENS` | API | File-edit output cap. |
| `app.config.llmFileEditMaxContextFiles` | `LLM_FILE_EDIT_MAX_CONTEXT_FILES` | API | File-edit context file cap. |
| `app.config.llmFileEditMaxContextChars` | `LLM_FILE_EDIT_MAX_CONTEXT_CHARS` | API | File-edit context character cap. |
| `app.config.llmUserRateLimitPerMinute` | `LLM_USER_RATE_LIMIT_PER_MINUTE` | API | Per-user LLM request rate. |
| `app.config.llmTenantRateLimitPerMinute` | `LLM_TENANT_RATE_LIMIT_PER_MINUTE` | API | Per-tenant LLM request rate. |
| `app.config.llmTenantDailyTokenQuota` | `LLM_TENANT_DAILY_TOKEN_QUOTA` | API | Tenant daily token fallback quota. |
| `app.config.llmUserDailyTokenQuota` | `LLM_USER_DAILY_TOKEN_QUOTA` | API | User daily token fallback quota. |
| `app.config.billingStreamName` | `BILLING_STREAM_NAME` | API | Billing stream. |
| `app.config.billingLlmUsageSubject` | `BILLING_LLM_USAGE_SUBJECT` | API | LLM billing subject. |
| `app.config.billingMaxBytes` | `BILLING_MAX_BYTES` | API | Max billing message size. |
| `app.config.billingRateCentsPerHour` | `BILLING_RATE_CENTS_PER_HOUR` | API | Compile billing base rate. |
| `app.config.billingFormatMultiplierStl` | `BILLING_FORMAT_MULTIPLIER_STL` | API | STL compile cost multiplier. |
| `app.config.billingFormatMultiplierStep` | `BILLING_FORMAT_MULTIPLIER_STEP` | API | STEP compile cost multiplier. |
| `app.config.billingFormatMultiplierGltf` | `BILLING_FORMAT_MULTIPLIER_GLTF` | API | glTF compile cost multiplier. |
| `app.config.billingFormatMultiplierGlb` | `BILLING_FORMAT_MULTIPLIER_GLB` | API | GLB compile cost multiplier. |

## Secrets

| Helm value | Environment variable or secret key | Used by | Purpose |
| --- | --- | --- | --- |
| `postgres.appUserSecret.password` | `APP_DB_PASSWORD` | API | Application database password. |
| `app.secret.databaseUrl` | `DATABASE_URL` | API | Optional complete database URL override. |
| `app.secret.valkeyUrl` | `VALKEY_URL` | API | Valkey URL. |
| `app.secret.oidcClientSecret` | `OIDC_CLIENT_SECRET` | API | OIDC confidential client secret, if enabled. |
| `app.llmSecret.apiKey` | `LLM_API_KEY` | API | LLM provider API key. |
| `app.llmSecret.fileEditSystemPrompt` | `LLM_FILE_EDIT_SYSTEM_PROMPT` | API | File-edit system prompt. |
| `cloudflared.tunnelTokenSecretName` | `TUNNEL_TOKEN` | cloudflared | Cloudflare tunnel token. |
| `keycloak.database.appUserSecret.password` | Keycloak DB password | Keycloak | Keycloak database password. |

Production should normally set `app.secretName`, `app.llmSecretName`, database
secret names, and tunnel token secret names to externally managed Secrets.

## LLM Model Schema

`app.config.llmModels` is rendered to `LLM_MODELS_JSON` as an array of objects.
Every model exposed in the UI and used by the API must be present here.

```json
{
  "id": "string, required, stable UI/API id",
  "label": "string, optional display label; defaults to id",
  "model": "string, optional provider model id; defaults to id",
  "endpoint": "string, required provider request URL",
  "api": "openai-chat-completions | anthropic-messages",
  "input_price_per_million": "number, required, USD per 1M uncached input tokens",
  "output_price_per_million": "number, required, USD per 1M output tokens",
  "cached_read_price_per_million": "number or null, optional USD per 1M cached-read tokens",
  "cached_write_price_per_million": "number or null, optional USD per 1M cache-write tokens",
  "enabled": "boolean, optional, defaults to true"
}
```

Example:

```yaml
app:
  config:
    llmDefaultModelId: kimi-k2.7-code
    llmDailyBudgetUsd: "2.00"
    llmModels:
      - id: kimi-k2.7-code
        label: Kimi K2.7 Code
        model: kimi-k2.7-code
        endpoint: https://opencode.ai/zen/go/v1/chat/completions
        api: openai-chat-completions
        input_price_per_million: 0.95
        output_price_per_million: 4.00
        cached_read_price_per_million: 0.19
        cached_write_price_per_million: null
        enabled: true
```

`openai-chat-completions` endpoints are called with OpenAI-compatible
`/chat/completions` semantics. `anthropic-messages` endpoints are called with
Anthropic-compatible `/messages` semantics.
