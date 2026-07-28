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
