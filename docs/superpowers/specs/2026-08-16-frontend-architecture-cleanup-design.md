# Frontend Architecture Cleanup Design

## Scope

Refactor the four largest frontend composition surfaces without changing routes,
workbench availability, API contracts, polling cadence, scene behavior, or UI
styling. Deliver the work as a stacked four-PR train so every increment is
independently reviewable.

## PR train

1. **App shell** — make `App.tsx` composition-only by extracting navigation,
   About, guest import, sidebar, and workbench-host UI into `ui/src/app/`.
2. **Generate** — separate pure conversation/status helpers and presentational
   panels from `GenerateDesignWindow`; keep job orchestration in the feature.
3. **Artus** — separate tree/model presentation and pure tree/BOM helpers from
   authenticated data and AI-edit orchestration.
4. **Extus** — separate Three.js material/batching helpers and viewer controls
   from the canvas lifecycle component.

Each PR is based on the previous PR's branch. Public component exports remain
compatible so downstream workbenches do not need coordinated changes.

## Boundaries

- `ui/src/app/` owns application-level composition only.
- Workflow-specific modules remain below their existing feature directory.
- Pure functions move to feature-local `model` modules and receive focused unit
  tests.
- Presentational components receive explicit props and do not call APIs.
- Existing hooks and orchestration remain local to their owning workflow; no new
  state-management or data-fetching framework is introduced.
- Direct DOM class mutation in the About menu is replaced with React state.

## Behavior preservation

The tab order, tab labels, access-controlled Site/Structural visibility, guest
workspace import flow, shared Extus viewport coordination, compile/AI polling,
tree selection, scene appearance, and material behavior must remain unchanged.
Existing tests are the primary behavioral contract; new tests cover extracted
boundaries and the React-state About menu.

## Validation

For every PR run focused tests for the touched feature, then:

```bash
cd ui
npm run lint
npm run typecheck
npm test
npm run build
```

The baseline has one unrelated deterministic failure in
`SiteWorkbench.test.tsx` (`object.stream is not a function`) and three lint
warnings outside this train. Each PR must introduce no additional failures or
warnings.
