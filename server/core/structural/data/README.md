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

# AS/NZS 1170.2 key-changes table evidence

`as_nzs_1170_2_2021_key_changes_tables.json` is a machine-readable extraction
of the numeric tables visible in the supplied “Key changes to AS/NZS
1170.2-2021” presentation. It includes source-page and file-hash provenance.

This is a secondary summary, not a copy of the licensed Standard. Every value
and its applicability must be checked against the licensed project edition and
amendments before certification. The Site workbench may suggest the Australian
Table 3.2(A) `Md` values and Table 3.3 `Mc` value, but it never marks that
evidence verified automatically. Tables that require opening geometry, surface
zones, loaded areas or dynamic-response inputs are report evidence only until
those inputs are modelled.
