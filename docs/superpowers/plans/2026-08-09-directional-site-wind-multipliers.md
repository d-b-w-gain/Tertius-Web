# Full Directional Site Wind Multipliers Plan

> **Epic:** [#338](https://github.com/d-b-w-gain/Tertius-Web/issues/338)
> **Parent plan:** [Site Workbench GIS Cache and Site-Specific Wind Basis](2026-08-05-site-workbench-gis-cache.md)

## Goal

Produce an auditable eight-direction site wind basis for:

\[
V_{\mathrm{sit},\beta} = V_R M_c
M_{d,\beta} M_{z,\mathrm{cat},\beta} M_{s,\beta} M_{t,\beta}
\]

Every multiplier must carry its raw measurements, source/version, algorithm and
standard references, confidence/coverage state, review state, and conservative
fallback reason. The resulting eight site speeds must continue through the
existing structure-orientation mapping into the four structural action axes.

The software may propose a lower multiplier, but it must never reduce the
reviewed design basis automatically.

## Current baseline

- `M_d`, `M_z,cat`, `M_s`, and `M_t` now have backward-compatible eight-direction
  contracts and are composed independently before the governing value is chosen
  for each structural face.
- The GIS pod now serves the GA national `M_z,cat`, `M_s`, and `M_t` grids as
  review-required hazard evidence. The GA `M_z,cat` values remain tied to their
  10 m reference height and cannot be adopted as the shed-height value.
- Existing scalar `M_z,cat`, `M_s`, and `M_t` inputs remain the conservative
  fallback until a directional set is explicitly adopted and reviewed.
- G-NAF geocoding, NSW/GA terrain evidence, raster/terrain tiles, and national
  wind multiplier evidence are all hosted behind the Site API and GIS pod.

The GA-baseline milestone was deployed to the local `tertius-fbd-smoke` k3s
release on 2026-08-09. High-resolution, standards-exact local refinement remains
subject to the engineering and licence gates below.

The first local-screening milestone was deployed to the same release on
2026-08-10. It binds the pinned DEM, address/candidate placement and dimensions,
eight terrain profiles, nearby building evidence, algorithm version, and all
directional raw measurements into immutable `windv1-*` evidence. It computes
candidate-height `M_z,cat`, building-screened `M_s`, and profile-screened `M_t`
automatically, but deliberately remains a working/design-screening basis rather
than a claim of licensed-standard verification.

## Decisions

### Do not scrape Google Earth

Google Photorealistic 3D Tiles may be offered as an optional display-only layer
through the official Map Tiles API. They must not enter the GIS cache or any
multiplier calculation. Google prohibits scraping, bulk storage, image/machine
analysis, object detection, geodata extraction, and deriving building models
from its map content.

Google Open Buildings is a separate reusable open dataset, but its published
coverage does not include Australia. It is not a substitute for the Google
Earth mesh around Australian sites.

### Use a two-level wind evidence model

1. **National baseline:** ingest Geoscience Australia's approximately 25 m
   national wind multiplier data. It already contains terrain, shielding, and
   topographic multipliers for eight directions. This provides a fast,
   consistent comparison value and national fallback.
2. **Local refinement:** recompute site evidence from higher-resolution terrain,
   land-cover, building, and point-cloud data where eligible coverage exists.
   Local results remain suggestions until their source and engineering method
   are reviewed.

The GA software uses adapted AS/NZS methods and its public repository is
GPL-3.0 and archived. Before incorporating it into a distributed product,
choose one explicit path:

- run an unmodified, source-published GA engine behind a separate service
  boundary and comply with GPL obligations; or
- implement the project-edition standard independently and use GA data/software
  only as a comparison oracle.

The second path is preferred for the design calculation authority. The GA
national product remains valuable evidence under its data licence.

### Building-data hierarchy for shielding

Use replaceable provider adapters and rank evidence per site:

1. project survey/as-built obstacle geometry supplied by the user;
2. licensed Geoscape National Buildings plus Building Height, if procured;
3. NSW classified LiDAR point clouds combined with an open footprint source;
4. Overture Maps Buildings, including available height/level attributes;
5. no complete eligible source: do not claim shielding and retain the
   conservative multiplier.

Overture is the default open footprint source. It publishes monthly global
GeoParquet, includes source lineage, permits spatial analysis under ODbL, and
conflates OSM, Microsoft and other compatible building sources. Its ML-derived
features and missing heights must remain visible quality limitations.

Microsoft's open global footprints can be retained as a provider fallback, but
Overture normally provides a better conflated view. Geoscape is the strongest
national production option because it links footprints to G-NAF/property data
and offers roof/eave heights, but it is a licensed commercial dependency.

NSW LiDAR is the preferred open height enhancement where coverage and
classification quality are adequate. Standard classes distinguish ground,
vegetation, and buildings, allowing height-above-ground measurements without
deriving geometry from restricted imagery.

## Target architecture

The Site API remains the authenticated boundary. The GIS pod owns acquisition,
normalisation, caching, spatial analysis, and immutable evidence manifests. The
structural code owns the standards-derived site-speed and pressure calculation.

```text
confirmed G-NAF point + candidate geometry + reference height
        |
        v
GIS evidence build
  - GA national multiplier baseline
  - DTM/DEM and optional classified LiDAR
  - land cover / roughness
  - building footprints and heights
        |
        v
8 x DirectionalMultiplierEvidence
  - raw profiles and obstacles
  - proposed Md, Mz,cat, Ms, Mt
  - provenance, coverage, uncertainty, fallback
        |
        v
explicit review/adoption in tertius_site.py
        |
        v
V_sit,beta and qz,beta -> building faces -> +X/-X/+Y/-Y
```

Large LiDAR/local-refinement builds must be asynchronous and content-addressed.
The existing GIS HTTP API can serve cached national lookups synchronously, but
CPU-heavy analysis should use a worker process/deployment rather than block the
API server.

## Phase 1: Engineering and licence contract

- [ ] Confirm the licensed project edition and amendments of AS/NZS 1170.2:2021.
- [ ] Create a clause/table inventory for all four multipliers, including
  applicability exceptions, limits, interpolation, lee-zone handling, and
  conservative defaults.
- [ ] Classify each implemented method as `standard_exact`, `GA_adapted`,
  `hazard_evidence`, or `manual`, and prevent those states being conflated.
- [ ] Verify the GA national product's version, inputs, resolution, vertical and
  horizontal references, licence, and relationship to the 2021 Standard.
- [ ] Decide and record the GPL boundary for the archived GA computation code.
- [ ] Record ODbL obligations for Overture derivatives and the commercial terms
  for any Geoscape adapter.

**Gate:** no multiplier can be labelled verified without a pinned standard
edition, method class, and source/licence record.

## Phase 2: Directional contracts and persistence

- [x] Replace scalar-only `terrain`, `shielding`, and `topographic` fields with
  backward-compatible eight-direction values.
- [x] Add `DirectionalMultiplierEvidence` with direction/bearing, value,
  method, raw measurements, source artifact IDs, coverage, uncertainty,
  review status, reviewer reason, algorithm version, and verifier digest.
- [ ] Add a `SiteWindEvidenceManifest` that binds the confirmed coordinate,
  candidate geometry revision, structure bearing, reference height, all source
  digests, standard edition, and analysis configuration.
- [x] Keep reviewed multiplier values and the evidence-manifest reference in
  `tertius_site.py`; do not persist `V_sit`, `q_z`, or structural loads there.
- [ ] Migrate existing scalar definitions by copying them to all directions and
  marking them `legacy_conservative`, never `verified`.
- [x] Make evidence stale when the site point, structure geometry/orientation,
  reference height, source release, standard edition, or algorithm changes.

**Gate:** all eight directions round-trip deterministically and legacy projects
remain conservative.

## Phase 3: Provider adapters

### National wind multiplier provider

- [x] Add a GA national multiplier adapter for the eight-band/direction NetCDF
  tiles for `M_z,cat`, `M_s`, and `M_t`.
- [ ] Pin releases and cache only bounded source tiles/extracts with complete
  attribution and checksums.
- [x] Expose the national values as baseline evidence, not silently as verified
  AS/NZS 1170.2:2021 design values.

### Terrain and topography providers

- [ ] Extend the NSW adapter to prefer eligible 1 m or 2 m LiDAR-derived DTM
  products when available; retain the current 5 m DEM and GA 30 m fallback.
- [ ] Acquire an adaptive analysis radius sufficient to identify the complete
  governing hill/ridge/escarpment profile; fail closed if the profile hits a
  data edge.
- [ ] Preserve vertical datum, acquisition date, point density/resolution,
  nodata, and accuracy metadata.

### Land-cover and roughness providers

- [ ] Add the GA/DEA classified land-cover input used by the GA multiplier
  method as the national baseline.
- [ ] Add eligible NSW land-use, vegetation/canopy, water, and built-density
  refinements behind the same classification contract.
- [ ] Version the mapping from each provider's classes to roughness length and
  AS/NZS terrain evidence; never hard-code it in UI code.

### Building and obstruction providers

- [ ] Add an Overture Buildings adapter that performs bounded GeoParquet queries,
  retains source lineage/GERS IDs, and pins the monthly release.
- [ ] Add optional Geoscape Buildings/Height adapters behind Secrets and licence
  feature flags; prefer bulk/clip access over expensive per-feature calls.
- [ ] Add a classified NSW LAZ/LAS adapter and generate ground, building, and
  vegetation height products for only the required bounded area.
- [ ] Fuse footprints and height observations without hiding conflicts; retain
  both source values, chosen value, and selection reason.
- [ ] Exclude the candidate structure itself from the set of shielding
  structures while including its dimensions in the standards calculation.

**Gate:** provider fixtures prove deterministic source ranking, conflict
reporting, expiry/staleness, and conservative behaviour with partial coverage.

## Phase 4: Multiplier engines

### `M_d,beta`

- [ ] Verify the digitised regional table against the licensed Standard and
  encode every applicability exception.
- [x] Preserve eight directions independently of structure orientation.
- [ ] Retain the existing face mapping and maximum contributing direction, with
  tests at exact 22.5/45 degree boundaries and every rotation quadrant.

### `M_z,cat,beta`

- [ ] Build the complete upwind fetch for each direction using land cover,
  water, vegetation, and built-density evidence.
- [ ] Calculate directional roughness/terrain transitions and the applicable
  averaged terrain category using the licensed rules.
- [x] Evaluate the terrain/height multiplier at the actual candidate reference
  height rather than a fixed 10 m product height.
- [ ] Return the classified fetch segments and transition distances so a
  reviewer can see why a category/value was chosen.
- [x] Fall back to the greater of the existing reviewed value and the
  conservative eligible category when the sector is incomplete.

### `M_s,beta`

- [x] Determine the standards-required shielding influence area from candidate
  dimensions and reference height.
- [x] For each direction, select eligible upwind shielding buildings and
  calculate their breadth, height, spacing/density, and shielding parameters.
- [x] Use measured heights where available; do not let assumed storey heights
  reduce `M_s`.
- [x] Adopt the January 2016 GA directional shielding grid as the established
  baseline. Calculate Table 4.2 from definitely qualifying lower-bound heights
  even when other sector candidates remain uncertain, and adopt the local
  result only when it produces a lower `M_s`. Missing or uncertain local data
  cannot erase GA baseline shielding credit.
- [ ] Treat vegetation according to the terrain/roughness rules unless the
  licensed shielding rule explicitly permits it.
- [x] Show the included/excluded obstruction set in 2D/3D with exclusion reasons.
- [x] If footprint or height coverage is incomplete, retain the directional GA
  baseline with an explicit evidence reason rather than worsening `M_s` to
  `1.0` because the open reconstruction has holes.

### `M_t,beta`

- [x] Sample true directional ground profiles from a bare-earth DTM, not a DSM
  or contour-derived terraced surface.
- [ ] Detect flat terrain, hills, ridges, escarpments, and applicable lee zones
  using the project-edition definitions.
- [ ] Calculate slope, feature height, crest/site distances, and the resulting
  directional multiplier with limits and interpolation exactly represented.
- [x] Retain every profile and detected breakpoint for report reproduction.
- [x] If the DTM radius, datum, resolution, or profile coverage is inadequate,
  retain the conservative current value and explain the gap.

The Amendment 2 implementation now sweeps two-sided cross-sections across every
plus/minus 22.5 degree sector, applies the amended `H < min(0.4h, 5 m)` screen,
distinguishes the 4L1 hill/ridge influence from the 10L1 downwind escarpment
influence, and never applies an Australian lee-zone reduction (`Mlee = 1.0`).
It retains the governing signed profile, crest/base/half-height breakpoints,
slope, `L1`, `L2`, `Mh`, equation branch and DEM coverage in the evidence. Steep
peak-zone geometry currently uses a disclosed conservative equation envelope;
the exact rectangular peak-zone classification and adaptive search-radius gate
remain open before this method can be labelled `standard_exact`.

The 2026-08-11 local-k3s replay for 14 Porter Street, North Wollongong pinned
algorithm v5 and a separate 5 km topographic DEM. The sector sweep found a
governing southeast escarpment cross-section at 147.5 degrees (`H=39.852 m`,
`x=790 m`, `L2=1018.368 m`) and adopted `M_t=1.024372`. The governing whole-site
wind case remained west at `V_sit=33.033752 m/s`; the generated 17-page report
retains all eight signed cross-sections and the directional calculation ledger.

### Composition

- [x] Produce eight complete `V_sit,beta` and `q_z,beta` records without taking
  an early global maximum.
- [x] Map each structure face to its contributing incoming directions and take
  the governing face value only at the structural boundary.
- [ ] Include component-by-component contribution and provenance in the final
  structural wind basis and report.

**Gate:** changing one direction's evidence affects only the correct sectors,
faces, and structural load cases.

## Phase 5: Site workbench and report

- [ ] Add multiplier layers for `M_z,cat`, `M_s`, `M_t`, and combined `V_sit`
  alongside the current `M_d` rose.
- [ ] Let a user select a direction and inspect its terrain fetch, elevation
  profile, shielding buildings, raw measurements, proposed value, current
  reviewed value, and fallback reason.
- [ ] Display source release, currency, resolution, licence/attribution, method
  class, uncertainty, and coverage for every value.
- [ ] Add an explicit `Adopt suggestion` action per multiplier/direction and an
  `Adopt complete reviewed set` action; both require a reason.
- [x] Never show an automatic suggestion as green/verified.
- [x] Include the evidence maps, profiles, obstacle schedule, tables, equations,
  overrides, and source/version index in the generated site report.
- [ ] If enabled, keep Google Photorealistic 3D Tiles in a clearly attributed
  display-only mode with no server caching or derived analysis.

## Phase 6: Validation

- [ ] Port or create algorithm unit tests from the licensed rules for flat/open,
  roughness transitions, urban shielding, isolated obstacles, hill, ridge,
  escarpment, lee-zone, and data-edge cases.
- [ ] Add rotation/metamorphic tests: rotating all inputs by 45 degrees must
  rotate the eight outputs without changing their values.
- [ ] Add fail-closed tests for missing heights, missing buildings, stale
  releases, partial sectors, datum mismatch, corrupt files, and source conflict.
- [x] Compare national lookups with the published GA multiplier tiles.
- [ ] Compare local algorithms against the GA software on identical input grids,
  while documenting expected differences from GA's adapted methodology.
- [ ] Establish engineer-reviewed golden sites: flat rural, dense urban, coast,
  ridge, escarpment/lee, source boundary, and national-fallback-only.
- [ ] Include 14 Porter Street, North Wollongong as the first visible end-to-end
  fixture, with an independently reproduced QGIS/GDAL evidence package.
- [x] Automatically retain the GA shielding baseline and apply only a lower
  Table 4.2 result supported by definite conservative building-height bounds;
  expose the adopted basis direction-by-direction without another review
  checkbox.
- [ ] Run focused suites, Helm lint/template, runtime parity, and the full
  authenticated Site-to-Structural k3s flow.

## Phase 7: Deployment and operations

- [x] Add the GA provider URL, feature flag, timeout, and worker settings to
  Helm, Compose development, Compose parity, and runtime-parity checks.
- [ ] Add pinned local-provider releases, cache quotas, and optional Geoscape
  credentials when those provider adapters are implemented.
- [ ] Add a dedicated wind-evidence worker deployment when LiDAR processing is
  enabled; grant it only GIS storage and allowlisted provider access.
- [ ] Keep immutable source artifacts and derived manifests on the retained GIS
  PVC with quota/eviction and corruption quarantine.
- [ ] Add bounded metrics for provider, method, cache result, latency,
  resolution/age bucket, fallback class, and analysis result state—never site
  coordinates, addresses, or raw evidence IDs.
- [ ] Roll out in `compare_only` mode first, then `reviewed_adoption`, and only
  enable production report claims after engineering and licence gates pass.

## Delivery milestones

1. **GA baseline:** address lookup returns all four eight-direction multipliers,
   with `M_z,cat`, `M_s`, and `M_t` explicitly labelled GA hazard evidence.
2. **High-resolution topography:** local NSW DTM produces reviewable `M_t,beta`.
3. **Open local context:** land cover plus Overture/LiDAR produces reviewable
   `M_z,cat,beta` and `M_s,beta` with conservative missing-data behaviour.
4. **Authoritative building option:** Geoscape adapter improves footprint and
   height reliability where licensed.
5. **Production gate:** licensed-standard verification, golden-site sign-off,
   reproducible reports, and k3s operational validation are complete.

## Acceptance criteria

- [x] All four multipliers exist independently for N, NE, E, SE, S, SW, W, and
  NW and feed the correct structural faces/actions.
- [ ] Every numeric value is reproducible from an immutable evidence manifest.
- [ ] Every sector identifies source, version, method, raw measurements,
  coverage, uncertainty, and review state.
- [ ] Missing, unlicensed, stale, contradictory, or incomplete local evidence
  cannot worsen or erase the established GA shielding baseline.
- [ ] National baseline results remain available during bounded upstream
  outages; local refinements rebuild deterministically.
- [ ] The final site report contains enough evidence for an independent reviewer
  to reproduce and challenge every selected multiplier.

## Primary references

- Geoscience Australia wind multipliers:
  <https://www.ga.gov.au/scientific-topics/community-safety/data-and-products/wind-multipliers>
- Geoscience Australia computation software:
  <https://github.com/GeoscienceAustralia/Wind_Multipliers>
- Overture Buildings:
  <https://docs.overturemaps.org/guides/buildings/>
- Microsoft Global ML Building Footprints:
  <https://github.com/microsoft/GlobalMLBuildingFootprints>
- NSW Elevation and Depth:
  <https://portal.spatial.nsw.gov.au/client/services?id=f1b1b26b23ab4574a561f890edd3bd7e>
- Geoscape National Buildings:
  <https://docs.geoscape.com.au/en/stable/buildings.html>
- Google Map Tiles API policy:
  <https://developers.google.com/maps/documentation/tile/policies>
