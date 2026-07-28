from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import get_db
from workflows.site import site_server


def test_site_workbench_creates_project_owned_definition_without_compile(monkeypatch):
    context = AuthContext(
        user_id=uuid4(),
        tenant_id=uuid4(),
        keycloak_subject="site-workbench-test",
        email="site@example.com",
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
