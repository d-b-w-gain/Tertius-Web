# Tertius Deployment

This directory documents how Tertius is deployed to Kubernetes. Production is managed with Flux against the production cluster; local k3s testing uses the same Helm chart and a local values file.

## Production Architecture

Production is GitOps-driven:

1. Flux watches this repository through `clusters/production/flux-system/gitrepository.yaml`.
2. Flux applies `clusters/production` through `clusters/production/flux-system/kustomization.yaml`.
3. The production Kustomization creates the `tertius` namespace and a Flux `HelmRelease`.
4. The `HelmRelease` renders `charts/tertius` into the production cluster.
5. Production-specific chart values are read from the in-cluster Secret `tertius-production-values`, key `values.yaml`.

The GitRepository intentionally includes only the GitOps and chart paths:

- `clusters/`
- `clusters/production/`
- `charts/`
- `charts/tertius/`

That keeps Flux focused on deployable configuration instead of the whole application source tree.

## Runtime Components

The `charts/tertius` Helm chart renders the Tertius application and its supporting platform resources:

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
clusters/production/
  kustomization.yaml
  flux-system/
    gitrepository.yaml
    kustomization.yaml
  tertius/
    namespace.yaml
    helmrelease.yaml

charts/tertius/
  Chart.yaml
  values.yaml
  values-local.yaml
  templates/
```

The production `HelmRelease` points at `./charts/tertius` from the Flux `GitRepository` source. It reconciles every five minutes, creates the target namespace, retries failed installs/upgrades, and waits through the parent Flux Kustomization.

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
helm dependency update charts/tertius
helm lint charts/tertius
helm template tertius charts/tertius --values charts/tertius/values-local.yaml
scripts/test-deployment-config.sh
```

## Production Operations

After changing chart templates, chart values, or production manifests:

1. Run the local chart checks.
2. Build and push matching API and UI images.
3. Update the production values Secret with the desired image tags and environment settings.
4. Push the GitOps/chart change to the branch watched by Flux.
5. Watch Flux reconcile the source, Kustomization, and HelmRelease.

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
