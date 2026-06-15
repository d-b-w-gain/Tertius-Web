"""Timus drafting PDF end-to-end tests.

Tests the full drafting pipeline:
  - HLR projection output (edge segments for all 4 views)
  - PDF generation with real fpdf2 output
  - Background build flow: trigger -> poll -> retrieve
  - PDF content validation (page count, title block text)
  - Cache poisoning prevention
  - Settings round-trip + validation
"""

from __future__ import annotations

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from core.compile_sandbox import run_compile_sandbox
from core.models import Artifact, CompileJob, ProjectFile, TimusSettings
from workflows.timus import timus_server

# ---------------------------------------------------------------------------
# Minimal build123d code that produces a 3D shape
# ---------------------------------------------------------------------------

BOX_CODE = """import build123d as bd
box = bd.Box(10, 10, 10)
"""

CYLINDER_CODE = """import build123d as bd
cyl = bd.Cylinder(radius=5, height=20)
"""


# ---------------------------------------------------------------------------
# PDF content helpers
# ---------------------------------------------------------------------------

def _count_pdf_pages(pdf_bytes: bytes) -> int:
    """Count pages by looking for PDF page markers."""
    import re

    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_bytes))


# ---------------------------------------------------------------------------
# HLR projection: verify edge segments for all 4 views
# ---------------------------------------------------------------------------

def test_hlr_projection_produces_edges_for_all_four_views(db_session, seeded_tenant, monkeypatch):
    """Run the sandbox with timus_views export and verify the JSON output
    contains edge segments for top, front, side, and iso views."""
    from core.compile_runtime import hydrate_project_files

    files = {"design.py": BOX_CODE}
    with hydrate_project_files(files) as project_dir:
        settings_path = project_dir / "settings.json"
        settings_path.write_text(json.dumps({
            "title": "TEST BOX",
            "stamp_text": "APPROVED",
            "show_redline": True,
            "show_hidden_lines": True,
            "scale": 1.0,
            "sheet_size": "A4",
        }))

        result = run_compile_sandbox(project_dir, "timus_views", timeout_seconds=30)

        assert result.success, f"Sandbox should succeed: {result.stderr}"
        assert result.output_path is not None

        views = json.loads(result.output_path.read_text())

        for view_name in ["top", "front", "side", "iso"]:
            assert view_name in views, f"Missing view: {view_name}"
            segments = views[view_name]
            assert isinstance(segments, list), f"{view_name} segments should be a list"
            assert segments, f"{view_name} should contain at least one projected edge segment"

            # Segment format: [[x1, y1], [x2, y2], is_hidden] (3 elements)
            seg = segments[0]
            assert len(seg) == 3, f"Segment should have 3 elements (p1, p2, hidden), got {seg}"
            assert len(seg[0]) == 2, f"Point 1 should have 2 coordinates, got {seg[0]}"
            assert len(seg[1]) == 2, f"Point 2 should have 2 coordinates, got {seg[1]}"
            assert isinstance(seg[2], bool), f"Hidden flag should be a bool, got {type(seg[2])}"


# ---------------------------------------------------------------------------
# PDF generation: real PDF output
# ---------------------------------------------------------------------------

def test_drafting_pdf_endpoint_returns_valid_pdf(authenticated_timus_client, db_session, seeded_tenant):
    """GET /projects/{name}/drafting.pdf should return a valid PDF with
    correct content type and basic structure."""
    # Update design.py with valid build123d code
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    db_session.commit()

    response = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"title": "TEST BOX", "size": "A4", "scale": "1.0"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers["content-disposition"]

    pdf_bytes = response.content
    assert pdf_bytes.startswith(b"%PDF-"), "Response should be a PDF file"
    assert pdf_bytes.rstrip().endswith(b"%%EOF"), "PDF should end with %%EOF marker"

    # Should have exactly 1 page
    pages = _count_pdf_pages(pdf_bytes)
    assert pages == 1, f"Expected 1 page, got {pages}"

    # PDF contains expected structure markers
    assert b"/Type /Page" in pdf_bytes, "PDF should contain a page object"
    assert b"/MediaBox" in pdf_bytes, "PDF should define a media box"


# ---------------------------------------------------------------------------
# PDF page dimensions vary by sheet size
# ---------------------------------------------------------------------------

def test_drafting_pdf_respects_sheet_size(authenticated_timus_client, db_session, seeded_tenant):
    """Different sheet sizes should produce different PDF page dimensions."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    db_session.commit()

    a4_response = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"size": "A4", "scale": "0.1"},
    )
    a3_response = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"size": "A3", "scale": "0.1"},
    )

    assert a4_response.status_code == 200
    assert a3_response.status_code == 200

    # A3 and A4 PDFs should differ in content (different page dimensions)
    # A4 landscape = 297x210mm, A3 landscape = 420x297mm
    assert len(a3_response.content) != len(a4_response.content), (
        "A3 and A4 PDFs should differ in content"
    )

    # Extract MediaBox from each PDF to verify different page sizes
    import re

    a4_box = re.search(rb"/MediaBox\s*\[([^\]]+)\]", a4_response.content)
    a3_box = re.search(rb"/MediaBox\s*\[([^\]]+)\]", a3_response.content)
    assert a4_box is not None, "A4 PDF should have MediaBox"
    assert a3_box is not None, "A3 PDF should have MediaBox"
    assert a4_box.group(1) != a3_box.group(1), "A4 and A3 should have different MediaBox values"

    # Both should be valid PDFs
    assert a3_response.content.startswith(b"%PDF-")
    assert a4_response.content.startswith(b"%PDF-")


# ---------------------------------------------------------------------------
# Background build flow: trigger -> poll -> retrieve
# ---------------------------------------------------------------------------

def test_background_build_flow_trigger_poll_retrieve(
    authenticated_timus_client, db_session, seeded_tenant, postgres_url, monkeypatch
):
    """Trigger a background build, poll status, then retrieve the built views."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    db_session.commit()

    engine = create_engine(postgres_url, pool_pre_ping=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(timus_server, "SessionLocal", TestingSessionLocal)

    # Initial status should be "none" (no artifact yet)
    status = authenticated_timus_client.get("/projects/default_purlin/drafting/status")
    assert status.json()["status"] in ("none", "building", "ready", "stale")

    try:
        # Trigger a build
        trigger = authenticated_timus_client.post("/projects/default_purlin/drafting/build")
        assert trigger.status_code == 200
        assert trigger.json()["status"] in ("started", "building")

        # After trigger, status should be "building" or "ready"; "none" means no build result exists.
        status2 = authenticated_timus_client.get("/projects/default_purlin/drafting/status")
        assert status2.json()["status"] in ("building", "ready")

        db_session.expire_all()
        job = db_session.scalar(
            select(CompileJob).where(
                CompileJob.tenant_id == seeded_tenant.tenant_id,
                CompileJob.project_id == seeded_tenant.project_id,
                CompileJob.export_format == "timus_views",
            )
        )
        assert job is not None, "Background build should persist a timus_views job"
        assert job.status == "succeeded"

        artifact = db_session.scalar(
            select(Artifact).where(
                Artifact.tenant_id == seeded_tenant.tenant_id,
                Artifact.project_id == seeded_tenant.project_id,
                Artifact.compile_job_id == job.id,
                Artifact.kind == "timus_views",
            )
        )
        assert artifact is not None, "Background build should persist a timus_views artifact"
        assert artifact.content is not None, "timus_views artifact should contain JSON result bytes"
        result_views = json.loads(artifact.content.decode("utf-8"))
        for view_name in ["top", "front", "side", "iso"]:
            assert result_views.get(view_name), f"Artifact result missing segments for {view_name}"

        retrieve = authenticated_timus_client.get("/projects/default_purlin/drafting.pdf")
        assert retrieve.status_code == 200
        assert retrieve.content.startswith(b"%PDF-")
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Cache poisoning: stale cache not served after design.py change
# ---------------------------------------------------------------------------

def test_drafting_cache_is_invalidated_on_design_change(
    authenticated_timus_client, db_session, seeded_tenant, monkeypatch
):
    """When design.py changes, the projection cache should be invalidated and
    the PDF should reflect the new geometry."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    db_session.commit()

    # Clear the module-level cache
    timus_server.PROJECTION_CACHE.clear()

    # First request: generates and caches views for BOX_CODE
    response1 = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"size": "A4", "scale": "0.1"},
    )
    assert response1.status_code == 200

    cache_key = f"{seeded_tenant.tenant_id}:{seeded_tenant.project_id}:default_purlin"
    assert cache_key in timus_server.PROJECTION_CACHE, "Views should be cached after first request"
    first_mtime, first_views = timus_server.PROJECTION_CACHE[cache_key]

    # Change design.py to different geometry
    design.content = CYLINDER_CODE
    design.updated_at = design.updated_at + timedelta(seconds=1)
    db_session.commit()

    # Second request: mtime changed, should recompute (not serve from cache)
    response2 = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"size": "A4", "scale": "0.1"},
    )
    assert response2.status_code == 200

    # Cache should now reflect the second design state, either by mtime or output.
    second_mtime, second_views = timus_server.PROJECTION_CACHE[cache_key]
    assert (second_mtime != first_mtime) or (second_views != first_views), (
        "Second request should recompute cached views after design.py changes"
    )

    timus_server.PROJECTION_CACHE.clear()


def test_drafting_pdf_recomputes_when_persisted_views_are_stale(
    authenticated_timus_client, db_session, seeded_tenant, monkeypatch
):
    """A persisted timus_views artifact should not be used after design.py changes."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    stale_artifact_time = design.updated_at
    db_session.add(
        Artifact(
            tenant_id=seeded_tenant.tenant_id,
            project_id=seeded_tenant.project_id,
            compile_job_id=None,
            kind="timus_views",
            storage_key="test/stale-timus-views.json",
            content_type="application/json",
            byte_size=2,
            content=b"{}",
            created_at=stale_artifact_time,
        )
    )
    db_session.commit()

    design.content = CYLINDER_CODE
    design.updated_at = stale_artifact_time + timedelta(seconds=1)
    db_session.commit()

    recomputed = {"called": False}

    def fake_get_projected_views(cache_key, compound, mtime):
        recomputed["called"] = True
        return {"top": [], "front": [], "side": [], "iso": []}

    monkeypatch.setattr(timus_server, "get_projected_views", fake_get_projected_views)

    response = authenticated_timus_client.get(
        "/projects/default_purlin/drafting.pdf",
        params={"size": "A4", "scale": "0.1"},
    )

    assert response.status_code == 200
    assert recomputed["called"], "Stale persisted views should be recomputed"


# ---------------------------------------------------------------------------
# Settings round-trip + validation
# ---------------------------------------------------------------------------

def test_timus_settings_full_round_trip(authenticated_timus_client, db_session, seeded_tenant):
    """Save custom settings, retrieve them, verify all fields round-trip correctly."""
    payload = {
        "title": "CUSTOM BRACKET",
        "stamp_text": "RELEASED",
        "show_redline": False,
        "show_hidden_lines": True,
        "scale": 2.5,
        "sheet_size": "A2",
    }

    save = authenticated_timus_client.put("/projects/default_purlin/settings", json=payload)
    assert save.status_code == 200
    assert save.json()["success"] is True

    load = authenticated_timus_client.get("/projects/default_purlin/settings")
    assert load.status_code == 200
    data = load.json()
    assert data["title"] == "CUSTOM BRACKET"
    assert data["stamp_text"] == "RELEASED"
    assert data["show_redline"] is False
    assert data["show_hidden_lines"] is True
    assert data["scale"] == 2.5
    assert data["sheet_size"] == "A2"


def test_timus_settings_defaults_when_none_saved(authenticated_timus_client, db_session, seeded_tenant):
    """Before any settings are saved, defaults should be returned."""
    # Delete any existing settings
    db_session.query(TimusSettings).filter(
        TimusSettings.tenant_id == seeded_tenant.tenant_id,
        TimusSettings.project_id == seeded_tenant.project_id,
    ).delete()
    db_session.commit()

    response = authenticated_timus_client.get("/projects/default_purlin/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "DEFAULT_PURLIN"  # name.upper()
    assert data["sheet_size"] == "A4"
    assert data["show_redline"] is True


def test_timus_settings_rejects_invalid_sheet_size(authenticated_timus_client):
    """Invalid sheet_size should return 422 validation error."""
    response = authenticated_timus_client.put(
        "/projects/default_purlin/settings",
        json={
            "title": "TEST",
            "stamp_text": "OK",
            "show_redline": True,
            "show_hidden_lines": True,
            "scale": 1.0,
            "sheet_size": "LETTER",
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Bounds endpoint
# ---------------------------------------------------------------------------

def test_bounds_endpoint_returns_max_dimension(authenticated_timus_client, db_session, seeded_tenant):
    """GET /projects/{name}/bounds should return max_dim from the build123d model."""
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.project_id == seeded_tenant.project_id,
            ProjectFile.filename == "design.py",
        )
    )
    design.content = BOX_CODE
    db_session.commit()

    response = authenticated_timus_client.get("/projects/default_purlin/bounds")
    assert response.status_code == 200
    data = response.json()
    assert "max_dim" in data
    # A 10x10x10 box should have max_dim = 10
    assert data["max_dim"] == pytest.approx(10.0, abs=0.1)


def test_bounds_endpoint_returns_404_for_missing_project(authenticated_timus_client):
    """Bounds endpoint should 404 for non-existent projects."""
    response = authenticated_timus_client.get("/projects/nonexistent/bounds")
    assert response.status_code == 404
