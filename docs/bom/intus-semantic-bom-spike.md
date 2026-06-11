# Intus Semantic BoM Metadata Spike

Issue: #73

## Goal

Verify whether Intus design scripts contain enough structured information to help a future BoM tab link design components to sourceable products without inferring from raw GLTF mesh nodes.

The first probe target is:

`C:\Users\ben\ContextUI\default\cache\tertius\intus\3x5shed\design.py`

## Spike Utility

`scripts/spikes/bom_metadata_probe.py` parses Python source with `ast` and does not execute `design.py`.

It inspects:

- `make_*` calls, including nested calls such as `bd.add(make_purlin(column_height))`
- function signatures from sibling project files such as `fasteners.py` and `library.py`
- positional arguments mapped back to parameter names
- `bd.Compound(..., label="...")` labels
- a first-pass BoM readiness result

## Initial Findings

Running the probe against `3x5shed` found:

- `make_fastener_assembly("M12", 25.0, 4.9)` can be mapped to `size`, `length`, and `grip_length` from `fasteners.py`.
- `make_purlin(column_height)`, `make_purlin(rafter_length)`, and `make_purlin(fascia_length)` can be found, but only expose `length`.
- The purlin calls are close to BoM-readable, but need a product key such as `part_number="Z10010"` or a manufacturer-backed library lookup.
- Build123D labels such as `Left Column`, `Fasteners`, and `Portal Frame` are useful visual anchors, but they do not carry enough sourcing information by themselves.

Example probe output:

```text
WARNING line 197 make_portal::column -> -: make_purlin(length) kind=structural_member missing=part_number
WARNING line 204 make_portal::rafter -> -: make_purlin(length) kind=structural_member missing=part_number
OK      line 240 make_portal -> fastener_base: make_fastener_assembly(size, length, grip_length) kind=fastener_assembly missing=-
WARNING line 282 make_fascia -> -: make_purlin(length) kind=structural_member missing=part_number
OK      line 297 make_fascia_brackets -> fastener_base: make_fastener_assembly(size, length, grip_length) kind=fastener_assembly missing=-
```

## Design Direction

Design items and procurement items must remain separate.

For example, a fastener assembly in the CAD model may represent one bolt and one nut at a connection. The BoM tab should be able to decompose and source that as separate supplier lines, packs, boxes, kits, or manually selected equivalents.

Library functions should therefore expose sourcing clues, not final purchasing decisions:

- function name
- component label
- product family or standard
- manufacturer
- part number or product key
- dimensions used by geometry
- repeated quantity context
- decomposition hints where an assembly contains purchasable sub-items

The BoM tab should turn these design items into procurement lines, remember confirmed supplier mappings, and suggest those mappings next time.

## Suggested Standard Parameter Names

Prefer these names in BoM-aware design/library functions:

- `part_number`
- `product_key`
- `manufacturer`
- `standard`
- `size`
- `length_mm`
- `width_mm`
- `height_mm`
- `thickness_mm`
- `diameter_mm`
- `grip_length_mm`
- `quantity`
- `unit`
- `role`
- `material`
- `finish`
- `source_library`

Existing names such as `length` and `grip_length` can be detected, but BoM linting should recommend unit-explicit names over time.

## Next Verification Step

Use the probe output to design a BoM lint/check panel that can report:

- `ok`: enough metadata to suggest a BoM match
- `info`: label or function signal exists, but not enough for sourcing
- `warning`: likely BoM item missing key fields such as `part_number`, `product_key`, or `length_mm`
- `error`: invalid metadata such as impossible dimensions or contradictory product keys

The warning text should be suitable for an Artus/Intus repair prompt.
