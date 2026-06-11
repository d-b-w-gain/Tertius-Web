<div align="center">
  <img src="assets/hero.png" alt="Tertius Hero Banner" width="100%" style="border-radius: 8px;" />

  # Tertius CAD

  **An open-source suite of next-generation CAD workflows and engineering tools.**

  <p>
    <a href="#architecture">Architecture</a> •
    <a href="#the-workflows">Workflows</a> •
    <a href="#getting-started">Getting Started</a> •
    <a href="#development">Development</a>
  </p>

</div>

---

Tertius is a robust, modular ecosystem for computational design and CAD engineering. It provides a web-based feature tree, a parametric project manager, and a fast 3D viewport—all backed by a powerful Python backend executing `Build123D` scripts. 

## 📸 Screenshots

| The Semantic Feature Tree (Artus) | The Realtime Viewport (Extus) |
| :---: | :---: |
| <img src="assets/Artus.png" width="400" /> | <img src="assets/Extus.png" width="400" /> |

| The Project Compiler (Intus) | 2D Drafting (Timus) |
| :---: | :---: |
| <img src="assets/Intus.png" width="400" /> | <img src="assets/Timus.png" width="400" /> |

---

## 🏗 Architecture

This project strictly adheres to a **Modular Monolith** pattern:
- **`ui/`**: A blazing fast React + Vite frontend leveraging Tailwind CSS v4. Contains the CAD viewers, node trees, and semantic interfaces.
- **`server/`**: A containerized Python backend (FastAPI) that dynamically wraps `Build123D` to compile parametric geometry scripts, calculate bounding boxes, and stream STLs/STEPs to the frontend.

## 🛠 The Workflows

Tertius currently bundles four specialized, highly decoupled workflows:

- 🌳 **Artus (The Feature Tree)**: Semantic code-editor interface that generates ASTs and links directly to AI agents.
- 👁 **Extus (The Viewport)**: A lightweight, performant 3D canvas built on Three.js, capable of hot-reloading geometry streams.
- ⚙️ **Intus (The Compiler)**: The core build engine. Parses projects, executes isolated Python sandboxes, and exports mesh data.
- 📐 **Timus (The Draftsman)**: A robust OpenCASCADE to PDF 2D drafting layout engine.

---

## 🚀 Getting Started

### Prerequisites
- **Docker** (for hosting the CAD backend cleanly)
- **Node.js 20+** (for frontend development)

### 1. Launching Postgres, Keycloak, and NATS

Local development uses Postgres for app data, Keycloak for login, and NATS JetStream for future asynchronous workflow handoff. Start the stack dependencies from the repository root:

```bash
docker compose up -d postgres keycloak nats
```

Keycloak imports the `tertius` realm on startup. The frontend client is `tertius-web`, and the demo login is:

```text
demo / demo
```

Copy the server environment template and run the database migration before starting the API locally:

```bash
cp server/.env.example server/.env
cd server
alembic upgrade head
python main.py
```

The important server values are:

```bash
DATABASE_URL=postgresql+psycopg://tertius:tertius@localhost:5432/tertius
KEYCLOAK_ISSUER=http://localhost:8080/realms/tertius
KEYCLOAK_AUDIENCE=tertius-web
NATS_URL=nats://localhost:4222
ALLOWED_ORIGINS=http://localhost:5173
```

Generated workflow artifacts are stored in Postgres; run Alembic migrations before compiling or serving artifacts.
NATS monitoring is available locally at `http://localhost:8222`. The current application only receives `NATS_URL`; producers, consumers, stream definitions, and NATS authentication are intentionally deferred until a concrete workflow depends on them.

For the frontend, copy `ui/.env.example` or set:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_KEYCLOAK_AUTHORITY=http://localhost:8080/realms/tertius
VITE_KEYCLOAK_CLIENT_ID=tertius-web
```

### 2. Launching the Backend (Docker)

The server relies on several internal X11 dependencies (like `libxrender1`) to render geometry headlessly in `OCP`. To prevent cluttering your local machine, run it in Docker:

```bash
docker build -t tertius-server .
docker run -p 8000:8000 tertius-server
```
*The API will be available at `http://localhost:8000/docs`.*

### 3. Launching the Frontend

The UI uses Vite for lightning-fast Hot Module Replacement.

```bash
cd ui
npm install
npm run dev
```
*The UI will be accessible at `http://localhost:5173`.*

---

## 🤝 Development & Contribution

Because Tertius workflows are heavily integrated into other tools (like `ContextUI`), this repository operates as a **bundle target**. 

If you are a core contributor modifying the upstream source files, use the included build script to synchronize and patch the codebase for web distribution:

```bash
python scripts/bundle.py
```
> **Note:** The bundle script automatically injects the web-safe `mockServerLauncher.ts` into the workflows, preventing local desktop dependencies from leaking into the React application. 

### Local Python tests with UV

Use UV for the local Python test environment. The dependency source of truth remains `server/requirements.txt`.

```bash
UV_CACHE_DIR=.uv-cache uv venv
UV_CACHE_DIR=.uv-cache uv pip install -r server/requirements.txt
UV_CACHE_DIR=.uv-cache uv run pytest
```

The integration tests use testcontainers and require Docker socket access.

## Kubernetes Deployment Test

The local k3s deployment harness expects an already-running k3s-compatible cluster, Helm, Docker, the CloudNativePG CRD `clusters.postgresql.cnpg.io`, and the Keycloak Operator CRD `keycloaks.k8s.keycloak.org`. It builds the API and UI images, makes them available to k3s, updates chart dependencies when vendored archives are incomplete, installs or upgrades the `infra/charts/tertius` Helm release, waits for app, Postgres, Valkey, NATS, Keycloak, and optional tunnel resources, then runs HTTP and in-cluster smoke checks including a JetStream health check.

```bash
scripts/test-k3s-deployment.sh
```

Tunnel-enabled run:

```bash
ENABLE_TUNNEL=true TUNNEL_TOKEN_SECRET_NAME=cloudflared-token scripts/test-k3s-deployment.sh
```

Useful overrides include `NAMESPACE`, `RELEASE_NAME`, `API_IMAGE`, `UI_IMAGE`, `TUNNEL_HOSTNAME`, and `KEYCLOAK_REALM`. Use `scripts/test-k3s-deployment.sh --cleanup` to uninstall the Helm release while preserving database and cache PVCs plus CloudNativePG data; add `--delete-data` only when those PVCs and database clusters should also be removed. The API no longer owns an artifact PVC.

If the cluster is already running a Flux-managed `tertius` release, run local smoke tests against an isolated release so Flux does not reconcile the test deployment mid-run:

```bash
NAMESPACE=tertius-smoke RELEASE_NAME=tertius-smoke scripts/test-k3s-deployment.sh
```

## License
MIT License.
