# Australian wind-region overlay

`wind_regions_simplified_t0.0500.json` is the cached, topology-preserving
simplification already used by the ContextUI shed/FBD workflow.

- Source: Geoscience Australia, “1170.2 Wind Regions for Australia”
- DOI: 10.26186/146359
- Licence: CC BY 4.0
- Original CRS: GDA2020 (EPSG:7844)
- Simplification tolerance: 0.05 degrees
- SHA-256: `58aa51b5e8334771205504e9bddb88735e50e06cda766b7940d0588717f29651`

The dataset and this simplified derivative are location aids only. They are
not suitable as the sole design authority. AS/NZS 1170.2 Figure 3.1(A) takes
precedence, and Tertius reports every lookup as approximate until an engineer
marks the region verified in `design.py`.

# Structural restraint evidence packs

`restraint_evidence_packs.json` is the fail-closed registry used to bind exact
rendered component identities to versioned restraint evidence. A pack records
the source-document hash and page scope, exact applicable part numbers,
published resistance/stiffness evidence, assumptions, and exclusions.

The first pack identifies the July 2026 LYSAGHT Zeds and Cees guide and the
`C10019` / `C10012` / `100AC` / `PB1230HS M12x30 grade 8.8` configuration.
The guide does not publish generic portal-rafter restraint resistance or
connection stiffness for that assembly, so those fields deliberately remain
null/unverified. The resolver must not substitute the guide's configuration-
specific transverse purlin capacity tables for the missing restraint evidence.
