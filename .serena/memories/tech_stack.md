# Tech Stack

- Backend: Python FastAPI app in `server/main.py`; server dependencies in `server/requirements.txt`; uvicorn entrypoint supported by `server/main.py`.
- Frontend: React + Vite + TypeScript in `ui/`; npm package manager with `ui/package-lock.json`; scripts include `npm run build`, `npm run lint`, `npm run dev`.
- Existing deployment baseline before k8s work: root `Dockerfile` is API-only Python image. Kubernetes design adds `Dockerfile.api`, `Dockerfile.ui`, and Helm chart under `charts/tertius`.
- Repo instruction: shell commands should be prefixed with `rtk`.