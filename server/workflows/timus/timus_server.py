import os
import sys
import tempfile
import traceback
from pathlib import Path
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import build123d as bd

app = FastAPI(title="Timus Drafting Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_ROOT = Path(__file__).parent.parent.parent.parent / 'cache' / 'tertius'
PROJECTS_DIR = CACHE_ROOT / 'intus'
ACTIVE_PROJECT = CACHE_ROOT / 'active_project.txt'

def get_compound_from_code(code: str) -> bd.Compound:
    env = {"bd": bd, "build123d": bd}
    exec(code, env)
    
    shapes = []
    for val in env.values():
        if isinstance(val, bd.Shape) and hasattr(val, "volume"):
            g_type = val.geom_type() if callable(val.geom_type) else val.geom_type
            geom_name = getattr(g_type, "name", str(g_type)).upper()
            if geom_name in ("SOLID", "COMPOUND", "OTHER"):
                shapes.append(val)
        elif hasattr(val, "part") and isinstance(getattr(val, "part"), bd.Shape):
            shapes.append(val.part)

    if not shapes:
        if hasattr(bd.BuildPart, "_get_context") and bd.BuildPart._get_context():
            shapes.append(bd.BuildPart._get_context().part)

    if not shapes:
        raise ValueError("No 3D shapes found in script.")

    final_shapes = []
    seen = set()
    for s in shapes:
        if id(s) not in seen:
            seen.add(id(s))
            final_shapes.append(s)

    if len(final_shapes) > 1:
        return bd.Compound(final_shapes)
    return final_shapes[0]


@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/projects/{name}/bounds")
def get_bounds(name: str):
    script_file = PROJECTS_DIR / name / "design.py"
    if not script_file.exists():
        return Response("Project not found", status_code=404)
    try:
        code = script_file.read_text(encoding="utf-8")
        compound = get_compound_from_code(code)
        bbox = compound.bounding_box()
        return {
            "dx": bbox.size.X,
            "dy": bbox.size.Y,
            "dz": bbox.size.Z
        }
    except Exception as e:
        return Response(str(e), status_code=500)

@app.get("/projects/{name}/views/{view_name}.svg")
def get_view_svg(name: str, view_name: str, scale: float = 1.0):
    script_file = PROJECTS_DIR / name / "design.py"
    if not script_file.exists():
        return Response("Project not found", status_code=404)
        
    try:
        code = script_file.read_text(encoding="utf-8")
        compound = get_compound_from_code(code)
        
        # Configure viewport based on standard third-angle views
        # viewport_origin is where the camera is looking FROM.
        bbox = compound.bounding_box()
        # Look at the center of the bounding box
        look_at = bbox.center()
        
        if view_name == "top":
            origin = (look_at.X, look_at.Y, bbox.max.Z + 1000)
            up = (0, 1, 0)
        elif view_name == "north":
            # Front elevation looking from -Y towards +Y
            origin = (look_at.X, bbox.min.Y - 1000, look_at.Z)
            up = (0, 0, 1)
        elif view_name == "east":
            # Side elevation looking from +X towards -X
            origin = (bbox.max.X + 1000, look_at.Y, look_at.Z)
            up = (0, 0, 1)
        else:
            return Response("Invalid view name", status_code=400)
            
        visible, hidden = compound.project_to_viewport(
            viewport_origin=origin,
            viewport_up=up,
            look_at=look_at
        )
        
        exporter = bd.ExportSVG(scale=scale)
        exporter.add_layer("Visible", line_color=(0,0,0), line_weight=0.5)
        exporter.add_layer("Hidden", line_color=(128,128,128), line_weight=0.2, line_type=bd.LineType.DASHED)
        
        exporter.add_shape(visible, layer="Visible")
        exporter.add_shape(hidden, layer="Hidden")
        
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            tmp_path = f.name
            
        exporter.write(tmp_path)
        
        with open(tmp_path, "r", encoding="utf-8") as f:
            svg_content = f.read()
            
        os.remove(tmp_path)
        
        import re
        # Remove any existing width/height
        svg_content = re.sub(r'(<svg[^>]*?)\s+width="[^"]*"', r'\1', svg_content)
        svg_content = re.sub(r'(<svg[^>]*?)\s+height="[^"]*"', r'\1', svg_content)
        
        match = re.search(r'viewBox="([^"]+)"', svg_content)
        if match:
            vb = match.group(1).split()
            if len(vb) >= 4:
                w = float(vb[2])
                h = float(vb[3])
                scaled_w = w * scale
                scaled_h = h * scale
                svg_content = svg_content.replace('<svg ', f'<svg width="{scaled_w}mm" height="{scaled_h}mm" ')
        
        return Response(content=svg_content, media_type="image/svg+xml")
        
    except Exception as e:
        err = traceback.format_exc()
        print(f"Error generating view {view_name}:\n{err}")
        # Return a simple error SVG
        err_svg = f'''<svg width="200" height="50" xmlns="http://www.w3.org/2000/svg">
            <text x="10" y="30" fill="red" font-family="monospace">Error: {str(e)}</text>
        </svg>'''
        return Response(content=err_svg, media_type="image/svg+xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
