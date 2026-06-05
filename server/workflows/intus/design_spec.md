# Intus: The CAD Compiler & Project Manager

We are now kicking off the implementation of the Intus workflow. This is the core engine that will execute the `build123d` Python scripts and manage your project files on disk.

## User Review Required
> [!IMPORTANT]
> - **Code Editor in Intus?** Should the Intus UI include a raw Python code editor so you can manually tweak the script, or should it purely be a "Project Manager" panel (list of projects, New/Load buttons, Compile button, Export format)? I plan to include a basic code editor to give you full control.
> - **Default Purlin Script:** The default seeded script will create a Lysaght Cee Purlin with variables `length`, `depth`, `flange`, `lip`, and `thickness`. I will write this using standard `build123d` geometry. 

## Proposed Architecture

### 1. The FastAPI Server (`intus_server.py`)
This server acts as the file manager and compilation engine.
- **Project Directory:** `../../cache/tertius/intus/[project_name]\`
- **Default Seeding:** On startup, if no projects exist, it creates `default_purlin` and writes a robust Lysaght Cee Purlin `design.py` into it.
- **Endpoints:**
  - `GET /projects`: Lists all projects.
  - `POST /projects/{name}/new`: Creates a new project with the default script.
  - `GET /projects/{name}/code`: Retrieves `design.py`.
  - `POST /projects/{name}/save`: Saves changes to `design.py`.
  - `POST /projects/{name}/compile`: 
    - Dynamically executes `design.py`.
    - Automatically collects all generated `build123d` shapes.
    - Exports them to `output.stl` or `output.step` (based on user request) in the project folder.

### 2. The React Frontend (ContextUI Structure)
We will strictly maintain the standard ContextUI workflow architecture where the Window acts as a router, and specific features are isolated into tabs inside a `ui/` directory.

- **`IntusWindow.tsx`**: The main entry point. Manages state (active tab) and the layout shell.
- **`IntusWindow.meta.json`**: Metadata for the ContextUI launcher (icon, color).
- **`ui/SetupTab.tsx`**: Uses `useServerLauncher` to start `intus_server.py` and display connection logs.
- **`ui/CompilerTab.tsx`**: 
  - **Top Bar**: Project Dropdown (switch projects), "New Project" button, Export Format Dropdown, "Compile & Export" button.
  - **Main Area**: A clean code editor for `design.py` and a console output panel for tracebacks/success logs.

## Verification Plan
1. We will start the Intus workflow in ContextUI.
2. We will verify the `SetupTab` correctly launches the Python server.
3. We will switch to the `CompilerTab` and verify it automatically seeded the `default_purlin` project.
4. We will click "Compile" and verify that an `output.stl` file is successfully generated in the cache folder.
5. We will introduce a syntax error into the script, click Compile, and verify the traceback is displayed cleanly in the UI.
