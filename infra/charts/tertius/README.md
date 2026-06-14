# Tertius Helm Chart

This chart renders the Tertius API and UI plus the Kubernetes resources needed for Postgres, Valkey, Keycloak, NATS JetStream, and Cloudflare Tunnel integration.

## Prerequisites

- Kubernetes cluster. Local testing targets k3s.
- Helm 3.
- CloudNativePG operator with `clusters.postgresql.cnpg.io` installed.
- Keycloak Operator with `keycloaks.k8s.keycloak.org` installed.
- KEDA with the `ScaledJob` CRD installed when `keda.enabled=true`.
- `RuntimeClass/gvisor` available for compile Jobs, or override `compileJobs.runtimeClassName`.
- Valkey and NATS Helm dependencies resolved with `helm dependency update infra/charts/tertius`.
- API and UI images already available to the cluster.

## Local k3s Flow

```bash
helm dependency update infra/charts/tertius
helm lint infra/charts/tertius
helm template tertius infra/charts/tertius --values infra/charts/tertius/values-local.yaml
helm upgrade --install tertius infra/charts/tertius \
  --namespace tertius \
  --create-namespace \
  --values infra/charts/tertius/values-local.yaml
```

For local image testing, build images as `tertius-api:local` and `tertius-ui:local`, then make them available to k3s through a local registry or `k3s ctr images import`. The local values use `IfNotPresent` so k3s can use locally loaded images.

Do not run local smoke upgrades against a Flux-managed release unless that is intentional. Use an isolated namespace and release such as `NAMESPACE=tertius-smoke RELEASE_NAME=tertius-smoke` when the cluster already manages `tertius/tertius` through GitOps.

Port-forward smoke tests:

```bash
kubectl -n tertius port-forward svc/tertius-ui 8080:80
curl http://127.0.0.1:8080/
curl http://127.0.0.1:8080/api/
```

In a second shell, direct API testing:

```bash
kubectl -n tertius port-forward svc/tertius-api 8000:8000
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/api/intus/health
```

The chart enables NATS JetStream with file-backed PVC storage by default. The API receives `NATS_URL` through the chart ConfigMap. When `app.config.natsUrl` is empty, the value is derived from the release-local NATS service, for example `nats://tertius-nats:4222` for release `tertius`. Set `app.config.natsUrl` only for unusual deployments with a different internal service contract.

NATS is internal-only. Do not route it through Cloudflare Tunnel, UI nginx, or public ingress. The local smoke harness waits for NATS pods and runs `nats server check jetstream` from an in-cluster `natsio/nats-box` pod.

Compile work runs as KEDA-created `ScaledJob` pods. Those pods use the API image with `server/start-compile-job.sh`, read one compile request from JetStream, publish one result to JetStream, and exit. They intentionally receive only NATS and compile-limit environment variables. Do not add app Secret env, database env, service-account tokens, PVCs, or API/Keycloak/Postgres egress to compile Jobs.

The chart does not install KEDA or its CRDs. `keda.enabled` defaults to `true` for the production and local values so compile work is rendered by default, but clusters without KEDA can render or install the rest of the chart with `--set keda.enabled=false`. Re-enable it only after the `ScaledJob` CRD is present.

By default, compile Job pods get a dedicated NetworkPolicy that denies ingress and only allows egress to DNS and NATS `4222`. API/UI ingress policies remain controlled by `networkPolicy.enabled`. If API egress hardening is added later, it must account for NATS, Postgres, Valkey, Keycloak, DNS, and any required external services together.

## Secrets

Production values should reference externally managed Secrets. Do not commit real database passwords, Valkey credentials, NATS credentials, Keycloak admin credentials, OIDC client secrets, or Cloudflare tunnel tokens.

List the Keycloak-related Secrets in the Tertius namespace:

```bash
rtk kubectl get secrets -n tertius | grep -i keycloak
```

List all key names in a Secret before decoding values:

```bash
rtk kubectl get secret tertius-keycloak-initial-admin -n tertius -o json \
  | jq '.data | keys'
```

Decode one Secret key from Kubernetes base64 storage:

```bash
rtk kubectl get secret tertius-keycloak-initial-admin -n tertius \
  -o jsonpath='{.data.username}' | base64 -d; echo

rtk kubectl get secret tertius-keycloak-initial-admin -n tertius \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Decode every key in a Secret:

```bash
rtk kubectl get secret tertius-keycloak-initial-admin -n tertius -o json \
  | jq -r '.data | to_entries[] | "\(.key)=\(.value | @base64d)"'
```

Decoded Secret values are plaintext credentials. Do not paste them into tickets, logs, pull requests, or committed files.

`values-local.yaml` creates placeholder database Secrets for local testing only. Cloudflare Tunnel is disabled by default; create the token Secret before enabling it:

```bash
kubectl -n tertius create secret generic cloudflared-token \
  --from-literal=TUNNEL_TOKEN="$TUNNEL_TOKEN"
```

Then install with:

```bash
helm upgrade --install tertius infra/charts/tertius \
  --namespace tertius \
  --create-namespace \
  --values infra/charts/tertius/values-local.yaml \
  --set cloudflared.enabled=true
```

## Routing

The intended production route is one public hostname through Cloudflare Tunnel. Route `/` and frontend assets to the UI Service. The UI runtime should reverse-proxy:

- `/api/*` to the API Service
- `/realms/*` and `/resources/*` to Keycloak so OIDC discovery and authorization flows use same-origin
- `/auth/*` as a compatibility alias that rewrites to Keycloak paths

With this in place the browser can keep one origin and still reach authentication endpoints through the UI service.

## Notes

This chart provisions future-facing infrastructure and environment variables. NATS is available as a platform capability, but application-level streams, publishers, consumers, and authentication are deferred until a workflow needs them.
