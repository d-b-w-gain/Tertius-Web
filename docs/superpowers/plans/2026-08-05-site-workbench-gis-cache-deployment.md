# GIS Cache Local k3s and Production Deployment Plan

> **For agentic workers:** Deploy through the Tertius Helm chart and existing harness. Use `kubectl` to inspect, exercise, and diagnose the dev pods; do not leave imperative production drift behind.

**Parent epic:** [#338](https://github.com/d-b-w-gain/Tertius-Web/issues/338)

**Goal:** Prove the GIS cache against the local `tertius-dev` k3s cluster, then carry the same chart, image promotion, runtime configuration, security policy, and smoke checks into the Flux-managed production release.

**Recommendation:** Build one Tertius-owned `gis-cache` service on FastAPI plus TiTiler/rio-tiler. Store validated source extracts as COGs on a PVC and keep immutable STAC-like metadata/evidence manifests beside them. TiTiler supplies mature raster reads, point/part/statistics, previews, tiles, STAC, and MosaicJSON; Tertius supplies safe upstream acquisition, source selection, provenance, cache lifecycle, and engineering-specific analysis.

---

## 1. Evidence from the current dev cluster

Read-only inspection on 2026-08-05 found:

- kubectl context `tertius-dev`, one k3s v1.36 node named `tertius`;
- the main release in namespace `tertius`, labelled and annotated as Helm-managed;
- a one-replica API and UI, CloudNativePG application and Keycloak databases, the Keycloak Operator, NATS JetStream, Valkey, OTEL Collector, VictoriaMetrics, and VictoriaTraces;
- compile work rendered as a KEDA `ScaledJob`, while long-running services use ordinary Deployments/StatefulSets;
- Cilium enforcing NetworkPolicies;
- default `local-path` storage with RWO PVCs;
- about 11 GiB/34% node memory in use during inspection;
- Flux production manifests in `infra/clusters/production`, watching `master` and reconciling the Tertius chart every five minutes;
- production Helm values supplied by the externally managed `tertius-production-values` Secret;
- GitHub Actions building immutable GHCR images, opening a checked image-promotion PR, and updating chart image tags before Flux rollout.

The `tertius-dev` context itself has no `flux-system` namespace. Local dev is therefore harness/Helm-driven; Flux reconciliation applies to the separate production cluster described by the checked-in manifests.

The Windows shell can run `kubectl`, but `helm` was not on its PATH during inspection. Use the repository's WSL/harness path or install Helm before manual render/upgrade work. Record this as a workstation prerequisite, not a cluster defect.

## 2. Technology decision

### Adopt: TiTiler/rio-tiler inside a Tertius service

TiTiler's complete FastAPI application already serves COG, STAC, MosaicJSON, tile-matrix, preview, information, point, and statistics endpoints. rio-tiler reads local or remote GDAL/Rasterio-supported rasters and provides point, bounded-part, and mosaic primitives. This matches both UI visualization and numerical elevation analysis.

Do not expose TiTiler's stock arbitrary `url=` endpoints. Wrap or replace them with routes that accept only Tertius evidence/source identifiers. The service resolves those identifiers to validated local COG paths. This closes the default SSRF surface and prevents users bypassing provider allowlists, byte/pixel bounds, licence policy, and cache validation.

Use:

- TiTiler/rio-tiler for reading, reprojection, numerical sampling, previews, and optional XYZ/WMTS tiles;
- GDAL/rasterio tooling for WCS/ArcGIS exports, clipping, datum/CRS checks, and COG creation;
- local immutable JSON/STAC metadata plus a rebuildable SQLite index on the PVC for the first single-replica release;
- the Tertius application database only for project-scoped evidence references, reviewer state, and audit records—not bulk rasters;
- OTEL for bounded service metrics/traces and the existing collector path.

### Keep optional: MapProxy

MapProxy is a strong plug-and-play proxy/cache for WMS, WMTS/TMS, ArcGIS REST, filesystem, MBTiles, S3, Redis, and related rendered map sources. It is appropriate if the Site map later needs a shared cartographic basemap/overlay cache.

Do not use MapProxy as the primary engineering cache. Its normal cache products are rendered image tiles, which can discard the float elevation values, source datum, nodata semantics, and resolution needed for terrain/topographic calculation.

### Defer: GeoServer plus GeoWebCache

GeoServer is the best option if Tertius needs a general-purpose WMS/WCS/WFS server, admin UI, ImageMosaic catalogue, styles, and broad desktop-GIS interoperability. GeoWebCache is integrated and mature for rendered tile caching.

For this epic it adds a JVM, a persistent mutable GeoServer data directory, REST configuration lifecycle, plugin/version maintenance, and a second API model. Tertius would still need custom provider selection, safe acquisition, evidence manifests, review states, and wind analysis. Re-evaluate it only if WCS interoperability or multi-team GIS publishing becomes a first-class requirement.

### Defer: pygeoapi and Terracotta

- pygeoapi is a good future standards façade for OGC API Coverages and Processes, but it is not the acquisition/cache engine.
- Terracotta is appealing for quickly publishing single-band COG collections, but TiTiler/rio-tiler is a closer fit for custom FastAPI integration, STAC/mosaics, bounded numerical reads, and future terrain algorithms.

## 3. Runtime shape

Render one `apps/v1` Deployment and one internal ClusterIP Service:

- name/component: `<release>-gis-cache` / `app.kubernetes.io/component=gis-cache`;
- replicas: exactly one while using an RWO PVC and local SQLite index;
- update strategy: `Recreate` to prevent simultaneous writers to the cache/index;
- port: `8000`, named `http`;
- PVC mount: `/var/lib/tertius-gis`, with separate `source/`, `derived/`, `manifests/`, `index/`, and `quarantine/` directories;
- writable `emptyDir` for `/tmp`, with a size limit; read-only root filesystem;
- non-root UID/GID and chart `fsGroup` ownership;
- startup probe validates the cache directory/index; readiness checks capacity and index health; liveness checks only the process;
- no public Ingress, NodePort, Cloudflare route, database Secret, NATS credentials, or compile-worker ServiceAccount;
- API reaches it through `http://<release>-gis-cache:8000` from the shared ConfigMap;
- cache pod reaches only DNS, the in-chart OTEL collector, and explicitly approved HTTPS GIS/geocoding providers.

Start local resources at:

- requests: 250m CPU, 512 MiB memory, 1 GiB ephemeral storage;
- limits: 2 CPU, 2 GiB memory, 4 GiB ephemeral storage;
- local PVC: 20 GiB `local-path`;
- production PVC: configurable, initially 100 GiB or the smallest capacity supported by the selected retention/coverage budget.

Measure a real GA 30 m and first state 1 m extract before treating those as final. Alert at 70%/85% storage and stop new acquisitions before disk exhaustion; continue serving verified cache hits where safe.

## 4. Helm implementation

Add a `gisCache` values tree with:

- `enabled`, `replicaCount`, `image.repository/tag/pullPolicy`;
- `port`, `service.type/port`;
- `storage.enabled/existingClaim/size/storageClassName/retain`;
- cache byte quota, low-water mark, maximum request area/pixels/bytes, acquisition timeout, retry/concurrency limits, and evidence TTL policy;
- provider enable flags and non-secret allowlisted hostnames;
- optional provider/geocoder Secret name and key references;
- probes, resources, pod/container security contexts, update strategy, and OTEL configuration.

Add chart templates for:

- Deployment;
- ClusterIP Service;
- retained PVC (or externally supplied claim);
- GIS-specific NetworkPolicy;
- ConfigMap entries used by both API and GIS service;
- optional provider credential Secret references without creating production credentials.

Follow existing helpers/labels and checksum annotations. Add a dedicated `tertius-gis-cache` ServiceAccount only if later evidence proves Kubernetes API access is required; otherwise use no token (`automountServiceAccountToken: false`).

NetworkPolicy must implement:

- ingress to GIS only from the release-local API on its HTTP port;
- no ingress from UI, compile jobs, Pi jobs, other namespaces, or Ingress controllers;
- egress to kube-dns;
- egress to the release-local OTEL collector when enabled;
- HTTPS egress only to configured provider destinations. Because Kubernetes NetworkPolicy cannot safely express arbitrary FQDN allowlists, validate whether Cilium FQDN policy is acceptable for this chart. If not, route upstream traffic through a controlled egress proxy with a stable in-cluster destination.

Do not merely add ingress policy and leave unrestricted egress: provider fetching creates a materially larger SSRF/blind-proxy risk than existing internal services.

## 5. Container and image pipeline

Create a separate `Dockerfile.gis` so GDAL/raster libraries do not enlarge or destabilize the API and compile images.

- Pin the base image by digest where practical.
- Pin GDAL/PROJ/rasterio/TiTiler-compatible versions and record them in evidence manifests.
- Run a compatibility spike on the repository's Python 3.14 platform target before choosing the base. Current Rasterio releases publish Python 3.14 Linux wheels, but the exact TiTiler, Rasterio, NumPy, PROJ, and GDAL matrix must build and pass a fixture COG read together. Do not silently move this one service to a different Python line without documenting the intentional runtime-parity exception.
- Include only provider adapters, GIS contracts, analysis code, migrations/index tooling, and health server code.
- Run as non-root and keep runtime package installation disabled.
- Add a container-level smoke check that opens a fixture COG, reads a known point, and renders one preview tile.

Extend `.github/workflows/images.yml` to build and push:

- `ghcr.io/d-b-w-gain/tertius-gis-cache:smoke-<sha>` for branch smoke runs;
- immutable `master-<run>-<attempt>-<sha>`, `sha-<sha>`, and `master` tags on `master`;
- the same checked immutable tag promoted into `infra/charts/tertius/values.yaml`.

Extend `scripts/promote_images.py` from three to four exact image markers and update its tests. The promotion PR must update API, Pi, UI, and GIS tags atomically so Flux never combines unrelated builds.

Add `Dockerfile.gis` and GIS dependency locks to workflow path filters and caches. Generate an SBOM/vulnerability scan if the existing image workflow gains that gate; GDAL/PROJ native packages make this especially valuable.

## 6. Local dev rollout

### Phase A: isolated technology spike

Use a separate namespace/release such as `tertius-gis-smoke`; do not hand-edit the long-lived `tertius` Deployment.

1. Build the GIS image as `localhost/tertius-gis-cache:local`.
2. Import it into the k3s containerd image store using the same mechanism as the existing harness/local scripts.
3. Render/lint the chart with `values-local.yaml`, GIS enabled, and other components reduced or disabled where the chart supports it.
4. Install the isolated release through Helm/harness.
5. Use `kubectl` to wait for the PVC, Deployment, and endpoint readiness.
6. Acquire one small GA 30 m fallback extract and one NSW Spatial Services 5 m DEM extract through the service contract. The NSW provider resolves the official map-sheet index and caches the normalized sheet; the 2 m contour product is an overlay/reference source, not the z surface.
7. Verify numerical point/profile output and one visualization tile against local GDAL/QGIS results.
8. Restart the pod and prove the second request is a persistent cache hit with no upstream dependency.
9. Prove an arbitrary URL/private-IP request is rejected before any network call.
10. Delete the isolated release and its disposable data only when the exact namespace/PVC targets have been verified.

Representative inspection commands:

```bash
kubectl config current-context
kubectl -n tertius-gis-smoke get deploy,pod,svc,pvc,networkpolicy -o wide
kubectl -n tertius-gis-smoke rollout status deploy/tertius-gis-smoke-gis-cache --timeout=180s
kubectl -n tertius-gis-smoke logs deploy/tertius-gis-smoke-gis-cache --tail=200
kubectl -n tertius-gis-smoke port-forward svc/tertius-gis-smoke-gis-cache 18004:8000
kubectl -n tertius-gis-smoke describe pvc tertius-gis-smoke-gis-cache
kubectl -n tertius-gis-smoke top pod -l app.kubernetes.io/component=gis-cache
```

Prefer service-contract requests from the API or a purpose-built smoke client. The port-forward is for diagnosis only and must not imply a production exposure requirement.

### Phase B: integrate with the long-lived local release

1. Enable `gisCache` in `values-local.yaml`.
2. Deploy the same chart into `tertius` through `scripts/harness-k3s.sh up` or the documented Helm command.
3. Confirm the API ConfigMap points to the release-local GIS service.
4. Use `kubectl rollout status` for GIS and API.
5. Exercise address confirmation → evidence build → review → Structural refresh through the frontend origin.
6. Use `kubectl logs`, `top`, events, and OTEL/Victoria queries to diagnose; do not patch source files into the pod as a release mechanism.
7. Verify a GIS pod restart, an upstream outage/cache hit, a cache-full condition, and a failed provider response leave existing verified structural inputs unchanged.

Required local checks:

```bash
kubectl -n tertius get deploy/tertius-gis-cache svc/tertius-gis-cache pvc
kubectl -n tertius rollout status deploy/tertius-gis-cache --timeout=180s
kubectl -n tertius get endpointslice -l kubernetes.io/service-name=tertius-gis-cache
kubectl -n tertius logs deploy/tertius-gis-cache --since=10m
kubectl -n tertius get events --sort-by=.lastTimestamp
scripts/check-runtime-parity.sh
scripts/harness-k3s.sh live-flow
```

Extend the live-flow harness with a GIS mode or dedicated step that checks a deterministic fixture/provider stub. CI must not depend on public government services being available.

## 7. CI and chart validation

Update `scripts/test-deployment-config.sh` to assert:

- GIS Deployment, Service, PVC, probes, resources, security context, labels, and ConfigMap endpoint render correctly;
- production defaults never create placeholder provider credentials;
- local values select the local image and `local-path` storage;
- disabled GIS values render no orphan Service/PVC/policy;
- NetworkPolicy permits API ingress and intended egress only;
- the image tag is immutable/promoter-managed.

Update `scripts/test-k3s-deployment.sh` to:

- build/import the local GIS image;
- wait for GIS rollout and PVC binding;
- run an in-cluster fixture COG point/tile/cache persistence smoke;
- confirm API-to-GIS reachability and reject UI/compile-job direct access;
- collect GIS logs/events on failure;
- delete only the disposable CI release data during cleanup.

Update Compose development and Compose parity with the same image, mount, healthcheck, internal endpoint, resource intent, and provider config. Extend `scripts/check-runtime-parity.sh` for every GIS variable and secret reference.

## 8. Production/ops rollout through Flux

Production deployment is GitOps-driven:

1. Merge implementation only after chart/config/k3s tests and local authenticated live-flow pass.
2. The image workflow builds all four immutable GHCR images from that exact `master` SHA.
3. The promotion workflow opens/updates the `image-promotion` PR and waits for chart checks.
4. The promotion PR merges only if `master` and the checked head remain unchanged.
5. Flux observes the chart/tag commit and reconciles the `tertius` HelmRelease.
6. The externally managed `tertius-production-values` Secret supplies production storage class/size, enable flag, provider allowlist, quotas, and credential Secret names.

Use a two-step enablement to avoid Flux requesting an image before its immutable tag exists:

- land chart/image-pipeline support with `gisCache.enabled=false`;
- allow image build and atomic promotion to complete;
- update the production values Secret with provider/storage configuration and `gisCache.enabled=true`;
- reconcile/observe the HelmRelease and keep the feature in compare-only mode.

Before enabling, ops must verify:

- at least the requested PVC capacity is available and the chosen production StorageClass/reclaim policy matches recovery expectations;
- provider credentials and egress policy are present;
- the promoted GIS image digest is pullable;
- backup/rebuild policy is documented (source cache may be rebuildable; reviewed evidence/audit references must not be lost);
- the production API can reach GIS, while UI/worker/direct public access is denied;
- alerts and storage-pressure behavior are active.

Operational observation commands:

```bash
kubectl -n flux-system get gitrepository tertius-web
kubectl -n tertius get helmrelease tertius
kubectl -n tertius describe helmrelease tertius
kubectl -n tertius rollout status deploy/tertius-gis-cache --timeout=300s
kubectl -n tertius get pod,svc,pvc,networkpolicy -l app.kubernetes.io/component=gis-cache
kubectl -n tertius logs deploy/tertius-gis-cache --since=15m
kubectl -n tertius top pod -l app.kubernetes.io/component=gis-cache
```

Do not use `kubectl set image`, edit the live Deployment, or copy files into the production pod for rollout. Imperative commands are diagnostic only; permanent changes return through Git, the chart, the production values Secret, and Flux.

## 9. Rollback

- Feature-level rollback: disable GIS suggestions/compare mode while leaving the cache available for diagnosis.
- Workload rollback: set `gisCache.enabled=false` in production values and reconcile Flux; the API must continue with current verified/conservative site inputs.
- Image rollback: revert the promoted chart image tag through Git and let Flux reconcile.
- Data rollback: never downgrade an index in place. Deploy code capable of reading the existing schema or rebuild a new index from immutable manifests/source COGs.
- PVC handling: retain by default during workload rollback. Delete only after source/evidence recovery and exact target verification.

## 10. Deployment completion gates

- [x] TiTiler/rio-tiler wrapper accepts only internal evidence identifiers, not arbitrary URLs.
- [ ] A validated COG supports exact point/profile reads and a UI preview tile from the same source artifact.
- [ ] Local isolated and integrated k3s rollouts pass, including persistent hit after pod restart.
- [ ] Chart, CI k3s, Compose parity, runtime parity, and full authenticated live-flow pass.
- [ ] API-only ingress and provider-bounded egress are enforced by Cilium and tested.
- [x] The fourth immutable image participates in atomic promotion.
- [ ] Flux deploys the promoted image with the external production values contract.
- [ ] Production starts in compare-only mode and cannot automatically reduce verified/conservative design inputs.
- [ ] Storage, upstream failure, cache corruption, and rollback runbooks are exercised.

## Primary technology references

- TiTiler documentation: `https://developmentseed.org/titiler/`
- rio-tiler documentation: `https://cogeotiff.github.io/rio-tiler/`
- MapProxy documentation: `https://mapproxy.github.io/mapproxy/`
- GeoServer/GeoWebCache documentation: `https://docs.geoserver.org/`
- pygeoapi documentation: `https://docs.pygeoapi.io/`

## 11. First implementation checkpoint (2026-08-05)

- Implemented and Linux-tested the evidence-ID-only TiTiler/rio-tiler service,
  deterministic COG ingestion, immutable manifests, point/preview reads, and
  fail-closed upload validation.
- Added the GIS image to Compose, Helm, local k3s build/import, GHCR builds, and
  four-image atomic promotion.
- Helm lint and Kubernetes server-side dry-run passed against `tertius-dev`.
- An isolated in-cluster live HTTP smoke ingested a fixture, returned elevation
  `84.0`, and rejected an arbitrary metadata-service URL with HTTP 422.
- Added an authenticated Site API proxy and an experimental Site-workbench panel
  for service health, manual GeoTIFF upload, provenance, immutable evidence
  metadata, a site-point elevation read, and a diagnostic raster preview. The
  panel cannot alter design inputs.
- The temporary source-injected pod is validation-only. A production-shaped
  Helm rollout still requires a built/imported image; Docker is unavailable on
  this Windows host, and publishing the branch/GHCR smoke image remains a
  separately authorized GitHub action.
