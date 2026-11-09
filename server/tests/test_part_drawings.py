"""Part drawings: tertius.drawing author API, projection, sandbox and endpoints."""

from __future__ import annotations

import json
import math
import re

import build123d as bd
import pytest
from sqlalchemy import select

from core.compile_sandbox import run_compile_sandbox
from core.models import Artifact, ProjectFile
from tertius.drawing import Drawing, DrawingError, DrawingSet, project_drawing
from tertius.session import TertiusRuntimeError, compile_session
from workflows.timus.part_drawing_pdf import render_drawing_set_pdf, render_part_drawing_pdf

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


# ------------------------------------------------- ordinate and angle dimensions
def _plate_drawing() -> dict:
    """A 100 x 60 plate with a 30 deg top edge corner and three holes."""
    with bd.BuildPart() as bp:
        with bd.BuildSketch(bd.Plane.XZ):
            bd.Polygon((0, 0), (100, 0), (100, 60), (0, 60 + 100 * math.tan(math.radians(30))), align=None)
        bd.extrude(amount=3)
    part = bp.part
    assert part is not None
    holes = [(20.0, 0.0, 20.0), (20.0, 0.0, 40.0), (21.0, 0.0, 40.0)]  # 21 and 20 are close in x
    with compile_session():
        drw = Drawing("Plate", part, decimals=1)
        front = drw.view("front", at=(150, 150))
        front.ordinate((0, 0, 0), holes + [(100, 0, 0)], axis="horizontal", offset=-10)
        front.ordinate((0, 0, 0), holes, axis="vertical", offset=-10)
        top_left = (0.0, 0.0, 60 + 100 * math.tan(math.radians(30)))
        front.angle((100, 0, 60), (0, 0, 60), top_left, radius=30)
        return project_drawing(drw)


def test_ordinates_measure_from_the_origin_once_per_value():
    doc = _plate_drawing()
    horizontal, vertical, _ = doc["dimensions"]
    assert horizontal["values"] == pytest.approx([0.0, 20.0, 21.0, 100.0])
    assert [i["text"] for i in horizontal["items"]] == ["0", "20.0", "21.0", "100.0"]
    assert vertical["values"] == pytest.approx([0.0, 20.0, 40.0])  # 40 appears once
    # 20 and 21 sit 1 mm apart: their texts are jogged at least the pitch apart.
    xs = [i["text_at"][0] for i in horizontal["items"]]
    assert xs[2] - xs[1] >= 3.6 - 1e-9
    jogged = horizontal["items"][1]["path"]
    assert len(jogged) == 3 and jogged[0][0] != pytest.approx(jogged[-1][0])
    # All values line up on one baseline below the view.
    frame = doc["views"][0]["frame"]
    assert {round(i["path"][-1][1], 6) for i in horizontal["items"]} == {round(frame[1] - 10, 6)}


def test_angle_is_measured_true_in_the_view():
    doc = _plate_drawing()
    angle = doc["dimensions"][2]
    assert angle["value"] == pytest.approx(30.0)
    assert angle["text"] == "30.0°"
    assert len(angle["arc"]) == 25


def test_angle_out_of_the_view_plane_is_refused():
    with compile_session():
        drw = Drawing("Bad", _angle())
        front = drw.view("front", at=(150, 150))
        front.angle((0, 0, 0), (10, 0, 0), (0, -10, 0))
        with pytest.raises(DrawingError, match="not in the plane"):
            project_drawing(drw)


def test_ordinate_and_angle_render():
    pdf = render_part_drawing_pdf(_plate_drawing())
    assert pdf.startswith(b"%PDF-")


# ------------------------------------------------------------------ drawing sets
SET_DESIGN = ANGLE_DESIGN + '''
from tertius.drawing import DrawingSet
pack = DrawingSet("Angle_Pack", title="Mount angle set", number="NGZ-SET-1")
pack.add(drw)
detail = Drawing("Angle_Detail", model, sheet="A4", drawing_set=pack)
detail.view("front", at=(140, 120))
'''


def _set_docs() -> tuple[list[dict], dict, dict]:
    """Two sheets in a set (A3 + A4) and one standalone drawing, one session."""
    part = _angle()
    with compile_session():
        pack = DrawingSet("Angle_Pack", title="Mount angle set")
        first = Drawing("Sheet_A3", part, drawing_set=pack)
        first.view("front", at=(140, 175))
        second = Drawing("Sheet_A4", part, sheet="A4")
        second.view("front", at=(140, 120))
        pack.add(second)
        alone = Drawing("Alone", part)
        alone.view("front", at=(140, 175))
        return [project_drawing(first), project_drawing(second)], project_drawing(alone), pack.describe()


def test_drawing_set_numbers_its_sheets_in_order():
    (first, second), alone, described = _set_docs()
    assert described == {"name": "Angle_Pack", "title": "Mount angle set", "number": "Angle_Pack",
                         "sheets": ["Sheet_A3", "Sheet_A4"]}
    assert first["set"] == {"name": "Angle_Pack", "title": "Mount angle set", "number": "Angle_Pack",
                            "index": 1, "count": 2}
    assert first["fields"]["sheet_no"] == "1 OF 2"
    assert second["fields"]["sheet_no"] == "2 OF 2"
    assert alone["set"] is None
    assert "sheet_no" not in alone["fields"]  # standalone sheets keep "1 OF 1"


def test_drawing_belongs_to_one_set_and_set_names_are_unique():
    part = _angle()
    with compile_session():
        a = DrawingSet("A")
        b = DrawingSet("B")
        drw = Drawing("Only_Once", part, drawing_set=a)
        with pytest.raises(DrawingError, match="already in set"):
            b.add(drw)
        with pytest.raises(TertiusRuntimeError, match="already used"):
            DrawingSet("A")


def test_renders_a_set_as_one_pdf_with_a_page_per_sheet():
    docs, _, _ = _set_docs()
    pdf = render_drawing_set_pdf(docs, title="Mount angle set")
    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf)) == 2
    boxes = [(float(w), float(h)) for w, h in
             re.findall(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", pdf)]
    sizes = {(round(w / 72 * 25.4), round(h / 72 * 25.4)) for w, h in boxes}
    assert {(420, 297), (297, 210)} <= sizes  # A3 and A4, each in landscape


def test_sandbox_records_drawing_sets():
    from core.compile_runtime import hydrate_project_files

    with hydrate_project_files({"design.py": SET_DESIGN}) as project_dir:
        result = run_compile_sandbox(project_dir, "timus_views", timeout_seconds=120)
        assert result.success, result.stderr
        views = json.loads(result.output_path.read_text())
    assert views["drawing_sets"] == [{"name": "Angle_Pack", "title": "Mount angle set",
                                      "number": "NGZ-SET-1", "sheets": ["Mount_Angle", "Angle_Detail"]}]
    by_name = {d["name"]: d for d in views["drawings"]}
    assert by_name["Mount_Angle"]["fields"]["sheet_no"] == "1 OF 2"
    assert by_name["Angle_Detail"]["fields"]["sheet_no"] == "2 OF 2"


def test_drawing_set_endpoints(authenticated_timus_client, db_session, seeded_tenant):
    docs, _, described = _set_docs()
    broken = {"schema": "tertius.part-drawing.v1", "name": "Broken", "error": "DrawingError: nope"}
    _set_design(db_session, seeded_tenant, ANGLE_DESIGN)
    _persist(db_session, seeded_tenant, {
        "top": [], "front": [], "side": [], "iso": [],
        "drawings": docs + [broken],
        "drawing_sets": [described, {"name": "Has_Broken", "sheets": ["Sheet_A3", "Broken"]}],
    })

    listed = authenticated_timus_client.get("/projects/default_purlin/drawing-sets")
    assert listed.status_code == 200
    assert [s["name"] for s in listed.json()["sets"]] == ["Angle_Pack", "Has_Broken"]
    drawings = {d["name"]: d for d in authenticated_timus_client.get("/projects/default_purlin/drawings").json()["drawings"]}
    assert drawings["Sheet_A3"]["set"] == "Angle_Pack"

    pdf = authenticated_timus_client.get("/projects/default_purlin/drawing-sets/Angle_Pack.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert len(re.findall(rb"/Type\s*/Page[^s]", pdf.content)) == 2

    missing = authenticated_timus_client.get("/projects/default_purlin/drawing-sets/Nope.pdf")
    assert missing.status_code == 404
    assert missing.json()["sets"] == ["Angle_Pack", "Has_Broken"]

    failed = authenticated_timus_client.get("/projects/default_purlin/drawing-sets/Has_Broken.pdf")
    assert failed.status_code == 422
    assert failed.json()["error"] == "DrawingError: nope"


def test_drawing_sets_need_a_current_build(authenticated_timus_client, db_session, seeded_tenant):
    from datetime import timedelta

    docs, _, described = _set_docs()
    _persist(db_session, seeded_tenant, {"drawings": docs, "drawing_sets": [described]})
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
    for path in ("drawing-sets", "drawing-sets/Angle_Pack.pdf"):
        response = authenticated_timus_client.get(f"/projects/default_purlin/{path}")
        assert response.status_code == 409, path
