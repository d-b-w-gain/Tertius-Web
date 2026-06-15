# Tertius Kubernetes Deployment Design

## Purpose

Plan a Kubernetes deployment for Tertius that can be tested on a local k3s cluster and later promoted to a production cluster with minimal structural change.

This design covers container packaging, one merged local Helm chart, and a README for cluster prerequisites. It does not include backend implementation work to persist application data in Postgres, use Valkey, or enforce Keycloak authentication yet.

## Current Project Context

Tertius is a modular monolith with:

- `server/`: FastAPI backend that mounts Intus, Artus, Extus, and Timus under `/api/*`.
- `ui/`: React/Vite frontend that can point at the API through `VITE_API_URL`.
- `Dockerfile`: current API-only Python image based on Python 3.11.
- `cache/tertius`: runtime file state expected by the backend for projects, active project pointers, STL/STEP outputs, and related workflow files.

The backend currently writes project and output state to the local filesystem. Postgres, Valkey, and Keycloak will be provisioned and wired through environment variables, but application-level integration is intentionally deferred.

## Decisions

- Use one merged local Helm chart: `infra/charts/tertius`.
- Add a chart README documenting prerequisites and local k3s test steps.
- Package the UI and API as separate container images and Kubernetes Deployments.
- Use Python 3.12 for the API image.
- Use Node 24 LTS for the UI build stage.
- Use nginx as the UI runtime image.
- Use CloudNativePG for Postgres, with the operator installed as a prerequisite.
- Use Valkey as the Redis-compatible cache service, preferably via the official Valkey Helm chart as a chart dependency.
- Use Keycloak for future authentication, with the Keycloak Operator installed as a prerequisite and the Keycloak instance rendered by the local chart.
- Use Cloudflare Tunnel through a `cloudflared` Deployment in the chart, consuming a tunnel token from a Kubernetes Secret.
- Test locally with k3s before production deployment.

## Runtime Compatibility

Python 3.14 is the latest stable Python line, but Tertius should not target it yet because `build123d` declares an upper bound of `<3.14` on PyPI (current releases support 3.10 through 3.13). Python 3.12 is the conservative target for the API container; 3.13 is also supported by build123d if a newer runtime is wanted later.

Node 24 is the current LTS line and is suitable for the Vite build stage.

References:

- Python 3.14 latest stable line: https://docs.python.org/3/whatsnew/3.14.html
- build123d Python compatibility: https://pypi.org/project/build123d/
- Node.js release guidance and current LTS: https://nodejs.org/en/about/releases/

## Container Images

### API Image

Create `Dockerfile.api`.

Responsibilities:

- Start from `python:3.14-slim` and copy in `uv` from `ghcr.io/astral-sh/uv`.
- Install system dependencies required by OpenCASCADE, Build123D, geometry rendering, and git-backed project history.
- `uv sync --no-dev --locked` against the root `pyproject.toml` and `uv.lock` (this also installs the project itself).
- Copy `server/` into the image.
- Set `WORKDIR /app`.
- Expose port `8000`.
- Run `uvicorn server.main:app --host 0.0.0.0 --port 8000`.

Generated artifacts are stored in Postgres. The API image should not require a persistent artifact filesystem.

### UI Image

Create `Dockerfile.ui`.

Responsibilities:

- Use `node:24-alpine` as the build stage.
- Install dependencies from `ui/package-lock.json`.
- Build the Vite app from `ui/`.
- Use nginx as a small runtime image.
- Serve the Vite `dist` output.
- Support SPA fallback to `index.html`.
- Reverse-proxy `/api/*` to the API Service (`proxy_pass`) so same-origin requests resolve to the backend in-cluster.
- Allow the API URL to be configured through the build-time `VITE_API_URL` value.

For local k3s and production tunnel routing, the recommended `VITE_API_URL` value is same-origin `/api`, so browser traffic can go through one public hostname.

Because nginx proxies `/api/*` to the API Service, same-origin routing works identically in both local `kubectl port-forward` testing (where only the UI Service is forwarded) and production tunnel routing, without depending on Cloudflare-side path rules or permissive CORS. The upstream API Service name/port should be configurable so the same image works across namespaces.

## Helm Chart

Create one chart at `infra/charts/tertius`.

### App Workloads

The chart will render:

- API `Deployment`
- API `Service`
- UI `Deployment`
- UI `Service`
- shared app `ConfigMap`
- app Secret references for future database/cache credentials
- optional NetworkPolicies
- optional ServiceAccount

The UI Service will receive browser traffic for static assets and frontend routes.

The API Service will receive internal traffic for `/api/*`.

### Postgres

The chart will render a CloudNativePG `Cluster` resource using `apiVersion: postgresql.cnpg.io/v1`.

The CloudNativePG operator itself is not installed by this chart. It must be installed before the chart is applied.

The chart will define values for:

- cluster name
- PostgreSQL major version 18 by default
- CloudNativePG-compatible PostgreSQL image tag pinned in values
- instance count
- storage size
- storage class
- database name
- application owner
- app user secret name
- optional backup settings left disabled by default for local k3s

The chart will expose the generated or configured database connection details to the API Deployment as `DATABASE_URL`, but the app will not use it until a later integration task.

CloudNativePG chart and operator reference: https://github.com/cloudnative-pg/charts

### Valkey

The chart will include Valkey as a Helm dependency using the official Valkey chart repository.

The chart will define values for:

- standalone mode for local k3s by default
- pinned Valkey chart version in `Chart.yaml`
- pinned Valkey image tag in values
- optional persistence
- auth Secret reference
- service name
- resources
- metrics disabled by default for local k3s

The API Deployment will receive `VALKEY_URL`, but the app will not use it until a later integration task.

Valkey Helm reference: https://valkey.io/valkey-helm/

### Keycloak

The chart will render Keycloak resources for future authentication, but the UI and API will not enforce login until a later integration task.

The chart will render:

- a CloudNativePG `Cluster` for the Keycloak database, separate from the Tertius application database
- a Keycloak database Secret or Secret reference
- a Keycloak `Keycloak` custom resource using `apiVersion: k8s.keycloak.org/v2beta1`
- optional `KeycloakRealmImport` resources for a Tertius realm, public UI client, and API audience/client configuration
- optional Secret references for future OIDC client credentials consumed by the app

The Keycloak Operator itself is not installed by this chart. It must be installed before the chart is applied.

The Keycloak Operator does not manage its own database, so the chart will use CloudNativePG to provision a dedicated Postgres database for Keycloak. This keeps Keycloak operationally isolated from the future Tertius application database while still using the same Postgres operator stack.

The chart will define values for:

- Keycloak CR name
- Keycloak image tag pinned in values
- hostname and admin hostname
- instance count
- resources
- TLS Secret reference
- proxy header mode, defaulting to `xforwarded` for operation behind Cloudflare Tunnel or nginx-style reverse proxies
- Keycloak database cluster name
- Keycloak database storage size and storage class
- optional realm import toggle
- future UI/API OIDC client IDs and Secret references

The UI and API Deployments will receive future-facing OIDC environment variables such as `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, and `OIDC_AUDIENCE`, but the app will not use them until a later integration task.

Keycloak Operator references:

- Basic deployment: https://www.keycloak.org/operator/basic-deployment
- Operator installation: https://www.keycloak.org/operator/installation
- CloudNativePG-backed Keycloak deployment: https://www.keycloak.org/high-availability/single-cluster/deploy-keycloak

### Cloudflare Tunnel

The chart will render:

- `cloudflared` Deployment
- `cloudflared` ServiceMonitor toggle if metrics are later enabled
- Secret reference for `TUNNEL_TOKEN`

The tunnel token should be created outside Helm for real environments. The chart can support an optional development-only value to create the Secret locally, but the README must recommend using an existing Secret for GitOps and production.

The `cloudflared` Deployment should run more than one replica for availability in production, but local k3s can default to one replica.

Cloudflare recommends running `cloudflared` adjacent to application Deployments and scaling it separately. Reference: https://developers.cloudflare.com/tunnel/deployment-guides/kubernetes/

## Routing

The intended external route is a single public hostname through Cloudflare Tunnel.

Routing behavior:

- `/` and frontend assets route to the UI Service.
- `/api/*` is received by the UI Service (nginx) and reverse-proxied to the API Service in-cluster.

Routing `/api/*` through nginx rather than splitting it at the edge keeps a single public hostname and a single ingress target (the UI Service), and makes same-origin behavior identical between local port-forward testing and production.

If using a remotely managed tunnel, the route definitions may live in Cloudflare rather than in the Helm chart. The chart README must document the Cloudflare-side routes needed for local testing and production.

For local k3s testing without Cloudflare, the README should support `kubectl port-forward`:

- UI Service to a local port such as `8080`.
- API Service to a local port such as `8000` for direct health checks.

## Local k3s Testing

The README will document:

1. Ensure a local k3s cluster is running.
2. Install the CloudNativePG operator.
3. Install the Keycloak Operator.
4. Add and update the Valkey Helm repository if dependency updates are needed.
5. Build the API and UI images locally.
6. Make the images available to k3s through a local registry or image import.
7. Create the Cloudflare tunnel token Secret if tunnel testing is enabled.
8. Run `helm dependency update infra/charts/tertius`.
9. Run `helm lint infra/charts/tertius`.
10. Run `helm template infra/charts/tertius --values infra/charts/tertius/values-local.yaml`.
11. Install with `helm upgrade --install tertius infra/charts/tertius --namespace tertius --create-namespace --values infra/charts/tertius/values-local.yaml`.
12. Wait for Deployments, CloudNativePG Clusters, Valkey, and Keycloak to become ready.
13. Port-forward the UI, API, and Keycloak services for smoke testing.

### k3s End-to-End Test Harness

The implementation should add a repeatable test path for an already-running k3s cluster. Prefer a script at `scripts/test-k3s-deployment.sh` plus README instructions, so the full deployment can be validated without remembering a long command sequence.

The script should be safe to run repeatedly. It should fail fast, print the command being executed, and include enough context on failure for the next debugging step.

Required inputs:

- `KUBECONFIG`, defaulting to the user's active Kubernetes context.
- `NAMESPACE`, defaulting to `tertius`.
- `RELEASE_NAME`, defaulting to `tertius`.
- `API_IMAGE`, defaulting to the local k3s image tag from `values-local.yaml`.
- `UI_IMAGE`, defaulting to the local k3s image tag from `values-local.yaml`.
- `ENABLE_TUNNEL`, defaulting to `false`.
- optional `TUNNEL_TOKEN_SECRET_NAME` when tunnel testing is enabled.

Required checks before install:

- `kubectl cluster-info` succeeds against the selected context.
- The selected cluster reports k3s-compatible nodes through `kubectl get nodes`.
- `helm version` succeeds.
- CloudNativePG CRDs are installed, including `clusters.postgresql.cnpg.io`.
- Keycloak Operator CRDs are installed, including `keycloaks.k8s.keycloak.org`.
- Valkey chart dependencies can be resolved through `helm dependency update infra/charts/tertius`.
- Local API and UI images exist in k3s or can be imported before install.

Required install/upgrade flow:

1. Build the API image from `Dockerfile.api`.
2. Build the UI image from `Dockerfile.ui`.
3. Load both images into k3s, using either a local registry or `k3s ctr images import`.
4. Run `helm lint infra/charts/tertius`.
5. Run `helm template infra/charts/tertius --values infra/charts/tertius/values-local.yaml`.
6. Install or upgrade with `helm upgrade --install`.
7. Wait for API and UI Deployments to become available.
8. Wait for CloudNativePG application and Keycloak database clusters to report readiness.
9. Wait for Valkey to become ready.
10. Wait for the Keycloak CR to report readiness.
11. If `ENABLE_TUNNEL=true`, verify the `cloudflared` Deployment becomes available.

Required smoke tests:

- Port-forward the UI Service to a local port and verify `GET /` returns HTML.
- Through the UI port-forward, verify `GET /api/` reaches the FastAPI root through nginx reverse proxying.
- Through the UI port-forward, verify `GET /api/intus/health` returns healthy JSON.
- Port-forward the API Service directly and verify `GET /` returns the backend status JSON.
- Verify remaining database and cache PVCs are bound, and verify the API pod does not mount an artifact PVC.
- Run a short in-cluster Postgres check using a temporary pod or operator-provided connection secret. This should confirm connection to both the future Tertius app database and the Keycloak database.
- Run a short in-cluster Valkey check using `valkey-cli PING` from a temporary pod or the Valkey chart's test pod if available.
- Port-forward Keycloak and verify the well-known OIDC configuration endpoint responds for the configured realm when realm import is enabled. If realm import is disabled, verify the Keycloak root or health endpoint responds instead.
- If `ENABLE_TUNNEL=true`, verify the configured Cloudflare hostname returns the UI and that `/api/intus/health` works through the tunnel.

Required cleanup behavior:

- The script should not delete the namespace by default.
- Add an explicit `--cleanup` option that uninstalls the Helm release and deletes only resources created by this deployment test.
- Preserve PVCs by default; require an explicit `--delete-data` option before deleting PVCs or CloudNativePG clusters.

The README should include a compact example:

```bash
scripts/test-k3s-deployment.sh
```

and a tunnel-enabled example:

```bash
ENABLE_TUNNEL=true TUNNEL_TOKEN_SECRET_NAME=cloudflared-token scripts/test-k3s-deployment.sh
```

## Health Checks

API probes:

- Startup: `GET /` with a generous failure threshold, so a slow first boot is not killed by the liveness probe.
- Readiness: `GET /`
- Liveness: `GET /`

The API image imports OpenCASCADE/Build123D, which can make first start slow. The `startupProbe` gates liveness until the process is up; liveness and readiness then take over once the startup probe succeeds.

Workflow-level health endpoints exist under mounted apps such as `/api/intus/health` and can be used for deeper smoke tests.

UI probes:

- Readiness: HTTP GET `/`
- Liveness: HTTP GET `/`

Postgres readiness is owned by CloudNativePG.

Valkey readiness is owned by the Valkey chart.

Keycloak readiness is owned by the Keycloak Operator and should be checked through the `Keycloak` CR `Ready` condition.

## Values Files

The chart should include:

- `values.yaml`: production-shaped defaults with conservative resources and disabled development shortcuts.
- `values-local.yaml`: local k3s defaults with single replicas, smaller storage, local image tags, `imagePullPolicy: IfNotPresent` (or `Never`) so locally imported images are not re-pulled from a registry, and optional tunnel disabled by default.

Production-specific values should be supplied outside the repository or in a later environment-specific overlay.

## Security

The chart should:

- Avoid embedding real Cloudflare tunnel tokens in Git.
- Avoid embedding real database or Valkey passwords in Git.
- Avoid embedding real Keycloak admin credentials, realm secrets, or OIDC client secrets in Git.
- Use Secret references for sensitive values.
- Run containers as non-root where the base images and filesystem permissions allow it.
- Set resource requests and limits.
- Keep CloudNativePG operator installation outside the app chart.
- Prefer same-origin API routing to avoid permissive CORS in production.

The current backend allows all CORS origins. Hardening CORS should be handled in a later production-readiness task once final hostnames are known.

## Non-Goals

This design does not:

- Implement application usage of Postgres.
- Implement application usage of Valkey.
- Implement Keycloak login, token validation, route protection, or user/session handling in the UI or API.
- Migrate existing filesystem project state to Postgres.
- Add object storage backups.
- Install cluster-wide operators from the app chart.
- Design multi-environment GitOps promotion.
- Harden all production security controls beyond chart structure and secret handling.

## Verification Plan

The implementation plan should include these verification commands:

- Build API image.
- Build UI image.
- Run API container and verify `/` and `/api/intus/health`.
- Run `npm run build` in `ui/`.
- Run `helm dependency update infra/charts/tertius`.
- Run `helm lint infra/charts/tertius`.
- Run `helm template` against default and local values.
- Install into local k3s with `values-local.yaml`.
- Verify pods, services, PVCs, CloudNativePG Clusters, Valkey, and Keycloak are ready.
- Port-forward and load the UI.
- Exercise a minimal API request.
- Port-forward Keycloak or route it locally and confirm the admin console responds.

## Implementation Defaults

- UI runtime: nginx.
- API runtime: Python 3.12.
- UI build runtime: Node 24 LTS.
- PostgreSQL default major version: 18.
- Valkey default image: latest stable Valkey image available when implementation begins, pinned to an exact tag in values.
- Valkey chart dependency: latest official chart version available when implementation begins, pinned in `Chart.yaml` and `Chart.lock`.
- Keycloak deployment method: official Keycloak Operator, installed as a prerequisite; the chart renders `Keycloak` and optional `KeycloakRealmImport` resources.
- Keycloak database: separate CloudNativePG-managed Postgres cluster/database from the future Tertius application database.
- Keycloak app wiring: expose OIDC env values for future use, but do not modify UI/API authentication behavior in this deployment pass.
- Local k3s image loading: document both a local registry and `k3s ctr images import`; prefer a local registry when available. When importing images directly, set `imagePullPolicy: IfNotPresent` (or `Never`) in `values-local.yaml` so k3s does not attempt to pull and fail.
- Local k3s tunnel behavior: disabled by default in `values-local.yaml`; enable only after the `TUNNEL_TOKEN` Secret exists.
