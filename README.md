# Tertius

Tertius is an open-source suite of CAD (Computer-Aided Design) workflows and tools. It provides a web-based feature tree, parametric project manager, and 3D viewport, all powered by a robust Python backend executing Build123D scripts.

## Architecture

This project is built using a **Core App + Independent Libraries** pattern:
- **`ui/`**: A React/Vite frontend containing the CAD viewers and semantic feature tree interfaces.
- **`server/`**: A Python-based backend (containerized via Docker) that wraps Build123D to compile parametric geometry scripts and generate STLs/STEPs.

## The Workflows
Tertius currently includes several specialized tools:
- **Artus (The Feature Tree)**: Semantic editor and AI agent interface parsing ASTs.
- **Extus (The STL Viewer)**: A decoupled 3D viewport built on Three.js for hot-reloading geometry.
- **Intus (The Compiler)**: The core engine for compiling CAD scripts and managing projects.
- **Timus**: 2D SVG drafting viewer.

## Getting Started

### Prerequisites
- Docker (for running the backend)
- Node.js (for running the frontend locally during development)

### Running the Backend
*(Instructions for Docker will be added here once containerization is finalized)*

### Running the Frontend
```bash
cd ui
npm install
npm run dev
```

## Contributing
Tertius workflows are developed in isolation. The components you see in `ui/src/workflows` and `server/workflows` are built as modular packages.
