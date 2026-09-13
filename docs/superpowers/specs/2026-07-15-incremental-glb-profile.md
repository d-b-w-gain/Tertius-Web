# Incremental Pre-Compile Chunk Replay Spike

Date: 2026-07-16

Issue: #309, prototype incremental chunk replay before full design compile.

## Summary

The post-build chunking pass was still off target for the main performance goal. It split a GLB/artifact after executing the full `design.py`, so unchanged walls, floors, frames, and cladding still had to be constructed before any cache decision could matter.

The corrected target is pre-compile chunk replay:

1. use provenance from a previous full run to identify visual-producing function calls;
2. load the design in config-only mode, keeping definitions and non-visual
   top-level configuration while skipping final top-level construction such as
   `building = shed_building(...)`;
3. replay only selected chunk-producing calls with their resolved arguments;
4. export only those replayed chunks;
5. use a pre-export cache key to skip replay/export for unchanged chunks in production.

This is the spike that can answer whether an unchanged component can avoid being recomputed.

## What Was Wrong With The Previous Rerun

`scripts/spikes/profile_chunked_glb_manifest.py` demonstrates useful artifact chunking, but it still:

- executes the full design;
- constructs all unchanged geometry;
- only then splits root children into separate GLBs;
- only then computes digest/cache status.

That can reduce transport and viewer work. It does not save Build123D construction time.

Keep it as a supporting artifact-shape probe, not as the answer to incremental compile.

## Corrected Prototype

Added `scripts/spikes/profile_precompile_chunk_replay.py`.

It tests a narrower but more relevant mechanism:

1. stage the project files;
2. read a provenance source map from a previous compile;
3. select a visual-producing call by `--function` or `--call-id`;
4. statically transform the entrypoint into definition-only source;
5. keep imports, constants, functions, and classes;
6. skip top-level call statements/assignments that would build the full model;
7. execute only that config-only module;
8. call the selected function with previously resolved provenance arguments;
9. export the returned Build123D shape as a chunk GLB;
10. write `precompile-replay-manifest.json`.

Example:

```powershell
python scripts/spikes/profile_precompile_chunk_replay.py `
  --project-dir .tmp/issue-309/3x5shed `
  --entrypoint design.py `
  --source-map .tmp/issue-309/full-run-source-map.json `
  --function roof_cladding `
  --output-dir .tmp/issue-309/precompile-roof `
  --quality rough
```

## Synthetic Proof

The synthetic fixture intentionally makes full top-level construction fail:

```python
def build_everything():
    raise RuntimeError("top-level full build should not run in definition-only replay")

building = build_everything()
```

The replay script skipped that top-level assignment and successfully replayed only:

```python
make_roof(width=900, colour="red")
```

Smoke result:

```json
{
  "selected_call_count": 1,
  "replayed_chunk_count": 1,
  "total_replay_seconds": 0.0017609999995329417,
  "total_export_seconds": 0.012709399998129811,
  "total_raw_glb_bytes": 3348,
  "top_level_statements_skipped": 1
}
```

This proves the mechanism can avoid full top-level design construction for well-structured functions with resolved arguments.

## Real 3x5 Shed Proof

The corrected prototype was also run against the reference design at:

```text
C:\Users\dbwga\Documents\Projects\CAD\3x5shed
```

First, a full provenance/chunk pass produced a real source map and artifact
chunks:

```powershell
python scripts/spikes/profile_chunked_glb_manifest.py `
  --project-dir C:\Users\dbwga\Documents\Projects\CAD\3x5shed `
  --entrypoint design.py `
  --quality sketch `
  --output-dir .tmp/issue-309/real-shed-full `
  --chunk-selection root-children `
  --stage-mode root-py `
  --timeout-seconds 1200
```

Real full-run result:

```json
{
  "design_execution_shape_construction_seconds": 62.735679600002186,
  "chunk_count": 25,
  "total_raw_glb_bytes": 47180760,
  "sequential_export_seconds": 15.283737099991413
}
```

The real provenance map contained 608 visual source calls. Replaying selected
real shed calls succeeded without running the final top-level build:

| Call | Function | Replay seconds | Export seconds | Raw GLB bytes |
| --- | --- | ---: | ---: | ---: |
| `call_665` | `roof_cladding` | 0.571871 | 0.357193 | 2,564,032 |
| `call_1033` | `long_wall_cladding` | 0.593662 | 0.325749 | 3,550,048 |
| `call_1245` | `roof_battens` | 0.847072 | 1.201222 | 3,396,664 |
| `call_2180` | `internal_long_wall_osb_lining` | 6.351361 | 5.895454 | 20,160,252 |
| `call_1672` | `floor_assembly` | 14.116537 | 4.440343 | 13,552,644 |

For the roof replay, the skipped top-level statements were:

```text
building = shed_building(...)
if "show_object" in locals(): ...
```

That is the central thesis: the selected chunks were built independently from
the real design without constructing the full shed.

## Pre-Export Cache Key

The prototype computes a cache key before export from:

- function name;
- qualified function name when provenance identifies an imported module
  function;
- resolved arguments from provenance;
- quality/export settings;
- selected function source;
- direct JSON-like global dependencies used by that function;
- direct helper function/class source dependencies used by that function.

That is the part that can save time: if the pre-export key matches an existing artifact, production can skip both replay and GLB export for that chunk.

For `call_665` / `roof_cladding`, the chunk-local key behaved as intended:

| Scenario | Pre-export key | GLB digest | Result |
| --- | --- | --- | --- |
| baseline | `6eb69b33327d...` | `ff3662f5b85c...` | baseline |
| wall color changed only | `6eb69b33327d...` | `ff3662f5b85c...` | unchanged roof chunk can be skipped |
| roof color changed | `c8389129106b...` | `c903eac1b379...` | roof chunk invalidated |

## Limits

This cannot be reliable for arbitrary Python without constraints. It is plausible for Tertius designs that are structured as import/constants/function definitions plus a final top-level build call.

Known hard cases:

- imports with side-effectful full builds;
- top-level mutation required by chunk functions;
- function calls whose arguments are unresolved objects rather than JSON-like
  values; the prototype now rejects those calls before replay;
- chunk functions that depend on globals changed by skipped top-level statements;
- globals or helper dependencies that are not visible through direct function
  bytecode names;
- nested calls where the desired chunk is not a directly callable global function.

The production design should therefore be conservative:

1. use precompile replay only when the provenance and definition-only load are safe;
2. fall back to full compile plus artifact chunking when safety checks fail;
3. encourage explicit chunk registration to remove guesswork.

## Validation

Completed for the corrected precompile replay prototype:

- `python -m py_compile scripts/spikes/profile_precompile_chunk_replay.py` passed.
- `python -m pytest scripts/tests/test_profile_precompile_chunk_replay.py -q` passed.
- Synthetic Build123D replay skipped a failing top-level full build and exported only the selected chunk.
- Real 3x5 shed `roof_cladding`, `long_wall_cladding`, `roof_battens`,
  `internal_long_wall_osb_lining`, and `floor_assembly` calls replayed as
  chunks without running `shed_building`.
- Roof chunk cache key stayed stable for an unrelated wall-color edit and
  changed for a roof-color edit.

Previously completed for the supporting post-build artifact chunker:

- `python -m pytest scripts/tests/test_profile_chunked_glb_manifest.py -q` passed, 4 tests.
- `python -m py_compile scripts/spikes/profile_chunked_glb_manifest.py` passed.
- Rough chunked pass against the pinned shed fixture.
- Rough unchanged second pass with 20/20 artifact cache hits.
- Rough targeted roof-colour change pass with 19 artifact hits and one changed roof cladding chunk.
- Low chunked pass identifying oversized natural chunk boundaries.

## Next Work

1. Extract the real shed source map from a full provenance run.
2. Attempt precompile replay for specific shed calls such as roof/wall cladding functions.
3. Compare full compile time vs selected replay time for a targeted change.
4. Define the minimum “chunkable design” rules needed for safe replay.
5. Add explicit component registration so cache keys are owned by Tertius policy, not inferred from incidental Python structure.

## Conclusion

Post-build GLB chunking is not enough. It helps artifact transport, but it does not stop unchanged geometry being recomputed.

The corrected spike now has a working proof for definition-only replay of selected visual-producing functions. That is the path that can save compile time when a small part of a design changes, provided the design is structured safely or Tertius adds explicit chunk registration.
