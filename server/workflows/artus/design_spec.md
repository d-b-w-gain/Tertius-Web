# Artus: The AI Feature Tree Editor

We are now planning the Artus workflow. This is the intelligent editor that understands your Python script, extracts parameters, and uses an LLM to rewrite code based on your intent.

## User Review Required
> [!IMPORTANT]
> - **Cross-talk via Files:** Just like Extus, Artus needs to know what you are currently working on. I propose Intus writes the path of the current project to a file: `C:\Users\ben\ContextUI\default\cache\tertius\active_project.txt`. Artus will simply read this file, find the real `design.py`, and edit it directly. This guarantees Artus is always editing whatever project you have open in Intus.
> - **LLM API Selection:** The core of Artus is the `/ai_modify` endpoint. We need to decide which LLM API (e.g., OpenAI, Gemini, or a local model like Ollama) the server should call to perform the code rewrites. What is your preference?

## Proposed Architecture

### 1. The FastAPI Server (`artus_server.py`)
This server acts as the AI bridge and code parser.
- **AST Parser:** Reads the active `design.py` using Python's `ast` module. It extracts top-level variables (e.g., `length = 2400.0`, `thickness = 1.9`) and formats them into a structured JSON list.
- **Endpoints:**
  - `GET /features`: Returns the AST-extracted Feature List.
  - `POST /ai_modify`: Accepts a natural language prompt (e.g., "Change the length to 3000 and add a 10mm hole in the center"). It sends the prompt + the original `design.py` to the LLM, receives the new code, and overwrites the `design.py` file on disk.

### 2. The React Frontend (ContextUI Structure)
- **`ArtusWindow.tsx`**: The main entry point and shell.
- **`ArtusWindow.meta.json`**: Metadata (perhaps a Tree or Robot icon).
- **`ui/SetupTab.tsx`**: Uses `useServerLauncher` to spin up `artus_server.py`.
- **`ui/FeatureTreeTab.tsx`**: 
  - **The Feature List:** Displays the variables dynamically extracted from the Python script as a clean list of editable parameters.
  - **The Intent Input:** A text box where you can type natural language instructions for the LLM.
  - **The Apply Button:** Sends your requested changes to the server, showing a loading spinner while the LLM rewrites the code.
  
*(Because Artus edits `design.py` on disk, the moment the LLM finishes, Intus will automatically detect the file change, recompile it, and Extus will automatically render the new 3D model!)*

## Verification Plan
1. Start the Artus workflow and launch the server.
2. Ensure Intus is running and has a project open.
3. Verify the FeatureTreeTab accurately displays the variables from the `design.py` script.
4. Submit a change request (e.g., "Change the lip length to 30.0").
5. Verify the LLM successfully rewrites the script, and that Intus and Extus react instantly to the new file.
