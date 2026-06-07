from collections.abc import Generator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from core.auth import get_auth_context
from core.auth_types import AuthContext
from core.db import Base, get_db
import core.models
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership, UserWorkspaceState
from workflows.artus.artus_server import app as artus_app
from workflows.extus.extus_server import app as extus_app
from workflows.intus.intus_server import app as intus_app
from workflows.timus.timus_server import app as timus_app


@pytest.fixture(scope="session")
def postgres_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:16") as postgres:
        host = postgres.get_container_host_ip()
        port = postgres.get_exposed_port(5432)
        username = postgres.username
        password = postgres.password
        database = postgres.dbname
        yield f"postgresql+psycopg://{username}:{password}@{host}:{port}/{database}"


@pytest.fixture()
def db_session(postgres_url: str) -> Generator[Session, None, None]:
    engine = create_engine(postgres_url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSessionLocal() as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@dataclass(frozen=True)
class SeededTenant:
    user_id: object
    tenant_id: object
    project_id: object


@pytest.fixture()
def seeded_tenant(db_session: Session) -> SeededTenant:
    user = AppUser(id=uuid4(), keycloak_subject="kc-test", email="test@example.com")
    tenant = Tenant(id=uuid4(), name="Test Tenant")
    db_session.add_all([user, tenant])
    db_session.flush()

    db_session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner"))
    project = Project(id=uuid4(), tenant_id=tenant.id, name="default_purlin", created_by=user.id)
    db_session.add(project)
    db_session.flush()

    design = ProjectFile(
        tenant_id=tenant.id,
        project_id=project.id,
        filename="design.py",
        content="import build123d as bd\nlength = 100\n",
    )
    db_session.add(design)
    db_session.flush()
    db_session.add(
        UserWorkspaceState(
            user_id=user.id,
            tenant_id=tenant.id,
            active_project_id=project.id,
            active_file_id=design.id,
        )
    )
    db_session.commit()
    return SeededTenant(user_id=user.id, tenant_id=tenant.id, project_id=project.id)


def configure_authenticated_client(app, db_session: Session, seeded_tenant: SeededTenant) -> TestClient:
    def override_db():
        yield db_session

    def override_auth():
        return AuthContext(
            user_id=seeded_tenant.user_id,
            tenant_id=seeded_tenant.tenant_id,
            keycloak_subject="kc-test",
            email="test@example.com",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return TestClient(app)


@pytest.fixture()
def authenticated_intus_client(db_session: Session, seeded_tenant: SeededTenant):
    client = configure_authenticated_client(intus_app, db_session, seeded_tenant)
    yield client
    intus_app.dependency_overrides.clear()


@pytest.fixture()
def authenticated_artus_client(db_session: Session, seeded_tenant: SeededTenant):
    client = configure_authenticated_client(artus_app, db_session, seeded_tenant)
    yield client
    artus_app.dependency_overrides.clear()


@pytest.fixture()
def authenticated_extus_client(db_session: Session, seeded_tenant: SeededTenant):
    client = configure_authenticated_client(extus_app, db_session, seeded_tenant)
    yield client
    extus_app.dependency_overrides.clear()


@pytest.fixture()
def authenticated_timus_client(db_session: Session, seeded_tenant: SeededTenant):
    client = configure_authenticated_client(timus_app, db_session, seeded_tenant)
    yield client
    timus_app.dependency_overrides.clear()
