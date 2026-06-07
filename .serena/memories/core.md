# Core

- Modular monolith: `server/` FastAPI mounts workflow sub-apps under `/api/*`; `ui/` React/Vite frontend consumes API via `VITE_API_URL`.
- Runtime filesystem state lives under `cache/tertius`; backend deployment must preserve write compatibility for projects, active pointers, outputs, and git-backed workflow state.
- Deployment design lives at `docs/superpowers/specs/2026-06-07-k8s-deployment-design.md`; Kubernetes work should align with its single-chart `charts/tertius` approach.
- Read `mem:tech_stack` for language/runtime/package manager pins. Read `mem:task_completion` for verification gates.