# NATS JetStream Stack Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Split compose/docs, Helm chart wiring, and smoke-test changes across sub-agents when possible.

**Goal:** Add NATS JetStream as a first-class Tertius stack dependency for local development, local k3s smoke deployments, and production GitOps deployments, without changing current application behavior until a concrete producer/consumer workflow is added.

**Architecture:** Run NATS as an internal support service with JetStream enabled and file-backed storage. Expose it to the API through an internal `NATS_URL` configuration value. Keep NATS off public routes, Cloudflare, and UI nginx. Add local Docker Compose support, Helm chart support, vendored chart dependency handling, local k3s smoke checks, and operator documentation.

**Tech Stack:** Existing Docker Compose local stack, Helm chart under `infra/charts/tertius`, Flux production manifests under `infra/clusters/production`, Kubernetes PVC-backed JetStream storage, existing FastAPI backend configuration, and the existing local k3s smoke harness.

---

## Stream Coding Clarity Gate

1. **Problem:** The stack has Postgres for durable relational data and Valkey for cache-like state, but no durable event bus for async jobs, event fanout, or workflow handoff.
2. **Success:** Developers can start NATS locally with Compose, the Helm chart can deploy NATS with JetStream enabled through the official chart schema, the API receives a valid internal `NATS_URL`, vendored chart dependencies remain complete, and local k3s smoke tests prove JetStream is reachable.
3. **Win condition:** NATS becomes a deployable platform capability before product code depends on it, so later async features do not need to redesign local and production infrastructure.
4. **Core decision:** Add NATS JetStream as an internal stack dependency now; defer stream creation, subjects, publishers, and consumers until the first product workflow needs them.
5. **Stack rationale:** NATS JetStream is lightweight for local development, works well in Kubernetes, and fits the preferred stack when messaging is required.
6. **MVP:** Compose service, Helm dependency/values, vendored NATS chart archive, API config/env wiring, local values, chart docs, local k3s smoke test, and deployment-config validation.
7. **Out of scope:** Public NATS exposure, cross-cluster messaging, multi-tenant stream policy, app-level producers/consumers, NATS-backed job execution, and replacing Valkey.
8. **Actors:** Developer running local Compose, operator deploying Helm/Flux, API process connecting to NATS, smoke-test harness validating stack health.
9. **Data boundary:** JetStream stores only future event/job payloads. Current app data remains in Postgres, artifacts remain in their existing storage path, and cache state remains in Valkey.
10. **Security boundary:** NATS stays cluster-internal. NATS authentication is intentionally deferred in this stack-only PR; a later producer/consumer plan must define the exact Secret and API env contract before production workflows depend on NATS.
11. **Failure policy:** If NATS is enabled and unavailable, stack smoke tests fail. Application startup behavior should remain unchanged until app code has a required NATS dependency.
12. **Migration path:** Start with one internal service and one `NATS_URL`; add streams, subjects, auth tightening, and app-level clients in later feature-specific plans.
13. **Verification:** Helm dependency/lint/template, production kustomize, deployment-config assertions, Compose startup, and local k3s JetStream smoke checks.

---

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Broker | NATS with JetStream | Provides messaging plus durable streams without a heavyweight broker |
| Exposure | Internal only | No browser or public edge traffic needs broker access |
| Local mode | Docker Compose service with ports `4222` and `8222` | Makes local development and monitoring simple |
| Kubernetes mode | Official NATS Helm dependency with JetStream enabled through `config.jetstream` | Follows existing Valkey dependency pattern while using the current NATS chart schema |
| Storage | File-backed PVC | JetStream durability requires persistent storage |
| API contract | `NATS_URL` env/config value | Keeps app integration narrow and testable |
| Auth | Deferred for this stack-only PR | Avoids incomplete credential wiring before an app workflow depends on NATS |
| Streams | Deferred | Avoids creating speculative subjects before a real workflow exists |
| NetworkPolicy | Do not add egress policy in this PR | Existing chart only defines ingress policies; egress hardening needs the full dependency graph |

---

## Data And Configuration Contracts

### Docker Compose

Add a `nats` service:

```yaml
nats:
  image: nats:2
  command:
    - -js
    - -sd
    - /data
    - -m
    - "8222"
  ports:
    - "4222:4222"
    - "8222:8222"
  volumes:
    - tertius-nats:/data
```

Add `tertius-nats` under `volumes`.

The existing backend container starts alongside stack dependencies. Add:

```yaml
environment:
  NATS_URL: nats://nats:4222
```

### Helm Values

Add a NATS block shaped for the current official NATS Helm chart. The parent chart value key is `nats` because that is the dependency name, but the nested JetStream settings must use the dependency chart schema:

```yaml
nats:
  enabled: true
  config:
    jetstream:
      enabled: true
      fileStore:
        enabled: true
        dir: /data
        pvc:
          enabled: true
          size: 1Gi
          storageClassName: local-path
  container:
    merge:
      resources:
        requests:
          cpu: 50m
          memory: 64Mi
        limits:
          cpu: 250m
          memory: 256Mi
```

Do not invent a separate `nats.url` value under the dependency chart values. Add a parent-chart value under `app.config.natsUrl` or a helper-derived default in the Tertius templates. The rendered API configuration should default to the release-local NATS client service when the parent override is empty.

Dependency handling must account for the existing vendored chart behavior:

```text
infra/charts/tertius/Chart.yaml
infra/charts/tertius/Chart.lock
infra/charts/tertius/charts/nats-<version>.tgz
```

The smoke script currently skips `helm dependency update` when a vendored Valkey archive exists. Either vendor the NATS archive during implementation or change the preflight check to verify every dependency archive required by `Chart.lock`.

### API Environment

Add:

```text
NATS_URL=nats://<release-nats-service>:4222
```

The default release-local service should be derived in Helm, not hardcoded in values. For release `tertius` and dependency name `nats`, expect the client service to render as `tertius-nats` unless the selected NATS chart version documents a different client service name. Confirm this with `helm template` before finalizing `NATS_URL`.

Do not make existing API startup require NATS until product code actually uses the client. This stack-only PR provides configuration only; it must not add a Python NATS client dependency or a startup connection check.

### Future Subject Convention

Reserve this naming pattern, but do not create streams in the stack-only change:

```text
tertius.artifact.created
tertius.artifact.deleted
tertius.job.requested
```

Reserve `TERTIUS_EVENTS` as the likely first stream name.

---

## Anti-Patterns

| Don't | Do Instead | Why |
| --- | --- | --- |
| Expose NATS through Cloudflare or UI nginx | Keep it internal to Compose/Kubernetes | Broker traffic is not browser traffic |
| Add speculative publishers/consumers in the stack PR | Add only config and health checks | Product behavior should come from a separate workflow plan |
| Use ephemeral JetStream storage in k3s or production | Use PVC-backed file storage | Durable streams need persistence |
| Hardcode release service names in values | Derive service names in Helm templates | Release names vary in CI and local k3s |
| Add partial production NATS auth wiring | Defer auth until a product workflow needs NATS or specify the exact chart/API credential contract | Partial security config creates a false sense of readiness |
| Replace Valkey with NATS | Keep Valkey for cache-like state | They solve different problems |
| Skip smoke checks because Helm renders | Verify JetStream from inside the namespace | Render success does not prove broker readiness |
| Couple API startup to NATS before app code uses it | Make `NATS_URL` available first | Avoids introducing unnecessary boot failures |
| Assume `helm dependency update` always runs | Account for the existing vendored chart archive path | The smoke harness skips updates when vendored dependencies exist |

---

## Task 1: Confirm Current Stack Shape

**Files:**
- Read: `docker-compose.yml`
- Read: `infra/charts/tertius/Chart.yaml`
- Read: `infra/charts/tertius/values.yaml`
- Read: `infra/charts/tertius/values-local.yaml`
- Read: `infra/charts/tertius/templates/api.yaml`
- Read: `infra/charts/tertius/templates/configmap.yaml`
- Read: `infra/charts/tertius/templates/secrets.yaml`
- Read: `scripts/test-k3s-deployment.sh`
- Read: `scripts/test-deployment-config.sh`

- [ ] Confirm Compose currently has Postgres and Keycloak but no NATS.
- [ ] Confirm Helm currently depends on Valkey but not NATS.
- [ ] Confirm API env/config is rendered through the existing ConfigMap/Secret pattern.
- [ ] Confirm local k3s smoke checks currently cover UI/API, Postgres, Valkey, and Keycloak.

## Task 2: Add Local Compose NATS

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`

- [ ] Add a `nats` service using the official `nats:2` image.
- [ ] Enable JetStream with file storage under `/data`.
- [ ] Expose client port `4222` and monitoring port `8222`.
- [ ] Add `tertius-nats` volume.
- [ ] Add a backend `environment` block with `NATS_URL=nats://nats:4222`; the backend service exists today but has no env block.
- [ ] Document `docker compose up -d postgres keycloak nats`.
- [ ] Document local monitoring at `http://localhost:8222`.

## Task 3: Add Helm Dependency And Values

**Files:**
- Modify: `infra/charts/tertius/Chart.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`
- Generated: `infra/charts/tertius/Chart.lock`
- Generated: `infra/charts/tertius/charts/nats-<version>.tgz`

- [ ] Add an official NATS Helm chart dependency pinned to a specific version.
- [ ] Gate the dependency with `nats.enabled`.
- [ ] Enable JetStream with `nats.config.jetstream.enabled=true`.
- [ ] Configure file-backed storage with `nats.config.jetstream.fileStore.pvc.enabled=true`.
- [ ] Configure local single-node PVC-backed storage in `values-local.yaml` using `nats.config.jetstream.fileStore.pvc.size` and `storageClassName`.
- [ ] Add conservative local resources matching the existing support-service style.
- [ ] Run `helm dependency update infra/charts/tertius`.
- [ ] Commit the updated `Chart.lock` and the vendored `infra/charts/tertius/charts/nats-<version>.tgz` archive, or update the smoke harness to run dependency update unless every dependency archive in `Chart.lock` is present.

## Task 4: Wire API Configuration

**Files:**
- Modify: `infra/charts/tertius/templates/configmap.yaml`
- Modify: `infra/charts/tertius/templates/api.yaml`
- Modify: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/values-local.yaml`

- [ ] Add a parent-chart override such as `app.config.natsUrl`, leaving the dependency chart values reserved for the NATS chart itself.
- [ ] Render `NATS_URL` for the API through the existing ConfigMap/Secret envFrom pattern.
- [ ] Default the URL to the release-local NATS client service when the parent override is blank.
- [ ] Support explicit URL override through values for unusual deployments.
- [ ] Do not add NATS auth env vars in this PR; document auth as a follow-up tied to the first producer/consumer workflow.
- [ ] Keep existing API behavior tolerant of missing NATS client code.

## Task 5: Add Network And Security Posture

**Files:**
- Read: `infra/charts/tertius/templates/networkpolicy.yaml`
- Read: `infra/charts/tertius/values.yaml`
- Modify: `infra/charts/tertius/README.md`

- [ ] Do not introduce egress NetworkPolicies in this PR.
- [ ] If touching NetworkPolicy, keep the existing ingress-only behavior intact.
- [ ] Document that a later egress policy must allow API egress to NATS `4222`, Postgres, Valkey, Keycloak, DNS, and any required external services together.
- [ ] Do not add ingress from UI pods or public ingress paths to NATS.
- [ ] Document that production auth is deferred until app workflows depend on NATS, unless this plan is revised with an exact credential contract.
- [ ] Keep local auth disabled unless a later requirement needs parity.

## Task 6: Extend Deployment Config Validation

**Files:**
- Modify: `scripts/test-deployment-config.sh`

- [ ] Assert Helm renders NATS resources when `nats.enabled=true`.
- [ ] Assert the rendered API receives `NATS_URL`.
- [ ] Assert rendered NATS config includes JetStream enabled and a fileStore PVC.
- [ ] Assert every dependency in `Chart.lock` has a matching archive under `infra/charts/tertius/charts/` when the smoke harness is allowed to skip `helm dependency update`.
- [ ] Assert local values do not hardcode release-specific service names.
- [ ] Assert production render keeps NATS internal-only.
- [ ] Keep existing Keycloak, Postgres, Valkey, and Flux path checks intact.

## Task 7: Extend Local k3s Smoke Test

**Files:**
- Modify: `scripts/test-k3s-deployment.sh`
- Modify: `infra/charts/tertius/README.md`

- [ ] Wait for NATS pods to become Ready when NATS is enabled.
- [ ] Add a `check_nats` smoke function.
- [ ] Add `NATS_CHECK_IMAGE`, defaulting to `natsio/nats-box:<pinned-tag>` unless the chosen chart provides a better in-cluster client.
- [ ] Run an in-namespace NATS client image such as `natsio/nats-box`.
- [ ] Verify `nats server check jetstream` or an equivalent JetStream health command.
- [ ] Optionally create and delete a temporary local smoke-test stream.
- [ ] Clean up transient NATS check pods with the existing cleanup pattern by extending the `delete_test_pods` matcher to include `nats`.

## Task 8: Update Operator Documentation

**Files:**
- Modify: `README.md`
- Modify: `infra/charts/tertius/README.md`

- [ ] Document local Compose startup and monitoring.
- [ ] Document Helm dependency update requirements.
- [ ] Document the `NATS_URL` contract.
- [ ] Document local k3s smoke test behavior.
- [ ] Document that NATS is not exposed through Cloudflare or UI nginx.
- [ ] Document that NATS auth is intentionally deferred in this stack-only change.
- [ ] Document the follow-up decision point for NATS auth before the first producer/consumer workflow goes live.

## Task 9: Verify

**Commands:**

```bash
helm dependency update infra/charts/tertius
find infra/charts/tertius/charts -maxdepth 1 -name 'nats-*.tgz' -print -quit | grep -q .
helm lint infra/charts/tertius
helm template tertius infra/charts/tertius --values infra/charts/tertius/values-local.yaml
kubectl kustomize infra/clusters/production
./scripts/test-deployment-config.sh
docker compose config
KUBECONFIG=/home/johnson/.kube/config ./scripts/test-k3s-deployment.sh
```

- [ ] All commands pass.
- [ ] Local k3s smoke output includes a successful NATS JetStream check.
- [ ] Existing UI/API, Postgres, Valkey, and Keycloak smoke checks still pass.
- [ ] Git diff contains only NATS-related stack, docs, and test-harness changes.

---

## Implementation Order

1. Compose and docs first, because this is the smallest local surface.
2. Helm dependency, values, and vendored chart archive next, because this defines the deployable contract.
3. API env rendering after the NATS service name is clear.
4. Validation script changes after rendered output is stable.
5. Local k3s smoke checks last, because they depend on the final chart shape.

## Sub-Agent Work Split

1. **Compose/docs agent:** `docker-compose.yml`, root `README.md`, local startup documentation.
2. **Helm agent:** `Chart.yaml`, values files, API config rendering, dependency vendoring, and network-policy review.
3. **Smoke/validation agent:** `scripts/test-deployment-config.sh`, `scripts/test-k3s-deployment.sh`, chart README.
4. **Integrator:** run dependency update, resolve overlaps, run verification, and produce the final implementation summary.
