import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from testcontainers.core.container import DockerContainer

import core.auth as auth
from core.config import Settings
from core.db import get_db
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership, UserWorkspaceState
from workflows.intus.intus_server import app as intus_app


def wait_for_keycloak(base_url: str) -> None:
    deadline = time.time() + 90
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/realms/tertius/.well-known/openid-configuration", timeout=2)
            if response.status_code == 200:
                return
            last_error = RuntimeError(f"unexpected status {response.status_code}")
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError("Keycloak did not become ready") from last_error


def write_test_realm(source: Path, destination: Path) -> None:
    realm = json.loads(source.read_text(encoding="utf-8"))
    for client in realm["clients"]:
        if client["clientId"] == "tertius-ui":
            client["directAccessGrantsEnabled"] = True
    destination.write_text(json.dumps(realm), encoding="utf-8")


def get_demo_access_token(base_url: str) -> str:
    response = httpx.post(
        f"{base_url}/realms/tertius/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "tertius-ui",
            "username": "demo",
            "password": "demo",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def test_keycloak_token_authenticates_request_and_provisions_user(db_session: Session, tmp_path: Path):
    realm_file = tmp_path / "tertius-realm.json"
    write_test_realm(Path("infra/keycloak/tertius-realm.json"), realm_file)

    with (
        DockerContainer("quay.io/keycloak/keycloak:26.6.3")
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", "admin")
        .with_volume_mapping(str(realm_file), "/opt/keycloak/data/import/tertius-realm.json", "ro")
        .with_exposed_ports(8080)
        .with_command("start-dev --import-realm")
    ) as keycloak:
        base_url = f"http://{keycloak.get_container_host_ip()}:{keycloak.get_exposed_port(8080)}"
        wait_for_keycloak(base_url)
        token = get_demo_access_token(base_url)

        def override_db():
            yield db_session

        settings = Settings(
            database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
            keycloak_issuer=f"{base_url}/realms/tertius",
            keycloak_audience="tertius-web",
        )
        intus_app.dependency_overrides[get_db] = override_db
        try:
            original_get_settings = auth.get_settings
            auth.get_settings = lambda: settings
            try:
                response = TestClient(intus_app).get("/projects", headers={"Authorization": f"Bearer {token}"})
            finally:
                auth.get_settings = original_get_settings
        finally:
            intus_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"projects": ["default_purlin"]}

    user = db_session.scalar(select(AppUser).where(AppUser.keycloak_subject.is_not(None)))
    tenant = db_session.scalar(select(Tenant))
    membership = db_session.scalar(select(TenantMembership))
    project = db_session.scalar(select(Project))
    design_file = db_session.scalar(select(ProjectFile))
    workspace = db_session.scalar(select(UserWorkspaceState))

    assert user.keycloak_subject
    assert user.email == "demo@example.com"
    assert user.username == "demo"
    assert tenant.name == "Demo User"
    assert membership.user_id == user.id
    assert membership.tenant_id == tenant.id
    assert project.name == "default_purlin"
    assert design_file.filename == "design.py"
    assert workspace.active_project_id == project.id
    assert workspace.active_file_id == design_file.id
