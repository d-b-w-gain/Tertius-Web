import os
import shutil
from pathlib import Path

# Paths
SOURCE_DIR = Path(__file__).parent.parent.parent.parent / "tertius"
DEST_DIR = Path(__file__).parent.parent
UI_DEST = DEST_DIR / "ui" / "src" / "workflows"
SERVER_DEST = DEST_DIR / "server" / "workflows"

def copy_workflow(workflow_name):
    print(f"Bundling workflow: {workflow_name}...")
    src_path = SOURCE_DIR / workflow_name
    
    if not src_path.exists():
        print(f"  Warning: Source {src_path} does not exist.")
        return

    # Create destination directories
    ui_out = UI_DEST / workflow_name
    server_out = SERVER_DEST / workflow_name
    ui_out.mkdir(parents=True, exist_ok=True)
    server_out.mkdir(parents=True, exist_ok=True)

    # Copy files based on extension
    for item in src_path.rglob("*"):
        if item.is_dir() or ".git" in item.parts or "node_modules" in item.parts:
            continue
            
        rel_path = item.relative_to(src_path)
        
        # Categorize file
        if item.suffix in ['.tsx', '.ts', '.css', '.svg', '.json'] and not item.name.endswith('.meta.json'):
            # Frontend file
            dest_file = ui_out / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_file)
            print(f"  UI: Copied {rel_path}")
            
        elif item.suffix in ['.py', '.md', '.txt'] or item.name.endswith('.meta.json'):
            # Backend / Server / Meta file
            dest_file = server_out / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_file)
            print(f"  Server: Copied {rel_path}")
            
def main():
    print("Starting Tertius Bundler...")
    
    # Clean previous builds
    if UI_DEST.exists():
        shutil.rmtree(UI_DEST)
    if SERVER_DEST.exists():
        shutil.rmtree(SERVER_DEST)
        
    workflows = ["artus", "extus", "intus", "timus"]
    for wf in workflows:
        copy_workflow(wf)
        
    print("Patching useServerLauncher hooks for web compatibility...")
    mock_src = DEST_DIR / "ui" / "src" / "mockServerLauncher.ts"
    if mock_src.exists():
        for wf in workflows:
            target1 = UI_DEST / wf / "ui" / "ServerLauncher" / "useServerLauncher.ts"
            target2 = UI_DEST / wf / "ui" / "ServerLauncher" / "ServerLauncher" / "useServerLauncher.ts"
            if target1.exists():
                shutil.copy2(mock_src, target1)
            if target2.exists():
                shutil.copy2(mock_src, target2)
                
            # Patch ServerLauncher.tsx missing imports
            sl1 = UI_DEST / wf / "ui" / "ServerLauncher" / "ServerLauncher.tsx"
            sl2 = UI_DEST / wf / "ui" / "ServerLauncher" / "ServerLauncher" / "ServerLauncher.tsx"
            for sl in [sl1, sl2]:
                if sl.exists():
                    txt = sl.read_text(encoding="utf-8")
                    if "import React" not in txt:
                        sl.write_text("import React, { useRef, useState, useEffect } from 'react';\n" + txt, encoding="utf-8")
        
    print("Bundling complete! Files are ready in ui/src/workflows and server/workflows.")

if __name__ == "__main__":
    main()
