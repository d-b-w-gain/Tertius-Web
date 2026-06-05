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

*(Replace the placeholders below with actual screenshots of the UI once you are happy with it!)*

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

### 1. Launching the Backend (Docker)

The server relies on several internal X11 dependencies (like `libxrender1`) to render geometry headlessly in `OCP`. To prevent cluttering your local machine, run it in Docker:

```bash
docker build -t tertius-server .
docker run -p 8000:8000 tertius-server
```
*The API will be available at `http://localhost:8000/docs`.*

### 2. Launching the Frontend

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

## License
MIT License.
