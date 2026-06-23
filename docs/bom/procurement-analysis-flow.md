# Procurement Analysis Flow

This document describes the deterministic path for turning `design.py` plus a
GLTF scene tree into procurement data. The LLM recovery action in Procurement is
useful assistance, but it is not the primary source of truth. The primary path
should be a compile-produced procurement analysis artifact.

## Compile And Procurement Flow

```mermaid
flowchart TD
  A["design.py source"] --> B["Compile job starts"]
  B --> C["Execute build123D design"]
  C --> D["Export GLTF / GLB model"]
  C --> E["Run AST analysis on design.py and local imports"]

  D --> F["Read GLTF scene tree"]
  E --> G["Extract source metadata"]

  G --> G1["Function calls"]
  G --> G2["Standard inputs"]
  G --> G3["Source trace"]
  G --> G4["Readiness diagnostics"]

  F --> H["Classify GLTF nodes"]
  H --> I{"Node is mesh?"}
  I -->|Yes| M["Mesh / face only"]
  I -->|No| J{"Named group with children?"}
  J -->|No| N["Ignore generated/default node"]
  J -->|Yes| K{"Has named child groups with mesh descendants?"}

  K -->|Yes| O["Assembly view"]
  K -->|No| P{"Has mesh descendants?"}
  P -->|Yes| Q["Component / end item"]
  P -->|No| N

  O --> R["Build assembly hierarchy"]
  Q --> S["Link component to nearest parent assembly"]

  G1 --> T["Match source metadata to component"]
  G2 --> T
  G3 --> T
  G4 --> T
  S --> T

  T --> U{"Identity resolved?"}
  U -->|Yes| V["Create requirement"]
  U -->|Partial| W["Create incomplete requirement + diagnostic"]
  U -->|No| X["Component diagnostic"]

  V --> Y["Group by canonical requirement key"]
  W --> Y
  X --> Z["Diagnostics"]

  R --> AA["procurement_analysis.json"]
  Y --> AA
  Z --> AA
  D --> AB["model.glb"]

  AA --> AC{"Same compile snapshot?"}
  AB --> AC
  AC -->|Yes| AD["Procurement workbench"]
  AC -->|No| AE["Block verification as stale"]

  AD --> AF["Assembly selector"]
  AD --> AG["Grouped BoM rows"]
  AD --> AH["Row highlights components"]
  AD --> AI["Component shows source trace"]
```

## Assembly And Component Classification

```mermaid
flowchart TD
  A["GLTF node"] --> B{"isMesh?"}
  B -->|Yes| C["Mesh / face: no BoM row"]

  B -->|No| D{"Generated/default name?"}
  D -->|Yes| E["Ignore for BoM; keep traversing"]

  D -->|No| F{"Has named child groups with mesh descendants?"}
  F -->|Yes| G["Assembly"]

  F -->|No| H{"Has mesh descendants?"}
  H -->|Yes| I["Component / end item"]

  H -->|No| J["Ignore or diagnostic"]

  G --> K["Selectable BoM view"]
  I --> L["Candidate procurement item"]
  L --> M["Attach AST metadata"]
  M --> N{"Part identity source"}
  N -->|Explicit arg| O["Use supplied part_number"]
  N -->|Resolved constant| P["Use constant value"]
  N -->|Function default| Q["Use default value"]
  N -->|Custom generated| R["Generate deterministic part key"]
  N -->|Unknown| S["Incomplete diagnostic"]
```

## Identity Rules

The analyzer must not know product values such as `C10019`. It must derive them
from the design source.

Resolution order:

1. Explicit call argument, for example `part_number="TEST-A"`.
2. Local constant, for example `PURLIN_PART_NUMBER = "TEST-B"`.
3. Imported local constant, for example `from products import MEMBER_PART`.
4. Function default, only when the call omits the argument.
5. Deterministic generated key for custom components.
6. Diagnostic when identity is still unresolved.

Every resolved value should keep a trace with the raw expression, resolved
value, source file, source line, and resolution method.

## Test Harness

The internal package starts at `server/core/procurement_analysis`.

The initial API is:

- `analyze_design_sources(files: dict[str, str], entrypoint="design.py")`
- `analyze_gltf_tree(gltf: dict)`
- `build_procurement_analysis(source_analysis, tree_analysis, explicit_manifest=None)`

The test suite uses tiny source strings and simplified GLTF trees so it can run
without K3s, a database, LLM configuration, or executing `design.py`.

Fixture cases intentionally use names such as `TEST-A`, `TEST-B`,
`TEST-IMPORTED`, and `TEST-DEFAULT` to avoid overfitting to one shed design or
one product number.

## Current Direction

The Procurement UI can temporarily infer draft rows from live GLTF and Artus
metadata, but the production path should move this logic into compile and store
a `procurement_analysis.json` artifact beside the model artifact.
