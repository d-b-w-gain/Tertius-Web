import os
import sys
import tempfile
import traceback
from pathlib import Path
from fastapi import FastAPI, Response, Query
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

WORKFLOW_DIR = Path(__file__).parent
CACHE_ROOT = Path(__file__).parent.parent.parent.parent / 'cache' / 'tertius'
PROJECTS_DIR = CACHE_ROOT / 'intus'

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

def _draw_drafting_sheet_background(pdf, title: str, stamp_text: str, show_redline: bool, w: float, h: float):
    # 1. Main outer border frame
    pdf.set_draw_color(15, 15, 17)
    pdf.set_line_width(0.55)
    pdf.rect(10, 10, w - 20, h - 20) 
    
    # 2. Border coordinate ticks
    pdf.set_line_width(0.18)
    section_w = (w - 20) / 4
    for i in range(1, 4):
        x = 10 + i * section_w
        pdf.line(x, 10, x, 7)
        pdf.line(x, h - 10, x, h - 7)
        
    section_h = (h - 20) / 4
    for i in range(1, 4):
        y = 10 + i * section_h
        pdf.line(10, y, 7, y)
        pdf.line(w - 10, y, w - 7, y)
        
    # 3. Coordinate Labels
    pdf.set_font("Courier", "B", 7.5)
    pdf.set_text_color(75, 85, 99)
    cols = ["4", "3", "2", "1"]
    for i in range(4):
        x = 10 + (i + 0.5) * section_w
        pdf.text(x - 0.8, 8.5, cols[i])
        pdf.text(x - 0.8, h - 3.5, cols[i])
        
    rows = ["D", "C", "B", "A"]
    for i in range(4):
        y = 10 + (i + 0.5) * section_h
        pdf.text(5.5, y + 1.2, rows[i])
        pdf.text(w - 5.5, y + 1.2, rows[i])
        
    # 4. Ultra-faint Grid Paper effect
    with pdf.local_context(stroke_opacity=0.012):
        pdf.set_line_width(0.1)
        for x in range(30, int(w) - 10, 20):
            pdf.line(x, 10, x, h - 10)
        for y in range(30, int(h) - 10, 20):
            pdf.line(10, y, w - 10, y)
    pdf.set_draw_color(15, 15, 17)
    
    # 5. Engineering Title Block (Bottom-Right)
    tb_w = 100
    tb_h = 25
    tb_x = w - 10 - tb_w
    tb_y = h - 10 - tb_h
    
    pdf.set_line_width(0.38)
    
    # Outer boundaries of title block (Top and Left)
    pdf.line(tb_x, tb_y, tb_x + tb_w, tb_y)
    pdf.line(tb_x, tb_y, tb_x, tb_y + tb_h)
    
    # Horizontal separators
    pdf.line(tb_x, tb_y + 9, tb_x + tb_w, tb_y + 9)
    pdf.line(tb_x, tb_y + 18, tb_x + tb_w, tb_y + 18)
    
    # Vertical separators for Rows 1 & 2
    pdf.line(tb_x + 50, tb_y, tb_x + 50, tb_y + 18)
    pdf.line(tb_x + 80, tb_y, tb_x + 80, tb_y + 18)
    
    # Vertical separator for Row 3
    pdf.line(tb_x + 35, tb_y + 18, tb_x + 35, tb_y + tb_h)
    
    # Row 1: Drawing Title
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 2, tb_y + 3, "DRAWING TITLE")
    pdf.set_font("Helvetica", "B", 7.8)
    pdf.text(tb_x + 2, tb_y + 7, title.upper())
    
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 52, tb_y + 3, "DOCUMENT NO.")
    pdf.set_font("Helvetica", "B", 7.2)
    pdf.text(tb_x + 52, tb_y + 7, "TERTIUS-DWG-001")
    
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 82, tb_y + 3, "SHEET NO.")
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.text(tb_x + 82, tb_y + 7, "1 OF 1")
    
    # Row 2: Status
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 2, tb_y + 11, "CHECKED BY")
    pdf.set_font("Helvetica", "B", 7)
    pdf.text(tb_x + 2, tb_y + 15, "TERTIUS SYSTEMS ENG")
    
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 52, tb_y + 11, "REVISION STATUS")
    pdf.set_font("Helvetica", "", 7.2)
    pdf.text(tb_x + 52, tb_y + 15, "REV 1.0")
    
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 82, tb_y + 11, "SCALE")
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.text(tb_x + 82, tb_y + 15, "NTS")
    
    # Row 3: Name
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 2, tb_y + 19, "APPLICANT NAME")
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.text(tb_x + 2, tb_y + 23, "PLACEHOLDER NAME")
    
    pdf.set_font("Courier", "B", 5.2)
    pdf.text(tb_x + 37, tb_y + 19, "SYSTEM")
    pdf.set_font("Helvetica", "B", 6.8)
    pdf.text(tb_x + 37, tb_y + 23, "TERTIUS CAD COMPILER")
    
    # Redline Revision Markup
    if show_redline:
        pdf.set_draw_color(255, 82, 82)
        pdf.set_line_width(0.3)
        # Redline strikethrough over REV 1.0
        pdf.line(tb_x + 52, tb_y + 14.0, tb_x + 63, tb_y + 14.0)
        
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(255, 82, 82)
        pdf.text(tb_x + 64, tb_y + 15, stamp_text)
        
        with pdf.rotation(angle=-3, x=tb_x + 40, y=tb_y + 14):
            pdf.set_font("Helvetica", "B", 5.8)
            pdf.rect(tb_x + 38, tb_y + 9, 10, 4)
            pdf.text(tb_x + 39, tb_y + 12, "QTD OK")
            
        pdf.set_draw_color(15, 15, 17)
        pdf.set_text_color(15, 15, 17)

def _draw_gorton_text(pdf, text: str, ox: float, oy: float, size: float = 20):
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.recordingPen import RecordingPen
        from fpdf.enums import StrokeCapStyle, StrokeJoinStyle
        from fpdf.drawing import DeviceRGB
        
        font_path = WORKFLOW_DIR / "www" / "GortonClassicRegular.otf"
        if not font_path.exists():
            raise FileNotFoundError("Font file not found")
            
        font = TTFont(str(font_path))
        glyph_set = font.getGlyphSet()
        cmap = font.getBestCmap()
        
        tracking = 65
        space_width = 250
        baseline = 556
        
        size_mm = size * 25.4 / 72
        scale = size_mm / baseline
        
        ops = []
        current_x = 0.0
        
        for char in text:
            if char == ' ':
                current_x += space_width + tracking
                continue
            
            codepoint = ord(char)
            glyph_name = cmap.get(codepoint)
            if not glyph_name:
                continue
            
            glyph = glyph_set[glyph_name]
            pen = RecordingPen()
            glyph.draw(pen)
            
            for cmd, args in pen.value:
                if cmd == 'moveTo':
                    x, y = args[0]
                    ops.append(('M', (current_x + x) * scale, (baseline - y) * scale))
                elif cmd == 'lineTo':
                    x, y = args[0]
                    ops.append(('L', (current_x + x) * scale, (baseline - y) * scale))
                elif cmd == 'curveTo':
                    (x1, y1), (x2, y2), (x3, y3) = args
                    ops.append(('C', 
                                (current_x + x1) * scale, (baseline - y1) * scale,
                                (current_x + x2) * scale, (baseline - y2) * scale,
                                (current_x + x3) * scale, (baseline - y3) * scale))
            current_x += glyph.width + tracking
            
        subpaths = []
        current = []
        for op in ops:
            if op[0] == 'M' and current:
                subpaths.append(current)
                current = []
            current.append(op)
        if current:
            subpaths.append(current)
            
        for subpath in subpaths:
            with pdf.new_path() as path:
                path.style.stroke_width = 0.38
                path.style.stroke_color = DeviceRGB(15/255, 15/255, 17/255)
                path.style.stroke_cap_style = StrokeCapStyle.ROUND
                path.style.stroke_join_style = StrokeJoinStyle.ROUND
                path.style.auto_close = False
                path.style.fill_color = None
                for op in subpath:
                    if op[0] == 'M':
                        path.move_to(op[1] + ox, op[2] + oy)
                    elif op[0] == 'L':
                        path.line_to(op[1] + ox, op[2] + oy)
                    elif op[0] == 'C':
                        path.curve_to(op[1] + ox, op[2] + oy, op[3] + ox, op[4] + oy, op[5] + ox, op[6] + oy)
                        
    except Exception:
        pdf.set_xy(ox, oy)
        pdf.set_font("Helvetica", "B", size)
        pdf.cell(w=110, h=7, text=text, ln=0)

PROJECTION_CACHE = {}

def get_projected_views(name: str, compound: bd.Compound, mtime: float):
    global PROJECTION_CACHE
    if name in PROJECTION_CACHE:
        cache_mtime, cached_views = PROJECTION_CACHE[name]
        if cache_mtime == mtime:
            return cached_views
            
    views = {}
    bbox = compound.bounding_box()
    look_at = bbox.center()
    max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)
    if max_dim == 0:
        max_dim = 100
        
    for view_name in ["top", "front", "side", "iso"]:
        if view_name == "top":
            origin = (look_at.X, look_at.Y, bbox.max.Z + max_dim)
            up = (0, 1, 0)
        elif view_name == "front":
            origin = (look_at.X, bbox.min.Y - max_dim, look_at.Z)
            up = (0, 0, 1)
        elif view_name == "side":
            origin = (bbox.max.X + max_dim, look_at.Y, look_at.Z)
            up = (0, 0, 1)
        else:
            origin = (look_at.X + max_dim, look_at.Y - max_dim, look_at.Z + max_dim)
            up = (0, 0, 1)
            
        visible, hidden = compound.project_to_viewport(
            viewport_origin=origin,
            viewport_up=up,
            look_at=look_at
        )
        
        segments = []
        if visible:
            proj_comp = bd.Compound(visible)
            v_bbox = proj_comp.bounding_box()
            cx = v_bbox.center().X
            cy = v_bbox.center().Y
            
            def tessellate(edges, is_hidden):
                for edge in edges:
                    gt = edge.geom_type() if callable(edge.geom_type) else edge.geom_type
                    g_type = getattr(gt, "name", str(gt))
                    n_samples = 2 if g_type == "LINE" else 30
                    pts = [edge.position_at(t/(n_samples-1)) for t in range(n_samples)]
                    for i in range(len(pts)-1):
                        dx1 = pts[i].X - cx
                        dy1 = pts[i].Y - cy
                        dx2 = pts[i+1].X - cx
                        dy2 = pts[i+1].Y - cy
                        segments.append(((dx1, dy1), (dx2, dy2), is_hidden))
            
            tessellate(visible, False)
            if hidden:
                tessellate(hidden, True)
                
        views[view_name] = segments
        
    PROJECTION_CACHE[name] = (mtime, views)
    return views

def _draw_compound_view(pdf, segments, ox: float, oy: float, w: float, h: float, scale: float, show_hidden: bool = True):
    if not segments:
        return
        
    screen_cx = ox + w/2
    screen_cy = oy + h/2
    
    # Draw solid lines first
    pdf.set_line_width(0.3)
    pdf.set_draw_color(15, 15, 17)
    pdf.set_dash_pattern()
    for ((dx1, dy1), (dx2, dy2), is_hidden) in segments:
        if not is_hidden:
            pdf.line(screen_cx + dx1 * scale, screen_cy - dy1 * scale, screen_cx + dx2 * scale, screen_cy - dy2 * scale)
            
    # Draw hidden lines
    if show_hidden:
        pdf.set_line_width(0.15)
        pdf.set_draw_color(150, 150, 150)
        pdf.set_dash_pattern(dash=1, gap=1)
        for ((dx1, dy1), (dx2, dy2), is_hidden) in segments:
            if is_hidden:
                pdf.line(screen_cx + dx1 * scale, screen_cy - dy1 * scale, screen_cx + dx2 * scale, screen_cy - dy2 * scale)
                
        pdf.set_dash_pattern()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/projects/{name}/bounds")
def get_project_bounds(name: str):
    script_file = PROJECTS_DIR / name / "design.py"
    if not script_file.exists():
        return Response("Project not found", status_code=404)
        
    try:
        code = script_file.read_text(encoding="utf-8")
        compound = get_compound_from_code(code)
        bbox = compound.bounding_box()
        max_dim = max(bbox.max.X - bbox.min.X, bbox.max.Y - bbox.min.Y, bbox.max.Z - bbox.min.Z)
        if max_dim == 0:
            max_dim = 100
        return {"max_dim": max_dim}
    except Exception as e:
        return Response(f"Internal Server Error: {str(e)}", status_code=500)

@app.get("/projects/{name}/drafting.pdf")
def get_drafting_pdf(
    name: str, 
    title: str = Query("UNTITLED PART"),
    stamp: str = Query("APPROVED"),
    redline: bool = Query(True),
    hidden_lines: bool = Query(True),
    scale: float = Query(1.0),
    size: str = Query("A4")
):
    script_file = PROJECTS_DIR / name / "design.py"
    if not script_file.exists():
        return Response("Project not found", status_code=404)
        
    try:
        mtime = script_file.stat().st_mtime
        code = script_file.read_text(encoding="utf-8")
        compound = get_compound_from_code(code)
        
        # Get cached views
        views = get_projected_views(name, compound, mtime)
        
        # Calculate Dimensions
        size = size.upper()
        formats = {
            "A4": (297, 210),
            "A3": (420, 297),
            "A2": (594, 420),
            "A1": (841, 594),
            "A0": (1189, 841),
        }
        if size not in formats:
            size = "A4"
        w, h = formats[size]
        
        # Initialize Landscape Document
        from fpdf import FPDF
        pdf = FPDF(orientation="landscape", unit="mm", format=(h, w))
        pdf.add_page()
        
        # Draw Background and Title Block
        _draw_drafting_sheet_background(pdf, title=title, stamp_text=stamp, show_redline=redline, w=w, h=h)
        _draw_gorton_text(pdf, "TERTIUS ENGINEERING", 15, 15, size=20)
        
        # View Layout Grid
        pdf.set_font("Helvetica", "B", 10)
        view_w = (w - 60) / 2
        view_h = (h - 60) / 2
        top_ox = 20
        top_oy = 30
        iso_ox = 40 + view_w
        iso_oy = 30
        front_ox = 20
        front_oy = 30 + view_h
        side_ox = 40 + view_w
        side_oy = 30 + view_h
        
        # Draw Direct OCCT Edges to PDF
        _draw_compound_view(pdf, views["top"], ox=top_ox, oy=top_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views["front"], ox=front_ox, oy=front_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views["side"], ox=side_ox, oy=side_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views["iso"], ox=iso_ox, oy=iso_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        
        # Labels
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(150, 150, 150)
        pdf.text(top_ox, top_oy + view_h - 2, "PLAN VIEW")
        pdf.text(front_ox, front_oy + view_h - 2, "FRONT ELEVATION")
        pdf.text(side_ox, side_oy + view_h - 2, "SIDE ELEVATION")
        pdf.text(iso_ox, iso_oy + view_h - 2, "ISOMETRIC VIEW")
            
        pdf_bytes = bytes(pdf.output())
        
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline"}
        )
        
    except Exception as e:
        err = traceback.format_exc()
        print(f"Error generating PDF:\n{err}")
        return Response(f"Internal Server Error: {str(e)}", status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
