from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from core.workbench_access import SITE_WORKBENCH_ROLE
from workflows.site import site_server


def test_site_workbench_creates_project_owned_definition_without_compile(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="site-workbench-test",
        email="site@example.com",
        roles=frozenset({SITE_WORKBENCH_ROLE}),
    )
    project = SimpleNamespace(id=uuid4(), name="structural_test")
    storage: dict[str, str] = {}

    class RepositoryStub:
        def __init__(self, _db, tenant_id):
            assert tenant_id == context.tenant_id

        def get_code(self, project_name, filename):
            assert project_name == project.name
            return storage.get(filename)

        def save_code(self, project_name, filename, content, user_id, message):
            assert project_name == project.name
            assert user_id == context.user_id
            assert message == "Update site and design basis"
            storage[filename] = content
            return True

    monkeypatch.setattr(site_server, "get_active_project", lambda _db, _ctx: project)
    monkeypatch.setattr(site_server, "ProjectRepository", RepositoryStub)
    site_server.app.dependency_overrides[get_auth_context] = lambda: context
    site_server.app.dependency_overrides[get_db] = lambda: object()
    try:
        with TestClient(site_server.app) as client:
            starter = client.get("/active")
            assert starter.status_code == 200
            assert starter.json()["exists"] is False

            payload = starter.json()["site_dict"]
            payload["location"]["address"] = "14 Porter St"
            payload["structure"]["orientation_status"] = "verified"
            payload["wind"]["cardinal_direction_multipliers"] = {
                direction: 1.0
                for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw")
            }
            payload["wind"]["region_status"] = "verified"
            payload["wind"]["table_status"] = "verified"
            payload["project_basis"]["standards"]["confirmed"] = True
            saved = client.put("/active", json=payload)
            reloaded = client.get("/active")
    finally:
        site_server.app.dependency_overrides.clear()

    assert saved.status_code == 200
    assert saved.json()["calculation"]["site_ready"] is True
    assert reloaded.json()["exists"] is True
    assert "site_dict = {" in storage["tertius_site.py"]
    assert "q_z_kPa" not in storage["tertius_site.py"]


def test_site_workbench_rejects_users_without_the_keycloak_role():
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="general-cad-user",
        email="cad@example.com",
    )
    site_server.app.dependency_overrides[get_auth_context] = lambda: context
    try:
        with TestClient(site_server.app) as client:
            response = client.get("/active")
    finally:
        site_server.app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"] == "Site workbench access required"


def test_site_workbench_proxies_gis_evidence_without_exposing_arbitrary_urls(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="site-gis-test",
        email="gis@example.com",
        roles=frozenset({SITE_WORKBENCH_ROLE}),
    )
    evidence_id = "gisv1-2bfc4440ceed019f52fb90a1811c09a6"
    calls: list[tuple[str, str, dict]] = []

    class ClientStub:
        def __init__(self, *, timeout, follow_redirects):
            assert timeout == site_server.GIS_UPSTREAM_TIMEOUT_SECONDS
            assert follow_redirects is False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/health/ready"):
                return httpx.Response(
                    200,
                    json={"status": "ready", "free_bytes": 1000, "total_bytes": 2000},
                )
            if url.endswith("/v1/evidence"):
                return httpx.Response(201, json={"evidence_id": evidence_id})
            if "/v1/raster/point/" in url:
                return httpx.Response(
                    200,
                    json={
                        "coordinates": [150.005, -33.005],
                        "values": [84.0],
                        "band_names": ["b1"],
                    },
                )
            if url.endswith("/v1/raster/preview.png"):
                return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfixture")
            raise AssertionError(f"Unexpected GIS request: {method} {url}")

    monkeypatch.setattr(site_server, "get_settings", lambda: SimpleNamespace(
        gis_cache_url="http://tertius-gis-cache:8000",
    ))
    monkeypatch.setattr(site_server.httpx, "Client", ClientStub)
    site_server.app.dependency_overrides[get_auth_context] = lambda: context
    try:
        with TestClient(site_server.app) as client:
            health = client.get("/gis/health")
            upload = client.post(
                "/gis/evidence",
                files={"raster": ("terrain.tif", b"fixture", "image/tiff")},
                data={
                    "provider": "manual-upload",
                    "dataset": "fixture DEM",
                    "dataset_version": "v1",
                    "licence": "Test only",
                    "attribution": "Test fixture",
                },
            )
            point = client.get(
                f"/gis/evidence/{evidence_id}/point",
                params={"latitude": -33.005, "longitude": 150.005},
            )
            preview = client.get(f"/gis/evidence/{evidence_id}/preview.png")
            invalid = client.get(
                "/gis/evidence/http://169.254.169.254/latest/point",
                params={"latitude": -33.005, "longitude": 150.005},
            )
    finally:
        site_server.app.dependency_overrides.clear()

    assert health.json()["status"] == "ready"
    assert upload.status_code == 201
    assert upload.json()["evidence_id"] == evidence_id
    assert point.json()["values"] == [84.0]
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["cache-control"] == "private, no-store"
    assert invalid.status_code in {404, 422}
    upload_call = next(call for call in calls if call[0] == "POST")
    assert upload_call[1] == "http://tertius-gis-cache:8000/v1/evidence"
    assert upload_call[2]["data"]["provider"] == "manual-upload"
    assert all("169.254.169.254" not in url for _, url, _ in calls)
