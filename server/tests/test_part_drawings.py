"""Part drawings: tertius.drawing author API, projection, sandbox and endpoints."""

from __future__ import annotations

import json
import re

import build123d as bd
import pytest
from sqlalchemy import select

from core.compile_sandbox import run_compile_sandbox
from core.models import Artifact, ProjectFile
from tertius.drawing import Drawing, DrawingError, project_drawing
from tertius.session import TertiusRuntimeError, compile_session
from workflows.timus.part_drawing_pdf import render_part_drawing_pdf

HOLES = (22.5, 51.75, 128.25, 157.5)
LENGTH, LEG, THICK, HOLE_Z, TAP = 180.0, 50.0, 2.0, 19.25, 4.2

ANGLE_DESIGN = f'''
import build123d as bd
from tertius.drawing import Drawing

HOLES = {HOLES!r}
with bd.BuildPart() as ang:
    with bd.BuildSketch(bd.Plane.YZ):
        bd.Polygon((0, 0), ({LEG}, 0), ({LEG}, {THICK}), ({THICK}, {THICK}), ({THICK}, {LEG}), (0, {LEG}), align=None)
    bd.extrude(amount={LENGTH})
    with bd.Locations(*[bd.Location((x, 0, {HOLE_Z}), (-90, 0, 0)) for x in HOLES]):
        bd.Hole({TAP} / 2, depth=10)
model = ang.part
rim = min((e for e in model.edges() if e.geom_type.name == "CIRCLE" and abs(e.arc_center.Y) < 1e-6),
          key=lambda e: e.arc_center.X)
drw = Drawing("Mount_Angle", model, sheet="A3", title="Mount angle", number="NGZ-D4Y4-CH3ZG",
              material="Mild steel", notes=["DEBURR ALL EDGES."])
front = drw.view("front", at=(140, 175))
right = drw.view("right", aligned_to=front)
drw.view("iso", at=(345, 235), scale=0.5)
front.horizontal((0, 0, 0), ({LENGTH}, 0, 0), offset=-12)
front.vertical((0, 0, 0), (HOLES[0], 0, {HOLE_Z}), offset=8)
front.diameter(rim, angle=60)
right.horizontal((0, 0, 0), (0, {LEG}, 0), offset=-8)
'''


def _angle() -> bd.Part:
    with bd.BuildPart() as ang:
        with bd.BuildSketch(bd.Plane.YZ):
            bd.Polygon((0, 0), (LEG, 0), (LEG, THICK), (THICK, THICK), (THICK, LEG), (0, LEG), align=None)
        bd.extrude(amount=LENGTH)
        with bd.Locations(*[bd.Location((x, 0, HOLE_Z), (-90, 0, 0)) for x in HOLES]):
            bd.Hole(TAP / 2, depth=10)
    assert ang.part is not None
    return ang.part


def _rims(part: bd.Part) -> list[bd.Edge]:
    return sorted(
        (e for e in part.edges() if e.geom_type.name == "CIRCLE" and abs(e.arc_center.Y) < 1e-6),
        key=lambda e: e.arc_center.X,
    )


def _angle_drawing(projection: str = "third") -> dict:
    part = _angle()
    with compile_session():
        drw = Drawing("Mount_Angle", part, projection=projection, title="Mount angle")
        front = drw.view("front", at=(140, 175))
        right = drw.view("right", aligned_to=front)
        drw.view("iso", at=(345, 235), scale=0.5)
        for i, x in enumerate(HOLES):
            front.horizontal((0, 0, 0), (x, 0, HOLE_Z), offset=-8 - 7 * i)
        front.horizontal((0, 0, 0), (LENGTH, 0, 0), offset=-40)
        front.vertical((0, 0, 0), (HOLES[0], 0, HOLE_Z), offset=8)
        front.diameter(_rims(part)[0], angle=60)
        right.horizontal((0, 0, 0), (0, LEG, 0), offset=-8)
        right.vertical((0, LEG, 0), (0, LEG, THICK), offset=6)
        right.radius(center=(0, 0, 0), radius=1.0)  # face-on in the right view
        return project_drawing(drw)


# ------------------------------------------------------------------ projection
def test_dimensions_resolve_to_model_values():
    doc = _angle_drawing()
    texts = [d["text"] for d in doc["dimensions"]]
    assert texts == ["22.50", "51.75", "128.25", "157.50", "180.00", "19.25", "Ø4.20", "50.00", "2.00", "R1.00"]
    values = {d["text"]: d["value"] for d in doc["dimensions"]}
    assert values["180.00"] == pytest.approx(LENGTH)
    assert values["Ø4.20"] == pytest.approx(TAP)


def test_holes_stay_exact_circles_and_views_align():
    doc = _angle_drawing()
    front, right, iso = doc["views"]
    circles = [e for e in front["entities"] if e["t"] == "circle"]
    assert len(circles) == 4
    assert all(c["r"] == pytest.approx(TAP / 2) for c in circles)
    # Third angle: the right view sits to the right, level with the front view.
    assert right["frame"][0] > front["frame"][2]
    assert right["frame"][1] == pytest.approx(front["frame"][1])
    assert right["frame"][3] == pytest.approx(front["frame"][3])
    # The sheet-scale front view is 1:1; the iso is drawn at 1:2 and labelled.
    assert front["frame"][2] - front["frame"][0] == pytest.approx(LENGTH)
    assert iso["scale"] == 0.5 and iso["show_label"] is True
    assert front["show_label"] is False


def test_first_angle_puts_the_right_view_on_the_left():
    doc = _angle_drawing("first")
    front, right, _ = doc["views"]
    assert right["frame"][2] < front["frame"][0]
    assert doc["title_block"]["projection"] == "first"


def test_dimension_lines_sit_outside_the_view():
    doc = _angle_drawing()
    front = doc["views"][0]
    overall = next(d for d in doc["dimensions"] if d["text"] == "180.00")
    assert overall["line"][0][1] == pytest.approx(front["frame"][1] - 40)
    assert overall["line"][0][0] == pytest.approx(front["frame"][0])
    assert overall["line"][1][0] == pytest.approx(front["frame"][2])


def test_circle_must_be_face_on():
    part = _angle()
    with compile_session():
        drw = Drawing("Bad", part)
        right = drw.view("right", at=(200, 150))
        right.diameter(_rims(part)[0])
        with pytest.raises(DrawingError, match="face-on"):
            project_drawing(drw)


def test_aligned_view_must_be_orthographic():
    part = _angle()
    with compile_session():
        drw = Drawing("Bad", part)
        front = drw.view("front", at=(140, 175))
        drw.view("iso", aligned_to=front)
        with pytest.raises(DrawingError, match="not an orthographic projection"):
            project_drawing(drw)


def test_drawing_names_are_unique_per_compile():
    part = _angle()
    with compile_session():
        Drawing("Twice", part)
        with pytest.raises(TertiusRuntimeError, match="already used"):
            Drawing("Twice", part)


def test_view_needs_a_position():
    with compile_session():
        drw = Drawing("NoPlace", _angle())
        with pytest.raises(DrawingError, match="needs at="):
            drw.view("front")


def test_renders_a_one_page_sheet_of_the_right_size():
    pdf = render_part_drawing_pdf(_angle_drawing())
    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 1
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", pdf)
    assert box is not None
    width_pt, height_pt = float(box.group(1)), float(box.group(2))
    assert width_pt == pytest.approx(420 / 25.4 * 72, abs=1)
    assert height_pt == pytest.approx(297 / 25.4 * 72, abs=1)


# --------------------------------------------------------------------- sandbox
def test_sandbox_projects_declared_drawings():
    from core.compile_runtime import hydrate_project_files

    with hydrate_project_files({"design.py": ANGLE_DESIGN}) as project_dir:
        result = run_compile_sandbox(project_dir, "timus_views", timeout_seconds=120)
        assert result.success, result.stderr
        assert result.output_path is not None
        views = json.loads(result.output_path.read_text())
    assert {"top", "front", "side", "iso"} <= set(views)  # the existing views are unchanged
    (doc,) = views["drawings"]
    assert doc["schema"] == "tertius.part-drawing.v1"
    assert doc["name"] == "Mount_Angle"
    assert [d["text"] for d in doc["dimensions"]] == ["180.00", "19.25", "Ø4.20", "50.00"]
    assert doc["title_block"]["number"] == "NGZ-D4Y4-CH3ZG"


def test_sandbox_records_a_broken_drawing_instead_of_failing():
    from core.compile_runtime import hydrate_project_files

    broken = ANGLE_DESIGN + "\nright.diameter(rim)\n"
    with hydrate_project_files({"design.py": broken}) as project_dir:
        result = run_compile_sandbox(project_dir, "timus_views", timeout_seconds=120)
        assert result.success, result.stderr
        views = json.loads(result.output_path.read_text())
    (doc,) = views["drawings"]
    assert "face-on" in doc["error"]


# ------------------------------------------------------------------- endpoints
def _set_design(db_session, seeded_tenant, code: str) -> None:
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = code
    db_session.commit()


def _persist(db_session, seeded_tenant, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    db_session.add(Artifact(
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        compile_job_id=None,
        kind="timus_views",
        storage_key="test/part-drawings.json",
        content_type="application/json",
        byte_size=len(body),
        content=body,
    ))
    db_session.commit()


def _views_with_drawings() -> dict:
    return {
        "top": [], "front": [], "side": [], "iso": [],
        "drawings": [
            _angle_drawing(),
            {"schema": "tertius.part-drawing.v1", "name": "Broken", "error": "DrawingError: nope"},
        ],
    }


def test_drawings_endpoints(authenticated_timus_client, db_session, seeded_tenant):
    _set_design(db_session, seeded_tenant, ANGLE_DESIGN)
    _persist(db_session, seeded_tenant, _views_with_drawings())

    listed = authenticated_timus_client.get("/projects/default_purlin/drawings")
    assert listed.status_code == 200
    names = {d["name"]: d for d in listed.json()["drawings"]}
    assert names["Mount_Angle"]["dimensions"] == 10
    assert names["Mount_Angle"]["sheet"] == "A3"
    assert names["Broken"]["error"] == "DrawingError: nope"

    doc = authenticated_timus_client.get("/projects/default_purlin/drawings/Mount_Angle.json")
    assert doc.status_code == 200
    assert doc.json()["dimensions"][4]["text"] == "180.00"

    pdf = authenticated_timus_client.get("/projects/default_purlin/drawings/Mount_Angle.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")

    missing = authenticated_timus_client.get("/projects/default_purlin/drawings/Nope.pdf")
    assert missing.status_code == 404
    assert "Mount_Angle" in missing.json()["drawings"]

    broken = authenticated_timus_client.get("/projects/default_purlin/drawings/Broken.pdf")
    assert broken.status_code == 422
    assert broken.json()["error"] == "DrawingError: nope"


def test_drawings_need_a_current_build(authenticated_timus_client, db_session, seeded_tenant):
    from datetime import timedelta

    _persist(db_session, seeded_tenant, _views_with_drawings())
    built_at = db_session.scalar(select(Artifact.created_at).where(Artifact.kind == "timus_views"))
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = ANGLE_DESIGN + "\n# edited after the build\n"
    design.updated_at = built_at + timedelta(seconds=1)
    db_session.commit()
    for path in ("drawings", "drawings/Mount_Angle.json", "drawings/Mount_Angle.pdf"):
        response = authenticated_timus_client.get(f"/projects/default_purlin/{path}")
        assert response.status_code == 409, path
