# Tertius Deployment

This directory documents how Tertius is deployed to Kubernetes. Production is managed with Flux against the production cluster; local k3s testing uses the same Helm chart and a local values file.

## Production Architecture

Production is GitOps-driven:

1. Flux watches this repository through `infra/clusters/production/flux-system/gitrepository.yaml`.
2. Flux applies `infra/clusters/production` through `infra/clusters/production/flux-system/kustomization.yaml`.
3. The production Kustomization creates the `tertius` namespace and a Flux `HelmRelease`.
4. The `HelmRelease` renders `infra/charts/tertius` into the production cluster.
5. Production-specific chart values are read from the in-cluster Secret `tertius-production-values`, key `values.yaml`.

The GitRepository intentionally includes only the GitOps and chart paths:

- `infra/clusters/`
- `infra/clusters/production/`
- `infra/charts/`
- `infra/charts/tertius/`

That keeps Flux focused on deployable configuration instead of the whole application source tree.

## Runtime Components

The `infra/charts/tertius` Helm chart renders the Tertius application and its supporting platform resources:

- **UI Deployment and Service**: Nginx serves the built React/Vite app. Browser traffic uses one origin, with `/api/*` reverse-proxied from Nginx to the API Service.
- **API Deployment and Service**: FastAPI serves workflow APIs, owns browser login through a Backend-for-Frontend auth flow, refreshes Keycloak tokens server-side, and uses Postgres for tenant-scoped state, auth sessions, and generated artifacts.
- **CloudNativePG application database**: Provides the Tertius Postgres database through a `postgresql.cnpg.io/v1` `Cluster`.
- **Valkey**: Redis-compatible cache service installed from the chart dependency.
- **Keycloak**: Identity provider managed through the Keycloak Operator, backed by a separate CloudNativePG database.
- **Cloudflare Tunnel**: Optional `cloudflared` Deployment for exposing the UI Service through a single public hostname.
- **ConfigMap and Secret references**: Shared app configuration is rendered into a ConfigMap; sensitive values come from Kubernetes Secrets.
- **OpenTelemetry Collector and optional Victoria backends**: Applications send
  OTLP to the collector. Small installs can enable bundled VictoriaMetrics and
  VictoriaTraces; production-style shared backends should be configured as
  collector exporters while keeping Victoria services internal.

## Request Flow

```text
Browser
  |
  v
Cloudflare Tunnel or port-forward
  |
  v
tertius-ui Service
  |
  +-- / and static assets -> nginx -> React app
  |
  +-- /api/* -> nginx reverse proxy -> tertius-api Service -> FastAPI
```

The API handles browser login through `/api/auth/*`, stores Keycloak access and refresh tokens in the database-backed `auth_sessions` table, and sends the browser only an HttpOnly session cookie plus a CSRF cookie. Browser API calls use same-origin cookies; browser code does not store or refresh OIDC tokens directly.

## Flux Layout

```text
infra/clusters/production/
  kustomization.yaml
  flux-system/
    gitrepository.yaml
    kustomization.yaml
  tertius/
    namespace.yaml
    helmrelease.yaml

infra/charts/tertius/
  Chart.yaml
  values.yaml
  values-local.yaml
  templates/
```

The production `HelmRelease` points at `./infra/charts/tertius` from the Flux `GitRepository` source. It reconciles every five minutes, creates the target namespace, retries failed installs/upgrades, and waits through the parent Flux Kustomization.

## Production Values and Secrets

Do not commit production secrets. Production values are supplied by the cluster Secret named `tertius-production-values` in the `tertius` namespace:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tertius-production-values
  namespace: tertius
stringData:
  values.yaml: |
    app:
      environment: production
      config:
        apiBasePath: /api
        keycloakIssuerUrl: https://<public-origin>/realms/tertius
        keycloakJwksUrlOverride: auto
        authCookieSecure: true
        authSessionIdleSeconds: 604800
        authSessionMaxSeconds: 2592000
        oidcIssuerUrl: https://<keycloak-host>/realms/tertius
        oidcClientId: tertius-ui
        oidcAudience: tertius-api
      secretName: tertius-app-secret
    api:
      image:
        repository: <registry>/tertius-api
        tag: <version>
        pullPolicy: IfNotPresent
    ui:
      image:
        repository: <registry>/tertius-ui
        tag: <version>
        pullPolicy: IfNotPresent
    keycloak:
      hostname: https://<keycloak-host>
      adminHostname: https://<keycloak-admin-host>
      realmImport:
        uiPublicClient: false
        uiClientSecret: <same-secret-as-OIDC_CLIENT_SECRET>
    cloudflared:
      enabled: true
      tunnelTokenSecretName: cloudflared-token
```

Create the referenced application Secret separately if `app.secretName` is set. It should provide sensitive runtime values such as `DATABASE_URL`, `VALKEY_URL`, OIDC client secrets if needed, and any environment-specific credentials.

For cookie-backed browser sessions, the referenced application Secret must include a stable `AUTH_SESSION_SECRET` and should include `OIDC_CLIENT_SECRET` for a confidential Keycloak client:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tertius-app-secret
  namespace: tertius
stringData:
  DATABASE_URL: ""
  VALKEY_URL: redis://tertius-valkey:6379/0
  OIDC_CLIENT_SECRET: <generated-keycloak-client-secret>
  AUTH_SESSION_SECRET: <stable-random-32-byte-or-longer-secret>
```

`AUTH_SESSION_SECRET` must remain stable across API deploys, or in-progress OAuth login state cookies will stop validating. Existing logged-in sessions are stored in Postgres and continue across API/UI rollouts as long as the `auth_sessions` table, Keycloak session, and app Secret remain intact.

Cloudflare tunnel tokens, database passwords, image pull credentials, and Keycloak admin credentials should be managed outside Git, then referenced by chart values.

## Cluster Prerequisites

The production cluster must already have:

- Flux installed and bootstrapped.
- Helm Controller and Source Controller from Flux.
- CloudNativePG CRDs, including `clusters.postgresql.cnpg.io`.
- Keycloak Operator CRDs, including `keycloaks.k8s.keycloak.org`.
- A storage class suitable for Postgres, Keycloak Postgres, and Valkey.
- Access to the API and UI container image registry.
- Any required image pull Secrets.
- The `tertius-production-values` Secret in the `tertius` namespace.
- A Cloudflare tunnel token Secret if `cloudflared.enabled=true`.

For node-level dependency setup, including k3s Cilium migration, gVisor `RuntimeClass/gvisor`, and NetworkPolicy acceptance tests for compile jobs, see `infra/cluster-dependencies.md`.

## Local k3s Validation

Use the local harness to test the Helm chart against an already-running k3s-compatible cluster. It builds the API and UI images, makes them available to k3s, updates chart dependencies, installs or upgrades the release, waits for app and platform resources, then runs smoke checks.

The friendly local wrapper is:

```bash
scripts/harness-k3s.sh up
scripts/harness-k3s.sh status
scripts/harness-k3s.sh smoke
```

It delegates deploy and cleanup to `scripts/test-k3s-deployment.sh`, which
remains the CI-compatible implementation used by the GitHub k3s smoke workflow.
See `docs/harness/local-harness.md` for runtime choices and
`docs/harness/runtime-parity.md` for Compose/Helm drift policy.

```bash
scripts/test-k3s-deployment.sh
```

### Windows Docker k3s Debug Environment

Use this when you want production-shaped debugging on a Windows machine without replacing the normal Docker Compose dev stack. Compose is still the fastest path for application development; local k3s is for deployment, resource, probe, PVC, service-routing, and operator issues.

Prerequisites on Windows:

- Docker Desktop with Linux containers enabled.
- `kubectl` on PATH.
- `helm` on PATH.
- Git Bash or WSL for running `scripts/test-k3s-deployment.sh`.

Start a single-node k3s cluster in Docker and install the operators required by the chart:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-k3s-docker.ps1 -InstallOperators
```

The script creates a Docker container named `tertius-k3s`, writes `.kube/tertius-k3s.yaml`, and prints the `KUBECONFIG` and `K3S_CONTAINER` values needed by the Bash harness. The k3s image tag is pinned; override it with `-K3sVersion` when intentionally testing another Kubernetes version.

Run the Helm deployment harness from Git Bash or WSL:

```bash
export KUBECONFIG="$PWD/.kube/tertius-k3s.yaml"
export K3S_CONTAINER=tertius-k3s
scripts/test-k3s-deployment.sh
```

Open the local UI after the harness starts port-forwards:

```text
http://localhost:18080
```

Useful debug commands:

```bash
kubectl -n tertius get pods -o wide
kubectl -n tertius get events --sort-by='.lastTimestamp'
kubectl -n tertius top pods
kubectl -n tertius describe pod -l app.kubernetes.io/component=api
kubectl -n tertius logs deploy/tertius-api --tail=200
kubectl -n tertius get clusters.postgresql.cnpg.io
kubectl -n tertius get keycloaks.k8s.keycloak.org
```

Reset the local k3s container when you want a clean cluster:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-k3s-docker.ps1 -Reset -InstallOperators
```

Clean up the Tertius release but keep persistent data:

```bash
export KUBECONFIG="$PWD/.kube/tertius-k3s.yaml"
export K3S_CONTAINER=tertius-k3s
scripts/test-k3s-deployment.sh --cleanup
```

Delete the local release, database clusters, and PVC data:

```bash
scripts/test-k3s-deployment.sh --cleanup --delete-data
```

If `helm` is missing on Windows, install it before using `-InstallOperators` or running the harness. For example:

```powershell
winget install Helm.Helm
```

Tunnel-enabled local run:

```bash
ENABLE_TUNNEL=true TUNNEL_TOKEN_SECRET_NAME=cloudflared-token scripts/test-k3s-deployment.sh
```

Useful overrides include:

- `NAMESPACE`
- `RELEASE_NAME`
- `API_IMAGE`
- `UI_IMAGE`
- `TUNNEL_HOSTNAME`
- `KEYCLOAK_REALM`

Cleanup keeps persistent data by default:

```bash
scripts/test-k3s-deployment.sh --cleanup
```

Use `--delete-data` only when PVCs and CloudNativePG database clusters should also be removed.

## Manual Chart Checks

These checks are useful before pushing GitOps changes:

```bash
helm dependency update infra/charts/tertius
helm lint infra/charts/tertius
helm template tertius infra/charts/tertius --values infra/charts/tertius/values-local.yaml
scripts/test-deployment-config.sh
```

## Flux Image Update PAT

Flux image automation pushes image tag bumps to the `flux-image-updates` branch. The GitHub Actions workflow `.github/workflows/flux-image-update-pr.yml` then uses the repository secret `FLUX_IMAGE_UPDATE_PAT` to create and auto-merge a PR back to `master`.

Regenerate the token when the workflow fails with:

```text
Resource not accessible by personal access token (createPullRequest)
```

Create a fine-grained personal access token:

1. Open <https://github.com/settings/personal-access-tokens>.
2. Select **Generate new token**, then **Fine-grained token**.
3. Use a clear token name such as `Tertius Flux image update PR`.
4. Set **Resource owner** to `d-b-w-gain`.
5. Set **Repository access** to **Only select repositories**.
6. Select `d-b-w-gain/Tertius-Web`.
7. Under **Repository permissions**, set:
   - **Contents**: `Read and write`
   - **Pull requests**: `Read and write`
8. Leave **Metadata** as the default read-only permission.
9. Generate the token and copy it immediately.

Update the repository secret:

1. Open `d-b-w-gain/Tertius-Web` on GitHub.
2. Go to **Settings** -> **Secrets and variables** -> **Actions**.
3. Update `FLUX_IMAGE_UPDATE_PAT` with the new token value.
4. Rerun the failed **Flux Image Update PR** workflow, or wait for the next Flux image automation push.

The token should not need repository administration permissions. If PR creation still fails after updating the secret, confirm the token owner has write access to `d-b-w-gain/Tertius-Web` and that the token was created for the `d-b-w-gain` resource owner.

## Production Operations

After changing chart templates, chart values, or production manifests:

1. Run the local chart checks.
2. Update the production values Secret with environment-specific runtime settings when needed.
3. Push the GitOps/chart change to the branch watched by Flux.
4. Watch Flux reconcile the source, Kustomization, and HelmRelease.

Application image builds are handled by the `Build Images` workflow on `master`; Flux image automation promotes new image tags through `infra/charts/tertius/values.yaml`.

Useful production inspection commands:

```bash
flux -n flux-system get sources git tertius-web
flux -n flux-system get kustomizations tertius-web
flux -n tertius get helmreleases tertius
kubectl -n tertius get pods
kubectl -n tertius get clusters.postgresql.cnpg.io
kubectl -n tertius describe helmrelease tertius
```

## Troubleshooting

- If Flux does not see a commit, check the `tertius-web` GitRepository status and confirm it is watching the expected branch.
- If the HelmRelease fails before rendering, confirm the chart dependency lock and values Secret are present.
- If pods cannot pull images, check registry credentials, image names, tags, and `imagePullSecrets`.
- If the API starts but returns authentication errors, confirm the Keycloak issuer URL, client IDs, audience, and realm import settings match the deployed Keycloak realm.
- If `/api/*` fails through the public hostname but direct API Service checks pass, inspect the UI nginx pod configuration and Cloudflare tunnel route.
- If databases do not become ready, inspect CloudNativePG cluster status, storage class availability, and the app-user Secrets referenced by chart values.
