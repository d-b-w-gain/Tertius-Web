from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, confloat, constr
from sqlalchemy import select, desc
from sqlalchemy.orm import Session
import json

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db, SessionLocal
from core.models import ProjectFile, TimusSettings, UserWorkspaceState, Project, CompileJob, Artifact
from core.repositories import ProjectRepository, CompileRepository
from core.compile_runtime import hydrate_project_files
from core.compile_sandbox import run_compile_sandbox
from core.artifacts import ArtifactStore
from core.config import get_settings

app = FastAPI(title="Timus Drafting Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKFLOW_DIR = Path(__file__).parent


def get_active_project(db: Session, ctx: AuthContext) -> Project | None:
    state = db.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == ctx.user_id,
            UserWorkspaceState.tenant_id == ctx.tenant_id,
        )
    )
    if state is None or state.active_project_id is None:
        return None
    return db.scalar(
        select(Project).where(
            Project.tenant_id == ctx.tenant_id,
            Project.id == state.active_project_id,
        )
    )

def _get_project_design_file(name: str, ctx: AuthContext, db: Session) -> ProjectFile | None:
    try:
        project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    except ValueError:
        return None
    if project is None:
        return None

    return db.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == ctx.tenant_id,
            ProjectFile.project_id == project.id,
            ProjectFile.filename == "design.py",
        )
    )




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

class TimusSettingsRequest(BaseModel):
    title: constr(min_length=1, max_length=255)
    stamp_text: constr(min_length=1, max_length=32)
    show_redline: bool
    show_hidden_lines: bool
    scale: confloat(gt=0, le=1000)
    sheet_size: Literal["A4", "A3", "A2", "A1", "A0"]


def serialize_timus_settings(settings: TimusSettings):
    return {
        "title": settings.title,
        "stamp_text": settings.stamp_text,
        "show_redline": settings.show_redline,
        "show_hidden_lines": settings.show_hidden_lines,
        "scale": float(settings.scale),
        "sheet_size": settings.sheet_size,
    }


def _background_build_timus_views(tenant_id: str, user_id: str, project_id: str, name: str):
    db = SessionLocal()
    try:
        repo = ProjectRepository(db, tenant_id)
        compile_repo = CompileRepository(db, tenant_id)
        files = repo.files_for_runtime(name)
        if not files:
            return
            
        job = compile_repo.start_job(project_id, user_id, "timus_views")
        job_id = job.id
        db.commit()
        
        with hydrate_project_files(files) as project_dir:
            settings_path = project_dir / "settings.json"
            import json
            
            settings = db.scalar(
                select(TimusSettings).where(
                    TimusSettings.user_id == user_id,
                    TimusSettings.tenant_id == tenant_id,
                    TimusSettings.project_id == project_id,
                )
            )
            settings_dict = serialize_timus_settings(settings) if settings else {
                "title": name.upper(),
                "stamp_text": "APPROVED",
                "show_redline": True,
                "show_hidden_lines": True,
                "scale": 1.0,
                "sheet_size": "A4",
            }
                
            settings_path.write_text(json.dumps(settings_dict))
            
            result = run_compile_sandbox(project_dir, "timus_views", timeout_seconds=300)
            if not result.success:
                error = result.error or result.stderr or "Compile failed"
                persisted_job = db.get(CompileJob, job_id)
                if persisted_job:
                    compile_repo.finish_job(persisted_job, "failed", error=error)
                    db.commit()
                return

            if result.output_path is None:
                return
            output_bytes = result.output_path.read_bytes()

        artifact_store = ArtifactStore(get_settings().artifact_root)
        stored = artifact_store.write_bytes(tenant_id, project_id, "timus_views", output_bytes)
        
        compile_repo.record_artifact(
            project_id,
            job_id,
            "timus_views",
            stored.storage_key,
            stored.content_type,
            stored.byte_size,
        )
        persisted_job = db.get(CompileJob, job_id)
        if persisted_job:
            compile_repo.finish_job(persisted_job, "succeeded")
            db.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


@app.post("/projects/{name}/drafting/build")
def trigger_drafting_build(
    name: str,
    background_tasks: BackgroundTasks,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db)
):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if not project:
        return Response("Not found", 404)
        
    compile_repo = CompileRepository(db, ctx.tenant_id)
    # Check if a build is already running
    running_job = db.scalar(
        select(CompileJob).where(
            CompileJob.tenant_id == ctx.tenant_id,
            CompileJob.project_id == project.id,
            CompileJob.export_format == "timus_views",
            CompileJob.status == "running"
        )
    )
    if running_job:
        return {"status": "building"}
        
    background_tasks.add_task(
        _background_build_timus_views,
        ctx.tenant_id,
        ctx.user_id,
        project.id,
        name
    )
    return {"status": "started"}

@app.get("/projects/{name}/drafting/status")
def get_drafting_status(
    name: str,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db)
):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if not project:
        return Response("Not found", 404)
        
    running_job = db.scalar(
        select(CompileJob).where(
            CompileJob.tenant_id == ctx.tenant_id,
            CompileJob.project_id == project.id,
            CompileJob.export_format == "timus_views",
            CompileJob.status == "running"
        )
    )
    if running_job:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        job_time = running_job.created_at
        if job_time.tzinfo is None:
            job_time = job_time.replace(tzinfo=timezone.utc)
            
        if (now - job_time).total_seconds() > 600:
            try:
                compile_repo = CompileRepository(db, ctx.tenant_id)
                compile_repo.finish_job(running_job, "failed", error="Job timed out or was abandoned by server restart")
                db.commit()
            except Exception:
                db.rollback()
        else:
            return {"status": "building"}
        
    # Check latest successful artifact
    latest_artifact = db.scalar(
        select(Artifact).where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind == "timus_views"
        ).order_by(desc(Artifact.created_at))
    )
    
    if not latest_artifact:
        return {"status": "none"}
        
    design_file = _get_project_design_file(name, ctx, db)
    if design_file and design_file.updated_at > latest_artifact.created_at:
        return {"status": "stale"}
        
    return {"status": "ready"}

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

@app.get("/projects/{name}/model")
def get_gltf_model(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if not project: return Response("Not found", 404)
    
    latest_artifact = db.scalar(
        select(Artifact).where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind.in_(["gltf", "glb"])
        ).order_by(desc(Artifact.created_at))
    )
    if not latest_artifact: return Response("No 3D model found", 404)
    
    artifact_store = ArtifactStore(get_settings().artifact_root)
    artifact_path = artifact_store.path_for(latest_artifact.storage_key)
    with open(artifact_path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type=latest_artifact.content_type)

@app.get("/projects/{name}/model_status")
def get_model_status(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if not project: return Response("Not found", 404)
    latest_artifact = db.scalar(
        select(Artifact).where(
            Artifact.tenant_id == ctx.tenant_id,
            Artifact.project_id == project.id,
            Artifact.kind.in_(["gltf", "glb"])
        ).order_by(desc(Artifact.created_at))
    )
    if not latest_artifact: return {"mtime": 0}
    return {"mtime": latest_artifact.created_at.timestamp()}

@app.post("/projects/{name}/activate")
def activate_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    repo = ProjectRepository(db, ctx.tenant_id)
    project = repo.get_project(name)
    if not project:
        return Response(status_code=404, content="Not found")
    repo.set_active_project(ctx.user_id, project.id)
    db.commit()
    return {"success": True}

@app.get("/project_name")
def get_project_name(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    project = get_active_project(db, ctx)
    if project is None:
        print("TIMUS PROJECT NAME IS NONE")
        return {"project_name": ""}
    print(f"TIMUS PROJECT NAME IS {project.name}")
    return {"project_name": project.name}


@app.get("/projects/{name}/settings")
def get_timus_settings(
    name: str,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if project is None:
        return Response("Project not found", status_code=404)

    settings = db.scalar(
        select(TimusSettings).where(
            TimusSettings.user_id == ctx.user_id,
            TimusSettings.tenant_id == ctx.tenant_id,
            TimusSettings.project_id == project.id,
        )
    )
    if settings is None:
        return {
            "title": name.upper(),
            "stamp_text": "APPROVED",
            "show_redline": True,
            "show_hidden_lines": True,
            "scale": 1.0,
            "sheet_size": "A4",
        }
    return serialize_timus_settings(settings)


@app.put("/projects/{name}/settings")
def put_timus_settings(
    name: str,
    req: TimusSettingsRequest,
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if project is None:
        return Response("Project not found", status_code=404)

    settings = db.scalar(
        select(TimusSettings).where(
            TimusSettings.user_id == ctx.user_id,
            TimusSettings.tenant_id == ctx.tenant_id,
            TimusSettings.project_id == project.id,
        )
    )
    if settings is None:
        settings = TimusSettings(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            project_id=project.id,
            title=req.title,
            stamp_text=req.stamp_text,
            show_redline=req.show_redline,
            show_hidden_lines=req.show_hidden_lines,
            scale=req.scale,
            sheet_size=req.sheet_size,
        )
        db.add(settings)
    else:
        settings.title = req.title
        settings.stamp_text = req.stamp_text
        settings.show_redline = req.show_redline
        settings.show_hidden_lines = req.show_hidden_lines
        settings.scale = req.scale
        settings.sheet_size = req.sheet_size
    db.commit()
    return {"success": True}


@app.get("/projects/{name}/drafting.pdf")
def get_drafting_pdf(
    name: str, 
    title: str = Query("UNTITLED PART"),
    stamp: str = Query("APPROVED"),
    redline: bool = Query(True),
    hidden_lines: bool = Query(True),
    scale: float = Query(1.0),
    size: str = Query("A4"),
    ctx: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
):
    project = ProjectRepository(db, ctx.tenant_id).get_project(name)
    if project is None:
        return Response("Project not found", status_code=404)
        
    try:
        latest_artifact = db.scalar(
            select(Artifact).where(
                Artifact.tenant_id == ctx.tenant_id,
                Artifact.project_id == project.id,
                Artifact.kind == "timus_views"
            ).order_by(desc(Artifact.created_at))
        )
        if not latest_artifact:
            return Response("Drafting views not generated yet", status_code=400)
            
        artifact_store = ArtifactStore(get_settings().artifact_root)
        artifact_path = artifact_store.path_for(latest_artifact.storage_key)
        
        with open(artifact_path, "r") as f:
            views = json.load(f)
        
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
        _draw_compound_view(pdf, views.get("top", []), ox=top_ox, oy=top_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views.get("front", []), ox=front_ox, oy=front_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views.get("side", []), ox=side_ox, oy=side_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        _draw_compound_view(pdf, views.get("iso", []), ox=iso_ox, oy=iso_oy, w=view_w, h=view_h, scale=scale, show_hidden=hidden_lines)
        
        # Labels
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(150, 150, 150)
        pdf.text(top_ox, top_oy + view_h - 2, "PLAN VIEW")
        pdf.text(front_ox, front_oy + view_h - 2, "FRONT ELEVATION")
        pdf.text(side_ox, side_oy + view_h - 2, "SIDE ELEVATION")
        pdf.text(iso_ox, iso_oy + view_h - 2, "ISOMETRIC VIEW")
            
        pdf_bytes = bytes(pdf.output())
        
        headers = {
            "Content-Disposition": f"inline; filename=\"{name}_drafting.pdf\""
        }
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response(f"Internal Server Error: {str(e)}", status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8893)
