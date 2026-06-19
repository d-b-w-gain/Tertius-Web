# Configuration and Secrets

This document is the operating reference for Tertius runtime configuration.
Helm values are the source for ConfigMap-backed settings. Kubernetes Secrets
are the source for credentials and prompts that must not be committed.

## ConfigMap

The Helm chart renders non-secret runtime settings into the `tertius-config`
ConfigMap. Most entries come from `app.config`; Keycloak session lifetime
entries mirror `keycloak.realmImport` values so operators can inspect the
active auth lifetime contract in one place. Only the API receives LLM settings.
UI pods and compile jobs must not receive LLM provider settings, API keys, or
prompts.

| Helm value | Environment variable | Used by | Purpose |
| --- | --- | --- | --- |
| `app.environment` | `APP_ENV` | API, UI | Deployment environment label. |
| `app.config.apiBasePath` | `API_BASE_PATH` | UI | Base API path exposed by nginx. |
| `app.config.keycloakIssuerUrl` | `KEYCLOAK_ISSUER` | API | Token issuer. |
| `app.config.keycloakAudience` | `KEYCLOAK_AUDIENCE` | API | API audience claim. |
| `app.config.keycloakAuthorizedParty` | `KEYCLOAK_AUTHORIZED_PARTY` | API | UI authorized party claim. |
| `app.config.keycloakJwksUrlOverride` | `KEYCLOAK_JWKS_URL_OVERRIDE` | API | Optional internal JWKS URL. |
| `keycloak.realmImport.accessTokenLifespanSeconds` | `KEYCLOAK_ACCESS_TOKEN_LIFESPAN_SECONDS` | Keycloak realm import | Access token lifetime. |
| `keycloak.realmImport.ssoSessionIdleTimeoutSeconds` | `KEYCLOAK_SSO_SESSION_IDLE_TIMEOUT_SECONDS` | Keycloak realm import | Rolling SSO idle timeout; token refresh extends this window. |
| `keycloak.realmImport.ssoSessionMaxLifespanSeconds` | `KEYCLOAK_SSO_SESSION_MAX_LIFESPAN_SECONDS` | Keycloak realm import | Hard SSO session cap. |
| `keycloak.realmImport.clientSessionIdleTimeoutSeconds` | `KEYCLOAK_CLIENT_SESSION_IDLE_TIMEOUT_SECONDS` | Keycloak realm import | Rolling client session idle timeout; token refresh extends this window. |
| `keycloak.realmImport.clientSessionMaxLifespanSeconds` | `KEYCLOAK_CLIENT_SESSION_MAX_LIFESPAN_SECONDS` | Keycloak realm import | Hard client session cap. |
| `app.config.authCookieSecure` | `AUTH_COOKIE_SECURE` | API | Whether auth cookies require HTTPS. Production should be `true`; local HTTP uses `false`. |
| `app.config.authSessionIdleSeconds` | `AUTH_SESSION_IDLE_SECONDS` | API | Rolling API cookie-session idle timeout. |
| `app.config.authSessionMaxSeconds` | `AUTH_SESSION_MAX_SECONDS` | API | Hard API cookie-session max lifetime. |
| `app.config.oidcIssuerUrl` | `OIDC_ISSUER_URL` | API | Optional OIDC issuer metadata value for BFF auth integrations. |
| `app.config.oidcClientId` | `OIDC_CLIENT_ID` | API | OIDC client id used by API BFF auth endpoints. |
| `app.config.oidcAudience` | `OIDC_AUDIENCE` | API | OIDC audience metadata value. |
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
| `app.secret.authSessionSecret` | `AUTH_SESSION_SECRET` | API | Stable signing secret for OAuth login state cookies. |
| `app.llmSecret.apiKey` | `LLM_API_KEY` | API | LLM provider API key. |
| `app.llmSecret.fileEditSystemPrompt` | `LLM_FILE_EDIT_SYSTEM_PROMPT` | API | File-edit system prompt. |
| `cloudflared.tunnelTokenSecretName` | `TUNNEL_TOKEN` | cloudflared | Cloudflare tunnel token. |
| `keycloak.database.appUserSecret.password` | Keycloak DB password | Keycloak | Keycloak database password. |

Production should normally set `app.secretName`, `app.llmSecretName`, database
secret names, and tunnel token secret names to externally managed Secrets.

## Browser Auth Sessions

Browser authentication uses a Backend-for-Frontend flow. The API performs the
OIDC authorization-code exchange, stores access and refresh tokens in the
database-backed `auth_sessions` table, and sends the browser only an HttpOnly
session cookie plus a readable CSRF cookie. Browser code no longer stores or
refreshes OIDC tokens directly.

The API refreshes the stored access token with the stored refresh token when an
authenticated request arrives near access-token expiry. `AUTH_SESSION_IDLE_SECONDS`
is extended by authenticated activity up to `AUTH_SESSION_MAX_SECONDS`; Keycloak
also enforces the configured SSO/client idle and max lifetimes.

The chart defaults to a public Keycloak client with PKCE so default renders do
not create a confidential client without a usable secret. Production can use a
confidential Keycloak client by setting
`keycloak.realmImport.uiPublicClient=false`, setting
`keycloak.realmImport.uiClientSecret` in the production values Secret, and
setting the same value as `OIDC_CLIENT_SECRET` in the app Secret. Also set
`AUTH_SESSION_SECRET` from an externally managed Secret and keep it stable
across deploys. The local chart can run without `OIDC_CLIENT_SECRET` by using
PKCE with the public client; browser sessions are backed by the API session
cookie and database-backed `auth_sessions` rows.

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
