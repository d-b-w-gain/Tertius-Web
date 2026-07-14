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
| Pi agent worker model | serial KEDA `ScaledJob` | one looped `pi-agent-worker` | one looped `pi-agent-worker` | serial execution and transport parity required |
| Pi OAuth storage | retained RWO PVC | retained `pi-agent-auth` volume | retained `pi-agent-auth` volume | never bind host `~/.pi`; explicit delete only |
| Pi append policy | identical read-only image file in API and worker | bind-mounted API policy file and image-backed worker policy file; rebuild the worker after prompt changes | identical read-only image file in API and worker | intentional dev drift; no environment, Secret, ConfigMap, workspace, or OAuth-PVC copy |
| Pi conversation continuity | bounded Postgres context, one `--no-session` worker per turn | bounded Postgres context, one `--no-session` worker per turn | bounded Postgres context, one `--no-session` worker per turn | Pi session files are not persisted |
| Pi network isolation | NetworkPolicy permits DNS, NATS, OTLP, and provider HTTPS | dedicated bridge exposes only NATS/OTLP peers plus bridge internet egress | same as Compose dev | Compose bridge cannot enforce Helm's destination/CIDR policy; verify peer isolation |
| KEDA ScaledJob | enabled by chart when CRD exists | not present | not present | k3s required |
| CloudNativePG | app and Keycloak clusters | container Postgres | container Postgres | k3s required |
| PVCs | chart/operator storage | Compose named volumes | Compose named volumes | intentional adapter |
| NetworkPolicy | chart policies | not present | not present | k3s required |
| OTEL collector | chart collector | local collector | local collector | protocol/name parity required |
| metrics backend | optional local chart backend | VictoriaMetrics | VictoriaMetrics | local-only unless enabled |
| traces backend | optional local chart backend | VictoriaTraces | VictoriaTraces | local-only unless enabled |
| environment variables | chart ConfigMap/Secrets | Compose env | production-shaped Compose env | parity-required |
| image build path | deployable images | dev image or bind mount | deployable images | parity-required |

New runtime env vars must be added to Helm values/templates, Compose dev if
needed, Compose parity when production-shaped, and `scripts/check-runtime-parity.sh`
when the value is a runtime contract.

The Compose `pi-agent-egress` network deliberately has only the Pi worker,
NATS, and the OTLP collector attached. The worker cannot resolve the API,
Postgres, or Keycloak service names. Docker bridge networking still provides
general outbound internet access required for the subscription provider, so it
is a weaker egress boundary than the Helm NetworkPolicy and is not evidence of
destination-level filtering.
