# Extus: The STL Viewer

We are now planning the Extus workflow. This is the isolated, decoupled 3D viewport that only cares about rendering `active_output.stl`.

## User Review Required
> [!IMPORTANT]
> - **Cross-talk via Files:** To keep Extus completely ignorant of Intus's project management, I propose that Intus always writes its compiled STL to a generic, shared file: `C:\Users\ben\ContextUI\default\cache\tertius\active_output.stl`. This means Extus only ever has to watch one file, regardless of what project you are working on in Intus. Does this shared "active output" file approach sound good to you?
> - **The Server:** Because web browsers cannot arbitrarily read files from your C:\ drive, Extus will need a tiny, lightweight Python server (`extus_server.py`) whose only job is to serve that STL file and tell the React app when it has been updated. 

## Proposed Architecture

### 1. The Lightweight File Server (`extus_server.py`)
A minimal FastAPI server purely for watching and serving the STL file to the browser.
- **Shared Target:** Watches `C:\Users\ben\ContextUI\default\cache\tertius\active_output.stl`
- **Endpoints:**
  - `GET /status`: Returns the `Last-Modified` timestamp of `active_output.stl`. 
  - `GET /model`: Returns the raw binary/text STL data.

### 2. The React Frontend (ContextUI Structure)
Following the exact same structure as Intus.
- **`ExtusWindow.tsx`**: The main entry point and shell.
- **`ExtusWindow.meta.json`**: Metadata (perhaps a 3D Cube icon).
- **`ui/SetupTab.tsx`**: Uses `useServerLauncher` to spin up `extus_server.py`.
- **`ui/ViewerTab.tsx`**: 
  - Uses Three.js and `STLLoader`.
  - Sets up beautiful, cinematic lighting and shadows.
  - Implements a polling loop: every 1 second, it checks `/status`. If the timestamp changes, it smoothly swaps out the old 3D mesh for the new one without moving the camera, giving you instant visual feedback when Intus compiles.

## Verification Plan
1. Start the Extus workflow and launch the server.
2. Manually drop an `active_output.stl` file into the cache folder.
3. Verify the ViewerTab loads and renders the 3D model beautifully.
4. Manually overwrite the `.stl` file with a different model while the viewer is running, and verify that the 3D viewport hot-reloads the new model seamlessly without requiring a page refresh.
