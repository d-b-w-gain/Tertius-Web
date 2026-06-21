# Runtime Parity

Helm/local k3s is canonical for production-shaped behavior. Compose dev may
intentionally differ for speed, but those differences must be documented instead
of copied silently into Helm. Compose parity should match Helm for routing,
image behavior, and environment contracts.

| Concern | Kubernetes behavior | Compose dev behavior | Compose parity behavior | Drift policy |
| --- | --- | --- | --- | --- |
| API process | `Dockerfile.api` image, FastAPI via start script | bind-mounted `server/` allowed | `Dockerfile.api` image | parity-required |
| UI serving | nginx static assets | Vite dev server/HMR | nginx static assets | parity-required outside dev |
| `/api` routing | same-origin through UI nginx | Vite/dev direct paths may differ | same-origin through UI nginx | parity-required |
| Postgres | CloudNativePG | `postgres` container | `postgres` container | intentional adapter |
| Keycloak | Operator-managed | `keycloak` container | `keycloak` container | issuer/client parity required |
| NATS | chart dependency | `nats` container | `nats` container | subject/max-payload parity required |
| Valkey | chart dependency | not required for all dev flows | inherited/optional | document differences |
| Compile worker model | KEDA `ScaledJob` | looped `compile-job-runner` | looped `compile-job-runner` | intentional adapter |
| KEDA ScaledJob | enabled by chart when CRD exists | not present | not present | k3s required |
| CloudNativePG | app and Keycloak clusters | container Postgres | container Postgres | k3s required |
| PVCs | chart/operator storage | Compose named volumes | Compose named volumes | intentional adapter |
| NetworkPolicy | chart policies | not present | not present | k3s required |
| OTEL collector | chart collector | local collector | local collector | protocol/name parity required |
| metrics backend | optional local chart backend | VictoriaMetrics | VictoriaMetrics | local-only unless enabled |
| environment variables | chart ConfigMap/Secrets | Compose env | production-shaped Compose env | parity-required |
| image build path | deployable images | dev image or bind mount | deployable images | parity-required |

New runtime env vars must be added to Helm values/templates, Compose dev if
needed, Compose parity when production-shaped, and `scripts/check-runtime-parity.sh`
when the value is a runtime contract.
