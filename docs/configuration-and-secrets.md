# Configuration and Secrets

This document is the operating reference for Tertius runtime configuration.
Helm values are the source for ConfigMap-backed settings. Kubernetes Secrets
are the source for credentials and prompts that must not be committed.

## ConfigMap

The Helm chart renders non-secret runtime settings into the `tertius-config`
ConfigMap. Most entries come from `app.config`; Keycloak session lifetime
entries mirror `keycloak.realmImport` values so operators can inspect the
active auth lifetime contract in one place. The API receives quota and dispatch
settings; the isolated Pi worker receives its bounded execution settings.
UI pods and compile jobs must not receive Pi provider settings or OAuth state.

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
| `app.config.llmFileEditMaxContextFiles` | `LLM_FILE_EDIT_MAX_CONTEXT_FILES` | API | File-edit context file cap. |
| `app.config.llmFileEditMaxContextChars` | `LLM_FILE_EDIT_MAX_CONTEXT_CHARS` | API | File-edit context character cap. |
| `app.config.llmUserRateLimitPerMinute` | `LLM_USER_RATE_LIMIT_PER_MINUTE` | API | Per-user LLM request rate. |
| `app.config.llmTenantRateLimitPerMinute` | `LLM_TENANT_RATE_LIMIT_PER_MINUTE` | API | Per-tenant LLM request rate. |
| `app.config.llmTenantDailyTokenQuota` | `LLM_TENANT_DAILY_TOKEN_QUOTA` | API | Tenant daily token fallback quota. |
| `app.config.llmUserDailyTokenQuota` | `LLM_USER_DAILY_TOKEN_QUOTA` | API | User daily token fallback quota. |
| `app.config.piAgentProvider` | `PI_AGENT_PROVIDER` | API, Pi worker | Pi provider; fixed to `openai-codex`. |
| `app.config.piAgentModel` | `PI_AGENT_MODEL` | API, Pi worker | Subscription model id. |
| `app.config.piAgentThinking` | `PI_AGENT_THINKING` | Pi worker | Pi reasoning level. |
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
| `cloudflared.tunnelTokenSecretName` | `TUNNEL_TOKEN` | cloudflared | Cloudflare tunnel token. |
| `keycloak.database.appUserSecret.password` | Keycloak DB password | Keycloak | Keycloak database password. |

Production should normally set `app.secretName`, database
secret names, and tunnel token secret names to externally managed Secrets.
Pi OAuth is not a Kubernetes Secret. It is mutable provider state on the retained
Pi auth PVC and is mounted only by the Pi worker and operator auth pod. Provision,
verify, rotate, and remove it with `scripts/pi-agent-auth.sh`; see
`docs/operations/pi-agent-auth.md`.

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
