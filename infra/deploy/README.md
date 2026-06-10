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
- **API Deployment and Service**: FastAPI serves workflow APIs, validates Keycloak tokens, uses Postgres for tenant-scoped state, and stores generated artifacts on a mounted cache volume.
- **API PVC**: Mounted at `/app/cache/tertius` for generated workflow/cache artifacts that need to survive pod restarts.
- **CloudNativePG application database**: Provides the Tertius Postgres database through a `postgresql.cnpg.io/v1` `Cluster`.
- **Valkey**: Redis-compatible cache service installed from the chart dependency.
- **Keycloak**: Identity provider managed through the Keycloak Operator, backed by a separate CloudNativePG database.
- **Cloudflare Tunnel**: Optional `cloudflared` Deployment for exposing the UI Service through a single public hostname.
- **ConfigMap and Secret references**: Shared app configuration is rendered into a ConfigMap; sensitive values come from Kubernetes Secrets.

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

Keycloak handles browser login and token issuance. The API validates bearer tokens against the configured Keycloak realm and audience before serving tenant-scoped project data.

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
    cloudflared:
      enabled: true
      tunnelTokenSecretName: cloudflared-token
```

Create the referenced application Secret separately if `app.secretName` is set. It should provide sensitive runtime values such as `DATABASE_URL`, `VALKEY_URL`, OIDC client secrets if needed, and any environment-specific credentials.

Cloudflare tunnel tokens, database passwords, image pull credentials, and Keycloak admin credentials should be managed outside Git, then referenced by chart values.

## Cluster Prerequisites

The production cluster must already have:

- Flux installed and bootstrapped.
- Helm Controller and Source Controller from Flux.
- CloudNativePG CRDs, including `clusters.postgresql.cnpg.io`.
- Keycloak Operator CRDs, including `keycloaks.k8s.keycloak.org`.
- A storage class suitable for the API cache PVC, Postgres, Keycloak Postgres, and Valkey.
- Access to the API and UI container image registry.
- Any required image pull Secrets.
- The `tertius-production-values` Secret in the `tertius` namespace.
- A Cloudflare tunnel token Secret if `cloudflared.enabled=true`.

## Local k3s Validation

Use the local harness to test the Helm chart against an already-running k3s-compatible cluster. It builds the API and UI images, makes them available to k3s, updates chart dependencies, installs or upgrades the release, waits for app and platform resources, then runs smoke checks.

```bash
scripts/test-k3s-deployment.sh
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
