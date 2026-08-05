# Site Workbench GIS Cache and Site-Specific Wind Basis Plan

> **For agentic workers:** Keep the checkboxes current as each slice lands. Validate the Site-to-Structural flow against the canonical k3s runtime before closing the epic.

**Goal:** Let a user enter and confirm a site address, acquire the best eligible authoritative GIS data for that location through a dedicated cache pod, and produce an auditable site evidence package that can support less conservative—but still fail-safe—wind design inputs.

**Architecture:** Add an internal `gis-cache` service with persistent content-addressed raster/vector storage and provider adapters. The Site API asks it for bounded, versioned site evidence; the Site workbench presents the evidence and requires review; the existing Structural API remains responsible for applying verified inputs and recomputing `V_sit`, `q_z`, and structural actions. Cache analysis-grade source extracts and metadata rather than only rendered tiles.

**Tech stack:** FastAPI/Pydantic, GDAL/rasterio/pyproj-compatible geospatial tooling, Cloud-Optimized GeoTIFF or equivalent bounded raster extracts, React/TypeScript, Helm/k3s, Compose, PVC-backed cache storage, existing Site and Structural workflow contracts.

**Epic:** [#338](https://github.com/d-b-w-gain/Tertius-Web/issues/338)

**Deployment plan:** [Local k3s and production rollout](2026-08-05-site-workbench-gis-cache-deployment.md)

---

## Product outcome

A designer can enter a street address, confirm the resolved point on a map, and see which authoritative datasets cover the site. Tertius selects the best eligible terrain source, uses Geoscience Australia national elevation as a fallback, evaluates terrain/topographic context by compass sector, and records enough provenance to reproduce the result.

The purpose is to replace blanket worst-case assumptions where better evidence supports an economical design. No GIS suggestion may reduce the structural design basis automatically. Unavailable, stale, ambiguous, conflicting, or unlicensed evidence must retain a conservative value and a visible reason.

## Existing boundary

The current implementation on `master` already:

- stores address, coordinates, wind region/status, terrain category, reference height, and exposure multipliers in `tertius_site.py`;
- suggests a wind region from the cached Geoscience Australia overlay;
- keeps the overlay explicitly subordinate to AS/NZS 1170.2 Figure 3.1(A);
- computes `V_sit` and `q_z` in `server/core/structural/site_wind.py`;
- refreshes structural actions without writing derived design actions into `tertius_site.py`.

This epic extends that boundary. It does not replace the current structural calculation authority or turn downloaded GIS data into an automatically verified engineering input.

## Authoritative-source constraints

- Geoscience Australia's SRTM 1-second DEM provides approximately 30 m national coverage and is the baseline fallback.
- State and territory services can provide roughly 1 m or better LiDAR-derived bare-earth DEM for parts of Australia, but coverage, access, licence, datum, currency, and rate limits differ. Each provider needs an explicit adapter and eligibility policy.
- A DEM represents bare earth; a DSM includes buildings/vegetation. Terrain, shielding, and topographic calculations must not silently substitute one for the other.
- Compass-direction `M_d` comes from the applicable AS/NZS 1170.2 edition. It is not derived from elevation tiles. Confirm the standards/licensing basis before encoding or redistributing its tables.
- Geoscience Australia's wind-multiplier software can inform terrain, shielding, and topographic methods, but its assumptions and adaptations require independent engineering verification before design use.
- Preserve horizontal CRS, vertical datum, units, nodata semantics, resolution, lineage, attribution, and acquisition metadata through every transformation.

## Proposed request flow

1. The Site workbench submits an address search.
2. A replaceable geocoding adapter returns bounded candidates; the user confirms a point.
3. The Site API requests a site evidence build for a bounded radius and analysis profile.
4. `gis-cache` inventories coverage, ranks eligible sources, and obtains cached or upstream source extracts.
5. The service validates/transforms the data and produces directional evidence plus uncertainty/fallback reasons.
6. The Site workbench displays sources, compass sectors, quality, and suggested inputs.
7. A reviewer explicitly accepts or overrides inputs with a reason.
8. The existing Structural API recomputes the wind basis and downstream actions.

## Non-negotiable safety rules

- Never lower a verified or conservative design input solely because a new automatic suggestion is smaller.
- Never label an automatically generated value `verified`.
- Fail closed on missing coverage, datum uncertainty, corrupt tiles, unexpected units, stale metadata, upstream errors, licence restrictions, or algorithm-version mismatch.
- Do not fetch arbitrary user-controlled URLs. Provider endpoints are configured and allowlisted.
- Do not emit addresses, coordinates, prompts, source payloads, raw project/job IDs, or content hashes in metrics or logs.
- Keep source evidence immutable and derivations reproducible by source digest plus algorithm/config version.

---

## Slice 1: Data-source and engineering contract

**Primary areas:**

- Add: `docs/gis/`
- Add: `server/core/gis/contracts.py`
- Add tests under: `server/tests/gis/`

- [ ] Confirm the supported AS/NZS 1170.2 edition and the licensed source/versioning policy for compass-direction `M_d`.
- [ ] Catalogue GA and state/territory DEM services: protocol, product type, coverage, resolution, CRS, vertical datum, licence, attribution, authentication, rate limits, freshness, and outage behavior.
- [ ] Define provider eligibility and deterministic source ranking. Prefer suitable bare-earth high-resolution data; fall back to GA SRTM 1-second.
- [ ] Define bounded analysis profiles (site radius, requested resolution, compass sectors, reference height, and maximum bytes/pixels).
- [ ] Define versioned Pydantic contracts for source metadata, coverage, cached artifacts, site evidence, directional results, uncertainty, fallback reasons, and review state.
- [ ] Define which calculations are GIS evidence versus standards-derived structural inputs.
- [ ] Record unresolved shielding inputs explicitly; a DEM alone is not evidence of building/vegetation shielding.

**Gate:** Contract fixtures round-trip deterministically and reject unknown schema versions, missing datum/units, unbounded requests, and incomplete provenance.

## Slice 2: Dedicated GIS cache pod

**Primary areas:**

- Add: `server/gis_cache/`
- Modify: `infra/charts/tertius/`
- Modify: Compose development and parity manifests
- Modify: `scripts/check-runtime-parity.sh`

- [x] Add a separately deployed internal `gis-cache` API with liveness, readiness, and storage health.
- [x] Add a PVC-backed, content-addressed cache for immutable source extracts and derived evidence manifests.
- [ ] Store source URL/identifier, dataset/version, acquisition time, extent, CRS, vertical datum, resolution, media type, licence/attribution, ETag/checksum, byte size, and validation state.
- [ ] Use atomic writes and verify digest, raster bounds, bands, units, nodata, CRS, and pixel limits before publishing a cache entry.
- [ ] Add upstream hostname allowlisting, redirect validation, DNS/IP safety, bounded response size, timeout/retry policy, and per-provider concurrency limits.
- [ ] Add cache quotas, least-recently-used/reference-aware eviction, storage-pressure readiness, corruption quarantine, and safe rebuild behavior.
- [x] Run the pod without database or compile-worker credentials; expose it only to the API through cluster policy.
- [x] Add resource requests/limits, NetworkPolicy, retained PVC configuration, Recreate updates, and non-root/read-only-container settings except for the cache mount.

**Gate:** Two identical bounded requests produce one upstream acquisition and one verified cache hit; corrupt or partial entries become misses without being served.

## Slice 3: Provider adapters and source selection

**Primary areas:**

- Add: `server/gis_cache/providers/`
- Add: `server/gis_cache/source_selection.py`

- [ ] Implement the Geoscience Australia wind-region adapter with current dataset/version/licence metadata.
- [ ] Implement GA SRTM 1-second DEM acquisition as national fallback using an approved WCS, STAC/COG, or equivalent analysis-grade endpoint.
- [ ] Implement the first state high-resolution bare-earth DEM adapter behind the common contract.
- [ ] Keep ArcGIS ImageServer, WCS, STAC/COG, and tile-index details inside provider adapters rather than leaking them into Site workflow code.
- [ ] Determine coverage before downloading large payloads and clip requests to the bounded analysis area.
- [ ] Rank sources by eligibility, product semantics, resolution, accuracy metadata, currency, coverage completeness, datum certainty, and configured preference.
- [ ] Record every rejected candidate and bounded reason without logging site coordinates.
- [ ] Reproject/resample only into an explicitly versioned analysis grid and retain the unmodified source extract.

**Gate:** Golden provider fixtures prove deterministic selection at coverage edges, across mixed resolutions, and when the preferred provider is unavailable.

## Slice 4: Site evidence analysis

**Primary areas:**

- Add: `server/core/gis/site_analysis.py`
- Modify: `server/core/structural/site_wind.py`
- Modify: Site workflow API routes

- [ ] Add replaceable address-geocoding contracts and candidate confirmation; persist the confirmed coordinate and human-readable address independently of provider-specific IDs.
- [ ] Generate terrain/topographic evidence by compass sector using a documented radius, sampling grid, and algorithm version.
- [ ] Separate raw measurements (elevation profile, slope, relief, roughness inputs) from suggested engineering categories/multipliers.
- [ ] Return uncertainty, coverage gaps, resolution, source age, and conservative fallback reasons per sector.
- [ ] Preserve the current wind-region boundary warning and require manual verification near/at region boundaries.
- [ ] Create an immutable site evidence manifest identified by schema, source digests, algorithm version, and configuration version.
- [ ] Store only reviewed inputs in `tertius_site.py`; reference evidence by stable manifest identity and do not write derived wind speed, pressure, or member loads there.
- [ ] Make review/override state explicit: suggested, stale, verified, overridden, unavailable, and conservative fallback.

**Gate:** Re-analysis with identical evidence/config is deterministic. Any source or algorithm change creates a new manifest and marks prior suggestions stale without altering verified design inputs.

## Slice 5: Site workbench experience

**Primary areas:**

- Modify: `ui/src/workflows/site/SiteWorkbench.tsx`
- Modify: `ui/src/workflows/site/contracts.ts`
- Modify: `ui/src/workflows/site/SiteWorkbench.test.tsx`

- [ ] Add address search, bounded candidate list, map/coordinate confirmation, and manual-coordinate fallback.
- [ ] Show selected and alternative source coverage, resolution, DEM/DSM type, datum, age, licence/attribution, and cache/upstream state.
- [x] Add true-north structure placement and a compass-sector view that keeps standards-derived `M_d` separate from GIS-derived terrain/topographic evidence. The first slice accepts licensed/manual `M_d` inputs; directional GIS terrain/topography remains pending.
- [ ] Show raw evidence, suggested value, current verified value, uncertainty, fallback reason, and effect on `V_sit`/`q_z`.
- [ ] Require an explicit review action before adopting suggestions; capture reviewer, reason, time, and applicable standards edition.
- [ ] Preserve manual overrides and make stale/changed source evidence obvious.
- [ ] Use clear `not checked` and conservative states; do not use pass/green presentation for unverified values.
- [ ] Make upstream outages non-destructive: show cached evidence age or retain current verified inputs.

**Gate:** UI tests cover confirmation, coverage fallback, stale evidence, unavailable high-resolution data, sector switching, review, override, and refusal to auto-reduce inputs.

## Slice 6: Runtime, observability, and operations

**Primary areas:**

- Modify: `infra/charts/tertius/values.yaml`
- Modify: Helm templates and Compose manifests
- Add: `docs/gis/operations.md`
- Modify: `docs/configuration-and-secrets.md`
- Modify: `docs/harness/runtime-parity.md`

- [ ] Define parity for provider endpoints, cache limits, timeouts, feature flags, PVCs, resources, and credentials across Helm, Compose development, and Compose parity.
- [ ] Put provider credentials in Secrets and document rotation; never persist them in cache metadata.
- [ ] Add bounded metrics for request outcome, provider class, cache hit/miss, latency bucket, fallback reason, data-age bucket, validation failure class, and storage pressure.
- [ ] Add traces without addresses, coordinates, raw IDs, arbitrary URLs, cache hashes, or raster payloads.
- [ ] Add dashboards/alerts for sustained upstream failure, cache corruption, eviction pressure, provider throttling, and evidence-build latency.
- [ ] Document cache backup/restore, rebuild, schema/algorithm invalidation, attribution, licence review, and incident behavior.
- [ ] Add `scripts/check-runtime-parity.sh` coverage for every new runtime variable.

**Gate:** Runtime parity checks pass and a NetworkPolicy test proves that only approved API-to-cache and cache-to-provider paths are available.

## Slice 7: Engineering verification and rollout

- [ ] Unit-test CRS transforms, vertical datum handling, units, nodata, resampling, compass-sector boundaries, tiling boundaries, cache invalidation, and fail-closed source ranking.
- [ ] Golden-test known sites across flat terrain, ridge/escarpment, coast, wind-region boundary, state-data edge, multiple-source overlap, and national-fallback-only coverage.
- [ ] Compare GIS measurements and suggested factors with independent QGIS/GDAL outputs and qualified engineering calculations.
- [ ] Verify that lower suggestions never replace verified/conservative inputs without explicit review.
- [ ] Run focused server/UI suites, Helm lint/template checks, runtime parity, and the full authenticated Site → Structural live flow in isolated local-values k3s.
- [ ] Roll out behind a feature flag and initially operate in compare-only mode.
- [ ] Define exit criteria for enabling reviewed adoption: source/licence sign-off, engineering golden cases, cache reliability, operational alerts, and documented rollback.

---

## Epic acceptance criteria

- [ ] Entering and confirming an address produces stable coordinates and a reproducible evidence manifest.
- [ ] The service selects the highest-quality eligible licensed DEM and falls back to GA 1-second coverage when needed.
- [ ] Repeated analysis of unchanged sources is served from cache and remains available through a bounded upstream outage.
- [ ] Every displayed value identifies its source, version, resolution, datum, age, quality/uncertainty, and verification state.
- [ ] Directional wind inputs are evaluated per compass sector without conflating standards-derived `M_d` with GIS-derived terrain/topographic evidence.
- [ ] Unavailable, conflicting, stale, corrupt, ambiguous, or unlicensed data cannot reduce the design basis automatically.
- [ ] Verified site inputs continue through the existing Structural API to recompute auditable wind speed, pressure, and actions.
- [ ] Canonical k3s validation proves the new pod, persistence, network policy, Site workflow, and Structural refresh end to end.

## Explicitly out of scope

- Engineering certification or removal of professional review.
- Treating rendered map, imagery, or hillshade tiles as analysis-grade elevation.
- Nationwide mirroring of every state/territory dataset in the first delivery slice.
- Automatic reduction of structural actions from unverified GIS suggestions.
- A general-purpose public GIS server or unrestricted proxy.
- Deriving building shielding from bare-earth elevation alone.

## Initial reference sources

- Geoscience Australia, `1170.2 Wind Regions for Australia` (location aid; the Standard takes precedence).
- Geoscience Australia / Digital Earth Australia, `SRTM-derived 1 Second Digital Elevation Models Version 1.0` (national fallback, CC BY 4.0).
- Geoscience Australia, `Australian local wind multiplier software` and national wind multiplier collection (method/reference input requiring engineering validation).
- State/territory elevation services such as Queensland's public 0.5/1 m LiDAR DEM coverage and Victoria's Vicmap 1 m DEM; access and licensing must be verified per adapter before production use.
