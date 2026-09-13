# Incremental Component Contract

Date: 2026-07-16

Issue: #311, define the deterministic chunk/component contract for
pre-compile replay.

## Purpose

Incremental pre-compile replay is only safe when Tertius can decide, before
Build123D shape construction, whether a component is identical to a previously
exported artifact. This contract defines the boundary between design code,
runtime provenance, replay eligibility, cache keys, and fallback behavior.

The contract is intentionally conservative. Inferred chunks are allowed only
when provenance proves a direct visual-producing function can be replayed from
resolved inputs. Explicit registration is the preferred production path because
it lets authors declare stable component identity and dependencies instead of
depending on incidental Python structure.

## Terms

- **Component**: a stable, user-meaningful design part that can be independently
  replayed and exported, such as `roof_cladding` or `floor_assembly`.
- **Instance**: one placement of a component in a scene. Multiple instances may
  share one geometry artifact when only placement differs.
- **Chunk**: the transport/export unit. In the pre-compile path, a chunk is the
  artifact for one component geometry plus material/export state. In the
  fallback post-build path, a chunk may be a bounded GLB subtree without compile
  time savings.
- **Replay function**: the callable that returns a Build123D shape or object
  with a `.part` Build123D shape.
- **Pre-export cache key**: the deterministic key computed before replay/export.
  A hit skips both replay and export.
- **Scene manifest**: the bounded compile result that references immutable
  component artifacts and records instance placement, visibility, source links,
  and material bindings.

## Component Identity

Every replayable component has a deterministic `component_id`.

For explicitly registered components:

- `component_id` is author supplied and must be stable across edits.
- It is scoped to the project/design version namespace, tenant, and workflow.
- It must not include raw user IDs, raw project IDs, auth tokens, prompts,
  source text, or unbounded generated hashes in telemetry labels.

For inferred components:

- `component_id` is derived from provenance as
  `inferred:<qualified_function>:<definition_file>:<definition_line>`.
- Inference is safe only for a direct, top-level callable found after
  definition-only loading.
- If the same inferred ID appears more than once with different semantic roles,
  Tertius must require explicit registration or fall back.

## Explicit Registration

Production design code should be able to register components with a small
runtime helper. The exact helper name can be finalized in #312, but the contract
shape is:

```python
register_component(
    component_id="roof.cladding",
    replay=roof_cladding,
    inputs={
        "width": shed_width,
        "length": shed_length,
        "colour": roof_colour,
    },
    dependencies=["roof_profile", "steel_catalog"],
    instances=[
        {
            "instance_id": "roof.cladding.main",
            "placement": roof_location,
            "visible": True,
        }
    ],
)
```

Required fields:

- `component_id`: stable component identity.
- `replay`: callable object or importable qualified function name.
- `inputs`: JSON-like replay inputs. Values must serialize deterministically.
- `dependencies`: declared non-input dependencies that affect geometry,
  material, placement, BoM/source metadata, or export output.

Optional fields:

- `instances`: stable instance IDs and transforms/visibility.
- `material`: material/color binding when it is not already part of inputs.
- `source_scope`: source files or regions that should invalidate the component.
- `fallback_reason`: author-declared reason the component must not be replayed.

Registration wins over inference. If registration and provenance disagree,
Tertius must reject replay for that component and explain the conflict in a
bounded diagnostic.

## Provenance Source Map Inputs

The existing full compile provenance map remains the minimum input for inferred
replay. A replay candidate must reference one `source_calls` record with:

- `returned_visual: true`;
- `id`, `function`, and `qualified_function`;
- `source_file` and `source_line` for the call site;
- `definition_file` and `definition_line` for the replay function;
- `parameters` where every required replay argument has a `resolved` value;
- `standard_inputs` when available for BoM/source navigation;
- GLTF node extras linking exported nodes to `tertiusSourceCallIds`.

The source map is evidence, not authority. It can prove that a previous full
compile returned visual geometry and captured resolved arguments. It cannot by
itself prove that skipped top-level statements are irrelevant, that global
mutation is safe, or that a nested helper call is a stable component boundary.

## Definition-Only Loading

Definition-only loading may keep:

- imports;
- function, class, and constant definitions;
- top-level assignments whose right-hand side has no calls;
- top-level assignments or expressions proven not to call visual-producing
  functions from the previous source map.

Definition-only loading must skip or reject:

- final full-build assignments such as `building = shed_building(...)`;
- `show_object(...)` and viewer/export side effects;
- top-level mutation, augmented assignment, I/O, network access, or runtime
  registration whose effect is required by the selected replay function;
- calls into functions that previously returned visual geometry;
- imports or module side effects that construct the full design.

Every skipped top-level statement must be recorded with bounded metadata:
statement kind, relative file, line number, and a short diagnostic code. Do not
emit full source text in telemetry or logs; local debug manifests may include
source snippets only when they stay on disk and are not uploaded.

If a selected component depends on a skipped statement, replay is unsafe and the
compile falls back.

## Pre-Export Cache Key

The pre-export cache key is schema-versioned and computed from deterministic
inputs only:

- contract schema version;
- component ID and replay function identity;
- replay inputs after canonical JSON serialization;
- explicit dependencies and inferred direct JSON-like globals;
- source digest for the replay function and directly referenced helper
  functions/classes;
- source-map provenance version;
- geometry-affecting runtime versions: Python, Tertius, Build123D/OCP, exporter;
- export format and quality/tessellation settings;
- material/color state when it affects exported output;
- component artifact schema version.

The key must not include:

- wall-clock timestamps;
- absolute local paths;
- raw tenant/user/project IDs;
- auth tokens or secrets;
- prompt text or generated source bodies;
- instance IDs or transforms when geometry/material output is unchanged.

Instance identity is represented in the scene manifest. Geometry identity is
represented by the component artifact key. Placement-only edits should not
invalidate the geometry artifact.

## Artifact Contract

Each component artifact is immutable and content-addressed or version-addressed
behind an access-controlled artifact URL.

Required artifact metadata:

- `artifact_id`;
- `component_id`;
- `cache_key`;
- `digest_sha256`;
- `format` such as `glb`;
- `byte_size` and optional compressed byte size;
- export quality/settings;
- source call IDs and component/source navigation metadata;
- bounded creation diagnostics.

Artifacts are tenant-isolated. Cache lookup must include tenant/workflow scope
or an equivalent authorization boundary even when the content digest matches.

## Scene Manifest Contract

The compile result stream should publish a bounded scene manifest instead of a
large monolithic GLB payload once #314 lands.

Each manifest component entry should include:

- `component_id`;
- `artifact_id` or artifact URL;
- `cache_key` or cache status with a bounded diagnostic;
- source call IDs/source navigation references;
- material binding when not embedded in the artifact.

Each instance entry should include:

- `instance_id`;
- `component_id`;
- transform/placement;
- visibility/selectability;
- BoM/source navigation references;
- stable viewer selection key.

The manifest must be small enough for the compile result stream. Large binary
payloads stay in artifact storage and are fetched separately by Extus.

## Replay Eligibility

A component is replayable when all of these are true:

- the replay function is available after definition-only loading;
- all required replay arguments are resolved and deterministic;
- explicit dependencies are present and deterministic;
- inferred dependencies are limited to JSON-like values or source-hashed helper
  functions/classes;
- skipped top-level statements are not required by the replay function;
- the replay function returns a Build123D shape or object with a Build123D
  `.part`;
- export settings are supported by the component artifact schema.

Any failed eligibility check produces a bounded fallback diagnostic and falls
back to a full compile plus post-build chunking where available.

## Fallback Rules

Tertius must fall back rather than replay when:

- provenance is missing, malformed, stale, or from an incompatible schema;
- any replay argument is unresolved or non-deterministic;
- definition-only load executes unsafe setup or cannot prove skipped setup is
  irrelevant;
- the candidate is a nested/non-importable call without explicit registration;
- a declared dependency is missing;
- cache key computation cannot include a required dependency;
- artifact lookup is unauthorized or inconclusive;
- replay/export fails or returns a non-visual result.

Fallback must preserve existing compile behavior. It may still publish a
post-build chunk manifest for transport and viewer performance, but that
manifest must be marked as `post_build` and must not be reported as compile
time saved.

## Diagnostics And Telemetry

Diagnostics may include bounded labels such as:

- `component_replay_hit`;
- `component_replay_miss`;
- `component_replay_ineligible_unresolved_input`;
- `component_replay_ineligible_skipped_dependency`;
- `component_replay_fallback_full_compile`;
- `component_artifact_export_failed`.

Telemetry must never include secrets, prompts, generated source, uploaded model
files, auth tokens, raw user IDs, raw project IDs, raw job IDs, full component
hashes, or unbounded source-map payloads. Use counts, durations, byte sizes,
schema versions, cache status, and short diagnostic codes.

## Acceptance Criteria For #311

- The production contract distinguishes component geometry identity from scene
  instance identity.
- Explicit registration is the preferred path and inference is only a
  conservative fallback.
- Provenance source-map requirements are tied to existing Tertius fields.
- Pre-export cache key inputs and exclusions are specified.
- Definition-only loading and skipped-statement fallback rules are specified.
- Artifact and scene manifest responsibilities are separated.
- Telemetry constraints match the repository safety rules.

