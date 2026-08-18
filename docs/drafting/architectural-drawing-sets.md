# Architectural Drawing Sets

## Purpose

Timus currently proves that Tertius can project compiled Build123D geometry
into hidden-line vectors and place four fixed views on a PDF sheet. This design
evolves that capability into a model-linked architectural documentation system.

The target is not a general-purpose CAD clone. Timus should be a controlled
sheet composer that derives repeatable views and annotations from the same
canonical project revision consumed by the other workbenches, while retaining
small, explicit author overrides for document composition.

The first acceptance project is the current Class 10a shed. It must produce a
coordinated concept-review set containing a cover/register, site plan, floor
plan, roof plan, four elevations, two sections, and door/window/material
schedules.

## Current State

The present implementation has useful foundations:

- OpenCASCADE hidden-line removal for top, front, side, and isometric views;
- authenticated, tenant-scoped persisted `timus_views` artifacts;
- browser preview and vector PDF rendering;
- A4-A0 sheet sizes, projection scale, line visibility, and basic title data;
- stale-artifact rejection after `design.py` changes.

The first delivery slice makes the saved layout authoritative in preview and
PDF export, supports either a single enlarged view or the established combined
four-view sheet, and defaults review output to `PRELIMINARY` and `NOT FOR
CONSTRUCTION`. It is nevertheless a part-view exporter rather than a
drawing-set system:

- the backend still exports only one sheet from a fixed set of four projections;
- floor plans are top projections, not horizontal cut-plane views;
- there are no configurable sections, crop regions, category filters, levels,
  dimensions, tags, symbols, hatches, schedules, or linked details;
- the title block still contains fixed drawing, sheet, and revision values and
  has no project, author, checker, issue-date, or multi-sheet register data;
- the sheet settings are one project-wide record rather than versioned sheet
  and viewport definitions;
- Timus can still execute source separately instead of consuming all drawing
  inputs from one canonical compile bundle.

## Product Boundary

### In scope

- multi-sheet drawing sets with stable drawing numbers and revisions;
- model-linked plan, roof, elevation, section, detail, and isometric viewports;
- site context from saved parcel, placement, orientation, and level evidence;
- architectural semantics for levels, envelope elements, openings, spaces,
  materials, drainage elements, and schedules;
- deterministic dimensions, levels, grids, tags, symbols, hatches, notes, and
  cross-references;
- explicit author overrides for label position, dimension placement, crop, and
  sheet composition without duplicating the underlying model;
- vector PDF output and a documented interchange path for downstream drafting;
- revision-aware validation, visual QA, and issue-status disclaimers.

### Not in scope for the first epic

- unrestricted line-by-line CAD authoring;
- replacing an architect, surveyor, certifier, or structural engineer;
- inferring architectural meaning from anonymous geometry when semantic data is
  absent;
- automatic planning or code-compliance certification;
- embedding licensed standard content or unverified site evidence;
- treating rendered linework as authoritative when its source revision is
  stale, partial, or inconsistent.

## Core Principle: One Project Revision

Timus must consume a versioned drawing-input artifact from the same successful
compile session that produced the project's physical model and semantic
sidecars. It must not execute `design.py` independently to rediscover geometry.

Every drawing-set artifact records:

- tenant and project scope;
- project revision and source-closure digest;
- physical-model and semantic-manifest digests;
- site-evidence revision where site context is used;
- drawing-contract version and renderer version;
- generation time and issue status;
- warnings, omissions, unsupported features, and author overrides.

If any required input changes, the drawing set becomes stale and cannot be
downloaded as a current issue.

## Versioned Drawing Contract

The first contract should separate document intent from rendered linework.

```text
DrawingSet
  metadata
  sources
  levels[]
  categories[]
  styles
  sheets[]
    sheet metadata and title block
    viewports[]
      view definition
      crop and scale
      visibility rules
      generated geometry reference
      annotations[]
    schedules[]
  issues[]
```

### Sheet

A sheet owns:

- drawing number, title, discipline, paper size, orientation, and sheet index;
- revision, issue status, issue date, author/checker fields, and project data;
- viewport and schedule placement in paper-space millimetres;
- general notes, legends, and revision records.

### View definition

A viewport defines:

- stable ID and type: site, floor plan, roof plan, elevation, section, detail,
  reflected/coordination plan, or isometric;
- model-space origin, direction, up vector, cut plane, depth, and crop;
- reference level and view range where applicable;
- category and object visibility;
- paper scale and display style;
- linked source objects and generated linework digest.

Plan and section views are true cuts. Cut edges, projected edges, hidden lines,
overhead/beyond lines, hatches, and annotations must remain distinguishable.

### Annotation

Annotations use stable model references wherever possible:

- associative linear, angular, radial, ordinate, and level dimensions;
- grids, level markers, north points, scale bars, section/elevation/detail
  references, opening tags, space labels, material callouts, and notes;
- leader and text position overrides stored separately from calculated values;
- explicit orphaned-reference state rather than silently retaining stale text.

### Semantics

The authoring contract needs explicit architectural concepts rather than
geometry-name heuristics:

- site boundary, placement, existing/proposed context, north, and datum;
- levels and finished floor/ground levels;
- slabs, walls/cladding, roofs, ceilings, structural framing, and foundations;
- doors, windows, openings, gutters, downpipes, flashings, and drainage points;
- spaces/uses, materials, finishes, fire/access attributes where supplied;
- stable IDs shared with physical geometry, procurement, structural analysis,
  viewer selection, schedules, and drawing annotations.

Missing required semantics must result in a visible `not documented` or
`incomplete` state, not guessed content.

## View Generation

### Site plan

Compose verified parcel and placement geometry with project outline, existing
context, north point, scale bar, dimensions, setbacks, levels, and nominated
stormwater information. Satellite imagery may be used as review context but is
not survey linework and must be labelled accordingly.

### Floor plan

Use a configurable horizontal cut plane and view range. Represent cut and
projected objects differently, tag openings and spaces, and dimension the
building grid, envelope, openings, and key clearances.

### Roof plan

Expose eaves, ridges, hips/valleys, overhangs, slopes, roof penetrations,
gutters, downpipes, and drainage directions without unrelated framing clutter.

### Elevations

Create named elevations relative to project north and building faces. Include
ground/level lines, height dimensions, openings, roof form, visible materials,
and references to sections/details.

### Sections and details

Support author-positioned cut planes and bounded depth. A section must show cut
geometry, projected context, material hatches, levels, key dimensions, and
referenced detail callouts. Details may derive from a parent view or an explicit
model-space crop at a larger scale.

### Schedules

Generate deterministic drawing, revision, door, window, material, and finish
schedules from semantic objects. Schedule rows keep stable object references so
selection and change diagnostics can link back to the model.

## Authoring Experience

The workbench should have three coordinated surfaces:

1. **Drawing set tree** - sheets, viewports, schedules, issues, and validation.
2. **Sheet canvas** - paper-space placement, crop, scale, and annotation
   overrides.
3. **Properties/inspector** - selected sheet/view/object inputs, provenance,
   warnings, and links to the model/site/structural workbenches.

The first workspace slice establishes this canvas-first shell with collapsible,
resizable navigation and property panels, compact generation/export controls,
display-only sheet zoom, and a focus mode. At compact widths, panels become
on-demand overlays so they do not permanently reduce the drawing surface.

Automatic layout templates create the initial shed set. Authors then make
bounded composition changes without breaking model association. Preview and
export consume the same saved drawing definition and vector renderer.

## First Shed Drawing Set

The first end-to-end fixture is:

| Number | Sheet |
| --- | --- |
| A000 | Cover, project data, drawing register, status and unresolved items |
| A100 | Site plan with parcel, placement, orientation and key setbacks |
| A101 | Dimensioned floor plan and opening tags |
| A102 | Roof and drainage plan |
| A200-A203 | Front, rear, left and right elevations |
| A300 | Transverse section through a portal frame |
| A301 | Longitudinal section through the three portals |
| A600 | Door, window and material schedules |

The set must expose rather than hide the current structural incompleteness. The
middle-portal serviceability failure, provisional bracing, unresolved
connections/foundations, missing site levels, and any geometry/site-coordinate
mismatch appear in A000's unresolved-items schedule until closed by their
owning workflows.

## Validation and Safety

- schema validation rejects duplicate IDs, dangling references, invalid scales,
  impossible crops, and unsupported sheet/view combinations;
- coordinate, orientation, footprint, height, level, and revision mismatches
  are surfaced before issue;
- actual printed scale is tested from model coordinates through PDF output;
- generated PDFs are rendered for visual regression and checked for clipping,
  overlaps, unreadable annotations, missing glyphs, and broken references;
- no title block may claim approval, checking, certification, or construction
  readiness without explicit authorised project data;
- generated concept sets default to `PRELIMINARY - NOT FOR CONSTRUCTION`;
- telemetry uses bounded states and never records project drawings, prompts,
  raw identifiers, addresses, coordinates, or other sensitive/high-cardinality
  content.

## Completion Criteria

The epic is complete when the shed fixture can produce the drawing set above
from one current project revision; preview and exported PDF agree; model edits
update associated views, dimensions, tags, and schedules; stale or incomplete
inputs block a misleading issue; and the rendered set is suitable for an
architect's concept review while clearly identifying engineering and approval
work that remains.
