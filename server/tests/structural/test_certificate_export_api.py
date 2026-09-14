from __future__ import annotations

from datetime import UTC, datetime
import io
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.workbench_access import STRUCTURAL_WORKBENCH_ROLE
from workflows.structural import structural_server


def _context() -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="structural-report-test",
        email="report@example.com",
        roles=frozenset({STRUCTURAL_WORKBENCH_ROLE}),
    )


def _report() -> SimpleNamespace:
    pdf_content = b"%PDF-1.4\ncontrolled draft\n%%EOF"
    evidence_content = b'{"snapshot":{}}'
    return SimpleNamespace(
        filename="shed-structural-certificate-draft.pdf",
        report_identity_digest="b" * 64,
        pdf_content=pdf_content,
        pdf_sha256="c" * 64,
        evidence_json_content=evidence_content,
        manifest={"schema_version": "tertius.structural.report-manifest.v1"},
        created_at=datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )


def test_certificate_pdf_endpoint_returns_controlled_artifact_headers(monkeypatch):
    context = _context()
    report = _report()
    project = SimpleNamespace(name="shed")
    monkeypatch.setattr(
        structural_server,
        "_active_report_export",
        lambda **_kwargs: (project, report, False),
    )
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.post(
                "/active/report/certificate-draft.pdf",
                json={"analysis_key_digest": "a" * 64},
            )
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.content == report.pdf_content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="shed-structural-certificate-draft.pdf"'
    )
    assert response.headers["x-tertius-structural-report"] == "CREATED"
    assert response.headers["x-tertius-structural-report-id"] == "b" * 64
    assert response.headers["x-tertius-artifact-sha256"] == "c" * 64
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_review_pack_endpoint_contains_pdf_evidence_and_manifest(monkeypatch):
    context = _context()
    report = _report()
    project = SimpleNamespace(name="shed")
    monkeypatch.setattr(
        structural_server,
        "_active_report_export",
        lambda **_kwargs: (project, report, True),
    )
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.post(
                "/active/report/review-pack.zip",
                json={"analysis_key_digest": "a" * 64},
            )
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-tertius-structural-report"] == "REUSED"
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "shed-structural-certificate-draft.pdf",
            "shed-structural-evidence.json",
            "manifest.json",
        ]
        assert archive.read("shed-structural-certificate-draft.pdf") == report.pdf_content
        assert archive.read("shed-structural-evidence.json") == report.evidence_json_content


def test_certificate_export_rejects_stale_reviewed_analysis(monkeypatch):
    context = _context()
    project = SimpleNamespace(id=uuid4(), name="shed")
    monkeypatch.setattr(
        structural_server,
        "_load_active_capture_context",
        lambda **_kwargs: (
            project,
            SimpleNamespace(
                design_hash="d" * 64,
                analysis_configuration_digest="e" * 64,
            ),
            object(),
        ),
    )
    monkeypatch.setattr(
        structural_server,
        "analysis_cache_identity",
        lambda **_kwargs: SimpleNamespace(key_digest="b" * 64),
    )
    structural_server.app.dependency_overrides[get_auth_context] = lambda: context
    structural_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(structural_server.app) as client:
            response = client.post(
                "/active/report/certificate-draft.pdf",
                json={"analysis_key_digest": "a" * 64},
            )
    finally:
        structural_server.app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "stale" in response.json()["detail"].lower()
