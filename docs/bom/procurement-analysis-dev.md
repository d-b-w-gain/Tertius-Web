# Procurement Analysis Dev Guide

This guide is for working on the deterministic procurement analysis package
without running the full app, K3s, a database, or an LLM.

## What This Package Does

The package lives at:

`server/core/procurement_analysis`

It reads:

- a `design.py` entrypoint and local Python imports
- optionally a GLTF scene tree or compiled GLTF model
- optionally explicit `tertius_bom` manifest metadata

It writes a `procurement_analysis.json` artifact with:

- `assemblies`: selectable BoM views/scopes
- `components`: build items that can be linked to source and visuals
- `requirements`: procurement lines with part identity, quantity, dimensions,
  source trace, and resolution trace
- `diagnostics`: missing or unsafe metadata that needs design-source repair

The package must stay deterministic. It should not call LLMs, Kubernetes,
FastAPI, SQLAlchemy, or arbitrary user code for source-analysis tests.

## Quick Start

From the app repo:

```powershell
cd C:\Users\ben\Documents\Projects\Tertius-Web
```

Use a dedicated project folder for each design/job. Do not overwrite a named
fixture such as `3x5shed` with an unrelated concept model; create a new folder
under `C:\Users\ben\ContextUI\default\cache\tertius\intus` instead. The
playground treats the folder name as part of the human review context, so a
wrong folder name makes the analysis misleading even when the JSON is valid.

Prefer the repo venv when it is available:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --project-dir C:\Users\ben\ContextUI\default\cache\tertius\intus\3x5shed `
  --source-only `
  --out C:\tmp\3x5shed-procurement_analysis.json
```

Open the static viewer:

```text
file:///C:/Users/ben/Documents/Projects/Tertius-Web/docs/bom/procurement-analysis-viewer.html
```

Then use the file picker in the page to load:

```text
C:\tmp\3x5shed-procurement_analysis.json
```

Using another Python environment, such as a temporary venv, is acceptable when
it has the required dependencies. The script imports the package from the repo
that contains `scripts/spikes/procurement_analysis_playground.py`, so make sure
the script path points at the intended `Tertius-Web` worktree.

## Source-Only Mode

`--source-only` skips GLTF compile and uses deterministic AST/source evidence.
It is fast and useful while refactoring `design.py`, but it cannot prove visual
linking.

Use it to answer:

- Are the part numbers resolvable from source?
- Are quantities explicit or statically resolvable?
- Are dimensions such as `length`, `length_mm`, `grip_length`, or `angle`
  being converted into requirement dimensions?
- Are functions grouped under sensible source assemblies?

Do not use source-only mode to claim visual verification. If there is no GLTF
tree, the artifact should be treated as source-derived and unverified.

## Full Compile Mode

For visual/build-tree evidence, let the playground compile a temporary GLB:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --design-py C:\Users\ben\ContextUI\default\cache\tertius\intus\3x5shed\design.py `
  --quality sketch `
  --compile-timeout 300 `
  --out C:\tmp\3x5shed-procurement_analysis.json
```

This executes the design in a temporary directory, exports GLB, analyzes the
scene tree, and combines it with AST/source metadata. Use this mode before
trusting assembly/component filters.

Do not pass `--compat-build123d-compound` for current Build123D runtimes unless
you are specifically debugging an old compatibility problem. That rewrite can
flatten the GLB hierarchy and turn a visual-verified run back into diagnostic
source-only evidence.

## 3x5 Shed Golden BoM

The regression suite includes an opt-in whole-shed golden comparison at:

```text
server/tests/test_procurement_shed_golden.py
server/tests/fixtures/procurement/3x5shed_expected_bom.json
```

The expected fixture records the repo commit it was created from, but it starts
with `status: manual_expected_values_pending`. Do not populate it by copying
analyzer output. Fill `line_items` from a manually calculated BoM, then change
the status to `verified`.

Run the shed through the visual playground first:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --design-py C:\Users\dbwga\Documents\Projects\CAD\3x5shed\design.py `
  --quality sketch `
  --compile-timeout 300 `
  --out C:\tmp\3x5shed-procurement_analysis.json
```

Then run the golden comparison against that visual-verified artifact:

```powershell
$env:PYTHONPATH = "server"
$env:TERTIUS_PROCUREMENT_SHED_ANALYSIS_JSON = "C:\tmp\3x5shed-procurement_analysis.json"
.\temp_env\Scripts\python.exe -m pytest server\tests\test_procurement_shed_golden.py -q
```

The test requires `analysis_mode: visual_verified` and
`quantity_authority: visual_tree`. For orderable discrete parts, each visual
component row must have quantity `1` with `quantity_source: visual_instances`.
Non-discrete materials such as concrete volume may use
`quantity_source: metadata_quantity_non_discrete`.

## Existing GLTF Or Tree Fixture

Use an existing text `.gltf` file:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --project-dir C:\path\to\project `
  --gltf C:\path\to\model.gltf `
  --out C:\tmp\procurement_analysis.json
```

Use a simplified tree fixture:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --project-dir C:\path\to\project `
  --tree-json C:\path\to\scene-tree.json `
  --out C:\tmp\procurement_analysis.json
```

The tree fixture is best for unit-style experiments where Build123D export is
not the thing being tested.

## Reading The Counts

The command prints:

```text
Assemblies=N Components=N Requirements=N Diagnostics=N
```

These counts are a smoke test, not a pass/fail definition. They change whenever
the design source changes.

Useful interpretations:

- `Requirements=0`: the design probably has no procurement-readable metadata,
  or all potential items are diagnostics only.
- `Diagnostics>0`: open the JSON and read `diagnostics`; this is usually the
  next design-source repair list.
- Many `(missing part number)` rows: the design is exposing components, but the
  functions need `part_number`, `product_key`, or explicit `tertius_bom`
  metadata.
- Source-only counts lower than full-compile counts: the GLTF/build tree is
  exposing visual structure that pure source analysis cannot prove.

## Design Source Conventions

The analyzer looks for generic procurement metadata. It must not know product
values like `C10019` by hard-coded app rules.

Prefer function signatures and calls like:

```python
def make_member(length, *, part_number="TEST-A", quantity=1, unit="each"):
    ...

left_column = make_member(length=2400, part_number=PURLIN_PART_NUMBER)
```

Useful field names:

- `part_number` or `product_key`
- `quantity`
- `unit`
- `length` or `length_mm`
- `width` or `width_mm`
- `height` or `height_mm`
- `diameter` or `diameter_mm`
- `grip_length` or `grip_length_mm`
- `material`
- `finish`
- `grade`
- `standard`

Assemblies and collections should organize the model. They should not become
procurement rows unless they are explicitly purchasable kits.

Meshes/faces should never become procurement rows.

Labels are useful for the viewer, but labels alone are not part identity.

## Static Resolution Rules

The source resolver can safely resolve:

- literals
- local constants
- imported local constants
- function defaults
- simple arithmetic
- selected `math` functions
- selected safe built-ins such as `sum`, `min`, `max`, `round`, and `abs`

It intentionally refuses arbitrary function execution, object methods,
Build123D runtime values, unknown imports, and third-party lookups. When in
doubt, it should emit diagnostics rather than invent a value.

## Development Loop

Run focused tests:

```powershell
.\temp_env\Scripts\python.exe -m pytest server\tests\test_procurement_analysis.py -q
```

Regenerate a playground artifact:

```powershell
.\temp_env\Scripts\python.exe scripts\spikes\procurement_analysis_playground.py `
  --project-dir C:\Users\ben\ContextUI\default\cache\tertius\intus\3x5shed `
  --source-only `
  --out C:\tmp\3x5shed-procurement_analysis.json
```

Open the static viewer and load the JSON:

```text
file:///C:/Users/ben/Documents/Projects/Tertius-Web/docs/bom/procurement-analysis-viewer.html
```

If the app API is running from the local K3s stack, patch it after analyzer
changes:

```powershell
.\scripts\local-k3s-patch-api.cmd
```

## PR Checklist

- Add a small analyzer unit test for each new rule.
- Avoid product-specific assertions except in fixture examples.
- Keep identity and quantity separate.
- Prefer unresolved diagnostics over fake rows.
- Verify source-only and full-compile modes when changing design-source
  matching or GLTF matching.
