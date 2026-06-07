# Postgres Keycloak Multitenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace file-based persistence with tenant-scoped Postgres persistence, protected by Keycloak-backed JWT authentication, while preserving the current Intus, Artus, Extus, and Timus user workflows.

**Architecture:** Keycloak owns authentication and issues JWTs. FastAPI validates Keycloak tokens, upserts app users by Keycloak `sub`, resolves the user's active tenant, and passes an `AuthContext` into every persistence operation. Postgres stores users, tenants, projects, project files, source snapshots, workspace state, Timus settings, compile job metadata, and artifact metadata; generated STL/STEP/PDF bytes live in tenant-scoped artifact storage referenced by Postgres.

**Tech Stack:** FastAPI, PyJWT with JWKS validation, SQLAlchemy 2.0, Alembic, Postgres, Testcontainers, pytest, React, TypeScript, `oidc-client-ts`, Vite.

---

## File Structure

- Create `server/core/config.py`: environment-backed app, database, artifact, and Keycloak settings.
- Create `server/core/db.py`: SQLAlchemy engine, session factory, and FastAPI DB dependency.
- Create `server/core/models.py`: SQLAlchemy ORM models for app users, tenants, memberships, projects, files, snapshots, workspace state, compile jobs, artifacts, and Timus settings.
- Create `server/core/auth_types.py`: shared `Principal` and `AuthContext` dataclasses used by auth and provisioning without circular imports.
- Create `server/core/auth.py`: Keycloak JWKS token validation and `AuthContext` dependency.
- Create `server/core/provisioning.py`: first-login user and default-tenant provisioning.
- Create `server/core/repositories.py`: tenant-scoped persistence helpers used by workflows.
- Create `server/core/artifacts.py`: tenant/project scoped local artifact storage.
- Create `server/core/compile_runtime.py`: hydrate project files into a temporary directory for `build123d` execution.
- Create `server/alembic.ini`, `server/migrations/env.py`, and `server/migrations/versions/0001_initial_multitenant_schema.py`: schema migrations.
- Create `server/tests/conftest.py`: isolated test app settings, DB override, and auth override helpers.
- Create `server/tests/test_migrations.py`: Testcontainers-backed Alembic migration test against real Postgres.
- Create `server/tests/test_auth.py`: JWT validation and missing-token tests.
- Create `server/tests/test_repositories.py`: tenant isolation and default provisioning tests.
- Create `server/tests/test_workflow_isolation.py`: endpoint-level cross-tenant denial tests.
- Modify `server/requirements.txt`: add database, migration, auth, and test dependencies.
- Modify `server/main.py`: add authenticated `/api/me`, wire DB lifecycle, and keep workflow mounts.
- Modify `server/workflows/intus/intus_server.py`: replace filesystem project persistence with repositories.
- Modify `server/workflows/artus/artus_server.py`: replace `active_project.txt` with authenticated workspace state.
- Modify `server/workflows/extus/extus_server.py`: replace global `active_output.stl` with latest active-project artifact lookup.
- Modify `server/workflows/timus/timus_server.py`: replace project file reads and browser `localStorage` state with DB-backed settings.
- Modify `Dockerfile`: install runtime dependencies and create artifact directory.
- Create `docker-compose.yml`: local Postgres and Keycloak services for development.
- Create `ui/src/auth/keycloak.ts`: OIDC client configuration and login/logout helpers.
- Create `ui/src/auth/AuthProvider.tsx`: React auth state provider.
- Create `ui/src/api/client.ts`: authenticated fetch wrapper.
- Modify `ui/src/main.tsx`: wrap app in `AuthProvider`.
- Modify `ui/src/App.tsx`: render login redirect state and authenticated workflows.
- Modify workflow UI files under `ui/src/workflows/**`: route calls through `apiFetch`, remove `intus_last_project` and `timus_settings_*` localStorage usage.
- Modify `ui/package.json`: add `oidc-client-ts`.

## Task 1: Server Dependencies And Configuration

**Files:**
- Modify: `server/requirements.txt`
- Create: `server/core/config.py`
- Test: `server/tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

Create `server/tests/test_config.py`:

```python
from core.config import Settings


def test_settings_build_keycloak_jwks_url():
    settings = Settings(
        database_url="postgresql+psycopg://tertius:tertius@localhost:5432/tertius",
        keycloak_issuer="http://localhost:8080/realms/tertius",
        keycloak_audience="tertius-web",
        artifact_root="/tmp/tertius-artifacts",
    )

    assert settings.keycloak_jwks_url == "http://localhost:8080/realms/tertius/protocol/openid-connect/certs"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_config.py -q
```

Expected: failure with `ModuleNotFoundError: No module named 'core'`.

- [ ] **Step 3: Add dependencies**

Modify `server/requirements.txt` to:

```text
fastapi
uvicorn[standard]
pydantic
pydantic-settings
build123d
fpdf2
fonttools
SQLAlchemy>=2.0
psycopg[binary]
alembic
PyJWT[crypto]
httpx
pytest
pytest-asyncio
testcontainers[postgres]
```

- [ ] **Step 4: Add `server/core/config.py`**

Create `server/core/config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="postgresql+psycopg://tertius:tertius@localhost:5432/tertius")
    keycloak_issuer: str = Field(default="http://localhost:8080/realms/tertius")
    keycloak_audience: str = Field(default="tertius-web")
    artifact_root: str = Field(default="/tmp/tertius-artifacts")

    @property
    def keycloak_jwks_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run the test to verify it passes**

Run:

```bash
rtk pytest server/tests/test_config.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add server/requirements.txt server/core/config.py server/tests/test_config.py
git commit -m "chore: add server configuration for postgres and keycloak"
```

## Task 2: Postgres Schema And DB Session

**Files:**
- Create: `server/core/db.py`
- Create: `server/core/models.py`
- Create: `server/tests/conftest.py`
- Create: `server/alembic.ini`
- Create: `server/migrations/env.py`
- Create: `server/migrations/versions/0001_initial_multitenant_schema.py`
- Test: `server/tests/test_migrations.py`
- Test: `server/tests/test_models.py`

- [ ] **Step 1: Write the failing model test**

Create `server/tests/test_models.py`:

```python
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership


def test_multitenant_models_expose_expected_columns():
    assert "keycloak_subject" in AppUser.__table__.columns
    assert "tenant_id" in Project.__table__.columns
    assert "tenant_id" in ProjectFile.__table__.columns
    assert "role" in TenantMembership.__table__.columns
    assert Tenant.__tablename__ == "tenants"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_models.py -q
```

Expected: failure with `ModuleNotFoundError` or missing model symbols.

- [ ] **Step 3: Add `server/core/db.py`**

Create `server/core/db.py`:

```python
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 4: Add `server/core/models.py`**

Create `server/core/models.py`:

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    keycloak_subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    username: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_project_name_per_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    files: Mapped[list["ProjectFile"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ProjectFile(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "filename", name="uq_project_file_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    project: Mapped[Project] = relationship(back_populates="files")


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class SourceSnapshotFile(Base):
    __tablename__ = "source_snapshot_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class UserWorkspaceState(Base):
    __tablename__ = "user_workspace_state"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_workspace_user_tenant"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    active_project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="SET NULL"))
    active_file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("project_files.id", ondelete="SET NULL"))


class CompileJob(Base):
    __tablename__ = "compile_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    export_format: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    compile_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("compile_jobs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)


class TimusSettings(Base):
    __tablename__ = "timus_settings"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_timus_settings_user_project"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    stamp_text: Mapped[str] = mapped_column(String(32), nullable=False)
    show_redline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_hidden_lines: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scale: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=1.0)
    sheet_size: Mapped[str] = mapped_column(String(8), nullable=False, default="A4")
```

- [ ] **Step 5: Add Alembic files**

Create `server/alembic.ini`:

```ini
[alembic]
script_location = migrations
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `server/migrations/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from core.config import get_settings
from core.db import Base
from core import models

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `server/migrations/versions/0001_initial_multitenant_schema.py`:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0001_initial_multitenant_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("keycloak_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320)),
        sa.Column("username", sa.String(length=255)),
        sa.Column("display_name", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("keycloak_subject", name="uq_app_users_keycloak_subject"),
    )
    op.create_index("ix_app_users_keycloak_subject", "app_users", ["keycloak_subject"])
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_project_name_per_tenant"),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_table(
        "project_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "filename", name="uq_project_file_name"),
    )
    op.create_index("ix_project_files_tenant_id", "project_files", ["tenant_id"])
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_snapshots_tenant_id", "source_snapshots", ["tenant_id"])
    op.create_index("ix_source_snapshots_project_id", "source_snapshots", ["project_id"])
    op.create_table(
        "source_snapshot_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_source_snapshot_files_snapshot_id", "source_snapshot_files", ["snapshot_id"])
    op.create_table(
        "user_workspace_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active_project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("active_file_id", sa.Uuid(), sa.ForeignKey("project_files.id", ondelete="SET NULL")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_workspace_user_tenant"),
    )
    op.create_index("ix_user_workspace_state_user_id", "user_workspace_state", ["user_id"])
    op.create_index("ix_user_workspace_state_tenant_id", "user_workspace_state", ["tenant_id"])
    op.create_table(
        "compile_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("export_format", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_compile_jobs_tenant_id", "compile_jobs", ["tenant_id"])
    op.create_index("ix_compile_jobs_project_id", "compile_jobs", ["project_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), sa.ForeignKey("compile_jobs.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_table(
        "timus_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("stamp_text", sa.String(length=32), nullable=False),
        sa.Column("show_redline", sa.Boolean(), nullable=False),
        sa.Column("show_hidden_lines", sa.Boolean(), nullable=False),
        sa.Column("scale", sa.Numeric(12, 6), nullable=False),
        sa.Column("sheet_size", sa.String(length=8), nullable=False),
        sa.UniqueConstraint("user_id", "project_id", name="uq_timus_settings_user_project"),
    )
    op.create_index("ix_timus_settings_user_id", "timus_settings", ["user_id"])
    op.create_index("ix_timus_settings_tenant_id", "timus_settings", ["tenant_id"])
    op.create_index("ix_timus_settings_project_id", "timus_settings", ["project_id"])


def downgrade() -> None:
    op.drop_table("timus_settings")
    op.drop_table("artifacts")
    op.drop_table("compile_jobs")
    op.drop_table("user_workspace_state")
    op.drop_table("source_snapshot_files")
    op.drop_table("source_snapshots")
    op.drop_table("project_files")
    op.drop_table("projects")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
    op.drop_table("app_users")
```

- [ ] **Step 6: Add Testcontainers Postgres fixtures**

Create `server/tests/conftest.py`:

```python
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from core.db import Base


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
```

- [ ] **Step 7: Run tests**

Create `server/tests/test_migrations.py`:

```python
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from core.config import get_settings


def test_alembic_upgrade_creates_multitenant_schema(postgres_url: str, monkeypatch):
    server_dir = Path(__file__).parents[1]
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()

    config = Config(str(server_dir / "alembic.ini"))
    config.set_main_option("script_location", str(server_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", postgres_url)

    upgrade(config, "head")

    engine = create_engine(postgres_url, pool_pre_ping=True)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    engine.dispose()
    get_settings.cache_clear()

    assert "app_users" in table_names
    assert "tenants" in table_names
    assert "projects" in table_names
    assert "project_files" in table_names
    assert "artifacts" in table_names
```

Run:

```bash
rtk pytest server/tests/test_models.py server/tests/test_migrations.py -q
```

Expected: both tests pass against a Testcontainers-managed Postgres instance.

- [ ] **Step 8: Commit**

```bash
git add server/core/db.py server/core/models.py server/tests/conftest.py server/alembic.ini server/migrations server/tests/test_models.py server/tests/test_migrations.py
git commit -m "feat: add multitenant postgres schema"
```

## Task 3: Keycloak JWT Auth Context

**Files:**
- Create: `server/core/auth_types.py`
- Create: `server/core/auth.py`
- Create: `server/tests/test_auth.py`
- Modify: `server/main.py`

- [ ] **Step 1: Write the failing auth dependency test**

Create `server/tests/test_auth.py`:

```python
import pytest
from fastapi import HTTPException

from core.auth import claims_to_principal


def test_claims_to_principal_requires_subject():
    with pytest.raises(HTTPException) as exc:
        claims_to_principal({"email": "a@example.com"})

    assert exc.value.status_code == 401


def test_claims_to_principal_maps_keycloak_claims():
    principal = claims_to_principal(
        {
            "sub": "kc-user-1",
            "email": "a@example.com",
            "preferred_username": "alice",
            "name": "Alice Example",
        }
    )

    assert principal.keycloak_subject == "kc-user-1"
    assert principal.email == "a@example.com"
    assert principal.username == "alice"
    assert principal.display_name == "Alice Example"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_auth.py -q
```

Expected: failure because `core.auth` does not exist.

- [ ] **Step 3: Add `server/core/auth_types.py`**

Create `server/core/auth_types.py`:

```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    keycloak_subject: str
    email: str | None
    username: str | None
    display_name: str | None


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    tenant_id: UUID
    keycloak_subject: str
    email: str | None
```

- [ ] **Step 4: Add `server/core/auth.py`**

Create `server/core/auth.py`:

```python
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.config import get_settings
from core.db import get_db
from core.provisioning import provision_user_context


bearer = HTTPBearer(auto_error=False)


def claims_to_principal(claims: dict) -> Principal:
    subject = claims.get("sub")
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token subject is missing")
    return Principal(
        keycloak_subject=subject,
        email=claims.get("email"),
        username=claims.get("preferred_username"),
        display_name=claims.get("name"),
    )


def decode_keycloak_token(token: str) -> dict:
    settings = get_settings()
    jwk_client = PyJWKClient(settings.keycloak_jwks_url)
    signing_key = jwk_client.get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.keycloak_audience,
        issuer=settings.keycloak_issuer.rstrip("/"),
    )


def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> AuthContext:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        claims = decode_keycloak_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc
    principal = claims_to_principal(claims)
    return provision_user_context(db, principal)
```

- [ ] **Step 5: Add authenticated `/api/me` in `server/main.py`**

Modify `server/main.py` imports:

```python
from core.auth import AuthContext, get_auth_context
```

Add this route above workflow mounts:

```python
@app.get("/api/me")
def read_me(ctx: AuthContext = Depends(get_auth_context)):
    return {
        "user_id": str(ctx.user_id),
        "tenant_id": str(ctx.tenant_id),
        "email": ctx.email,
    }
```

Also add `Depends` to the FastAPI import:

```python
from fastapi import Depends, FastAPI
```

- [ ] **Step 6: Run auth tests**

Run:

```bash
rtk pytest server/tests/test_auth.py -q
```

Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
git add server/core/auth_types.py server/core/auth.py server/main.py server/tests/test_auth.py
git commit -m "feat: add keycloak jwt auth context"
```

## Task 4: First-Login Provisioning

**Files:**
- Create: `server/core/provisioning.py`
- Test: `server/tests/test_provisioning.py`

- [ ] **Step 1: Write the failing provisioning test**

Create `server/tests/test_provisioning.py`:

```python
from sqlalchemy import select

from core.auth_types import Principal
from core.models import Project, ProjectFile, TenantMembership
from core.provisioning import provision_user_context


def test_first_login_creates_tenant_membership_and_default_project(db_session):
    principal = Principal(
        keycloak_subject="kc-123",
        email="alice@example.com",
        username="alice",
        display_name="Alice Example",
    )

    ctx = provision_user_context(db_session, principal)
    project = db_session.scalar(select(Project).where(Project.tenant_id == ctx.tenant_id))
    membership = db_session.scalar(select(TenantMembership).where(TenantMembership.user_id == ctx.user_id))
    design_file = db_session.scalar(select(ProjectFile).where(ProjectFile.project_id == project.id, ProjectFile.filename == "design.py"))

    assert membership.role == "owner"
    assert project.name == "default_purlin"
    assert "build123d" in design_file.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_provisioning.py -q
```

Expected: failure because `core.provisioning` does not exist.

- [ ] **Step 3: Add `server/core/provisioning.py`**

Create `server/core/provisioning.py`:

```python
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext, Principal
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership, UserWorkspaceState, now_utc


DEFAULT_SCRIPT_PATH = Path(__file__).parent.parent / "workflows" / "intus" / "templates" / "default_purlin.py"


def _default_script() -> str:
    return DEFAULT_SCRIPT_PATH.read_text(encoding="utf-8")


def provision_user_context(db: Session, principal: Principal) -> AuthContext:
    user = db.scalar(select(AppUser).where(AppUser.keycloak_subject == principal.keycloak_subject))
    if user is None:
        user = AppUser(
            keycloak_subject=principal.keycloak_subject,
            email=principal.email,
            username=principal.username,
            display_name=principal.display_name,
        )
        db.add(user)
        db.flush()
        tenant_name = principal.display_name or principal.username or principal.email or "Personal Workspace"
        tenant = Tenant(name=tenant_name)
        db.add(tenant)
        db.flush()
        db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner"))
        project = Project(tenant_id=tenant.id, name="default_purlin", created_by=user.id)
        db.add(project)
        db.flush()
        design_file = ProjectFile(tenant_id=tenant.id, project_id=project.id, filename="design.py", content=_default_script())
        db.add(design_file)
        db.flush()
        db.add(UserWorkspaceState(user_id=user.id, tenant_id=tenant.id, active_project_id=project.id, active_file_id=design_file.id))
        db.commit()
        return AuthContext(user_id=user.id, tenant_id=tenant.id, keycloak_subject=user.keycloak_subject, email=user.email)

    user.email = principal.email
    user.username = principal.username
    user.display_name = principal.display_name
    user.last_seen_at = now_utc()
    membership = db.scalar(select(TenantMembership).where(TenantMembership.user_id == user.id))
    db.commit()
    return AuthContext(user_id=user.id, tenant_id=membership.tenant_id, keycloak_subject=user.keycloak_subject, email=user.email)
```

- [ ] **Step 4: Run the provisioning test**

Run:

```bash
rtk pytest server/tests/test_provisioning.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/core/provisioning.py server/tests/test_provisioning.py
git commit -m "feat: provision clean-slate tenants on first login"
```

## Task 5: Tenant-Scoped Project Repository

**Files:**
- Create: `server/core/repositories.py`
- Test: `server/tests/test_repositories.py`

- [ ] **Step 1: Write the failing repository isolation test**

Create `server/tests/test_repositories.py`:

```python
import pytest
from sqlalchemy.orm import Session

from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership
from core.repositories import ProjectRepository


def seed_two_tenants(db: Session):
    user_a = AppUser(keycloak_subject="a")
    user_b = AppUser(keycloak_subject="b")
    tenant_a = Tenant(name="A")
    tenant_b = Tenant(name="B")
    db.add_all([user_a, user_b, tenant_a, tenant_b])
    db.flush()
    db.add_all([
        TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"),
        TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"),
    ])
    project_a = Project(tenant_id=tenant_a.id, name="same_name", created_by=user_a.id)
    project_b = Project(tenant_id=tenant_b.id, name="same_name", created_by=user_b.id)
    db.add_all([project_a, project_b])
    db.flush()
    db.add_all([
        ProjectFile(tenant_id=tenant_a.id, project_id=project_a.id, filename="design.py", content="a = 1"),
        ProjectFile(tenant_id=tenant_b.id, project_id=project_b.id, filename="design.py", content="b = 2"),
    ])
    db.commit()
    return tenant_a.id, tenant_b.id


def test_project_repository_only_reads_current_tenant(db_session):
    tenant_a, tenant_b = seed_two_tenants(db_session)
    repo_a = ProjectRepository(db_session, tenant_a)
    repo_b = ProjectRepository(db_session, tenant_b)

    assert repo_a.get_code("same_name", "design.py") == "a = 1"
    assert repo_b.get_code("same_name", "design.py") == "b = 2"


def test_project_repository_rejects_invalid_filename(db_session):
    tenant_a, _ = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, tenant_a)
    with pytest.raises(ValueError):
        repo.get_code("same_name", "../design.py")
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_repositories.py -q
```

Expected: failure because `ProjectRepository` does not exist.

- [ ] **Step 3: Add `server/core/repositories.py`**

Create `server/core/repositories.py`:

```python
import hashlib
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Project, ProjectFile, SourceSnapshot, SourceSnapshotFile, UserWorkspaceState, now_utc


FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.py$")


def require_valid_python_filename(filename: str) -> str:
    if not FILENAME_RE.fullmatch(filename):
        raise ValueError("Invalid filename")
    return filename


class ProjectRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def list_projects(self) -> list[str]:
        rows = self.db.scalars(select(Project).where(Project.tenant_id == self.tenant_id).order_by(Project.name)).all()
        return [row.name for row in rows]

    def get_project(self, name: str) -> Project | None:
        return self.db.scalar(select(Project).where(Project.tenant_id == self.tenant_id, Project.name == name))

    def create_project(self, name: str, user_id: UUID, default_code: str) -> Project:
        project = Project(tenant_id=self.tenant_id, name=name, created_by=user_id)
        self.db.add(project)
        self.db.flush()
        self.db.add(ProjectFile(tenant_id=self.tenant_id, project_id=project.id, filename="design.py", content=default_code))
        self.db.commit()
        return project

    def list_files(self, project_name: str) -> list[str]:
        project = self.get_project(project_name)
        if project is None:
            return []
        rows = self.db.scalars(
            select(ProjectFile)
            .where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)
            .order_by(ProjectFile.filename)
        ).all()
        names = [row.filename for row in rows]
        if "design.py" in names:
            names.remove("design.py")
            names.insert(0, "design.py")
        return names

    def get_code(self, project_name: str, filename: str) -> str | None:
        filename = require_valid_python_filename(filename)
        project = self.get_project(project_name)
        if project is None:
            return None
        file_row = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        return None if file_row is None else file_row.content

    def save_code(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
        filename = require_valid_python_filename(filename)
        project = self.get_project(project_name)
        if project is None:
            return False
        file_row = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        if file_row is None:
            file_row = ProjectFile(tenant_id=self.tenant_id, project_id=project.id, filename=filename, content=content)
            self.db.add(file_row)
        else:
            file_row.content = content
            file_row.updated_at = now_utc()
        project.updated_at = now_utc()
        self._snapshot(project, user_id, message)
        self.db.commit()
        return True

    def delete_file(self, project_name: str, filename: str) -> bool:
        filename = require_valid_python_filename(filename)
        if filename == "design.py":
            raise ValueError("Cannot delete design.py")
        project = self.get_project(project_name)
        if project is None:
            return False
        file_row = self.db.scalar(
            select(ProjectFile).where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id, ProjectFile.filename == filename)
        )
        if file_row is None:
            return False
        self.db.delete(file_row)
        self.db.commit()
        return True

    def files_for_runtime(self, project_name: str) -> dict[str, str] | None:
        project = self.get_project(project_name)
        if project is None:
            return None
        files = self.db.scalars(select(ProjectFile).where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)).all()
        return {file.filename: file.content for file in files}

    def _snapshot(self, project: Project, user_id: UUID, message: str) -> None:
        files = self.db.scalars(select(ProjectFile).where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)).all()
        digest_input = "\n".join(f"{file.filename}:{file.content}" for file in sorted(files, key=lambda item: item.filename))
        snapshot = SourceSnapshot(
            tenant_id=self.tenant_id,
            project_id=project.id,
            message=message,
            content_hash=hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
            created_by=user_id,
        )
        self.db.add(snapshot)
        self.db.flush()
        for file in files:
            self.db.add(SourceSnapshotFile(snapshot_id=snapshot.id, filename=file.filename, content=file.content))
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
rtk pytest server/tests/test_repositories.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add server/core/repositories.py server/tests/test_repositories.py
git commit -m "feat: add tenant scoped project repository"
```

## Task 6: Artifact Storage And Runtime Hydration

**Files:**
- Create: `server/core/artifacts.py`
- Create: `server/core/compile_runtime.py`
- Test: `server/tests/test_artifacts.py`

- [ ] **Step 1: Write failing artifact and hydration tests**

Create `server/tests/test_artifacts.py`:

```python
from pathlib import Path
from uuid import uuid4

from core.artifacts import ArtifactStore
from core.compile_runtime import hydrate_project_files


def test_artifact_store_writes_tenant_scoped_file(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    tenant_id = uuid4()
    project_id = uuid4()

    result = store.write_bytes(tenant_id, project_id, "stl", b"solid test")

    assert result.storage_key.endswith(".stl")
    assert store.path_for(result.storage_key).read_bytes() == b"solid test"
    assert str(tenant_id) in result.storage_key


def test_hydrate_project_files_creates_python_files(tmp_path: Path):
    with hydrate_project_files({"design.py": "x = 1", "helpers.py": "y = 2"}) as project_dir:
        assert (project_dir / "design.py").read_text(encoding="utf-8") == "x = 1"
        assert (project_dir / "helpers.py").read_text(encoding="utf-8") == "y = 2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest server/tests/test_artifacts.py -q
```

Expected: failure because `core.artifacts` and `core.compile_runtime` do not exist.

- [ ] **Step 3: Add `server/core/artifacts.py`**

Create `server/core/artifacts.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


CONTENT_TYPES = {
    "stl": "application/octet-stream",
    "step": "application/step",
    "pdf": "application/pdf",
}


@dataclass(frozen=True)
class StoredArtifact:
    storage_key: str
    content_type: str
    byte_size: int


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_bytes(self, tenant_id: UUID, project_id: UUID, kind: str, content: bytes) -> StoredArtifact:
        ext = kind.lower()
        storage_key = f"{tenant_id}/{project_id}/{uuid4()}.{ext}"
        path = self.path_for(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredArtifact(storage_key=storage_key, content_type=CONTENT_TYPES.get(ext, "application/octet-stream"), byte_size=len(content))

    def path_for(self, storage_key: str) -> Path:
        return self.root / storage_key
```

- [ ] **Step 4: Add `server/core/compile_runtime.py`**

Create `server/core/compile_runtime.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from core.repositories import require_valid_python_filename


@contextmanager
def hydrate_project_files(files: dict[str, str]) -> Iterator[Path]:
    with TemporaryDirectory(prefix="tertius-project-") as tmp:
        project_dir = Path(tmp)
        for filename, content in files.items():
            safe_name = require_valid_python_filename(filename)
            (project_dir / safe_name).write_text(content, encoding="utf-8")
        yield project_dir
```

- [ ] **Step 5: Run tests**

Run:

```bash
rtk pytest server/tests/test_artifacts.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add server/core/artifacts.py server/core/compile_runtime.py server/tests/test_artifacts.py
git commit -m "feat: add tenant scoped artifact storage"
```

## Task 7: Intus Repository-Backed Endpoints

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Test: `server/tests/test_intus_endpoints.py`

- [ ] **Step 1: Write endpoint tests using auth and DB overrides**

Create `server/tests/test_intus_endpoints.py`:

```python
from uuid import uuid4

from fastapi.testclient import TestClient

from core.auth import AuthContext, get_auth_context
from core.db import get_db
from core.models import AppUser, Project, ProjectFile, Tenant, TenantMembership
from workflows.intus.intus_server import app


def make_test_client(db_session, tenant_id, user_id):
    def override_db():
        yield db_session

    def override_auth():
        return AuthContext(user_id=user_id, tenant_id=tenant_id, keycloak_subject="kc-test", email="test@example.com")

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return TestClient(app)


def test_projects_are_scoped_to_authenticated_tenant(db_session):
    user_a = AppUser(id=uuid4(), keycloak_subject="a")
    user_b = AppUser(id=uuid4(), keycloak_subject="b")
    tenant_a = Tenant(id=uuid4(), name="A")
    tenant_b = Tenant(id=uuid4(), name="B")
    db_session.add_all([user_a, user_b, tenant_a, tenant_b])
    db_session.flush()
    db_session.add_all([
        TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"),
        TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"),
    ])
    db_session.add(Project(tenant_id=tenant_a.id, name="a_project", created_by=user_a.id))
    db_session.add(Project(tenant_id=tenant_b.id, name="b_project", created_by=user_b.id))
    db_session.commit()

    client = make_test_client(db_session, tenant_a.id, user_a.id)

    response = client.get("/projects")

    assert response.status_code == 200
    assert response.json() == {"projects": ["a_project"]}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_intus_endpoints.py -q
```

Expected: failure because current Intus reads filesystem projects and does not use auth context.

- [ ] **Step 3: Refactor Intus endpoint dependencies**

In `server/workflows/intus/intus_server.py`, import:

```python
from fastapi import Depends, FastAPI
from sqlalchemy.orm import Session

from core.auth import AuthContext, get_auth_context
from core.db import get_db
from core.repositories import ProjectRepository
```

Remove `CACHE_ROOT`, `PROJECTS_DIR`, `ACTIVE_STL`, `ACTIVE_PROJECT`, `auto_commit`, and `init_defaults_if_needed`. Keep `DEFAULT_PURLIN`.

- [ ] **Step 4: Replace project list/create/code/file endpoints**

Use this pattern in each Intus endpoint:

```python
@app.get("/projects")
def list_projects(ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    return {"projects": ProjectRepository(db, ctx.tenant_id).list_projects()}
```

For create:

```python
@app.post("/projects/{name}/new")
def new_project(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    repo = ProjectRepository(db, ctx.tenant_id)
    if repo.get_project(name):
        return JSONResponse(status_code=400, content={"error": "Project already exists"})
    repo.create_project(name, ctx.user_id, DEFAULT_PURLIN)
    return {"success": True, "project": name}
```

For code:

```python
@app.get("/projects/{name}/code")
def get_code(name: str, file: str = "design.py", ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    try:
        code = ProjectRepository(db, ctx.tenant_id).get_code(name, file)
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    if code is None:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"code": code}
```

For files:

```python
@app.get("/projects/{name}/files")
def list_files(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    files = ProjectRepository(db, ctx.tenant_id).list_files(name)
    if not files:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    return {"files": files}
```

For save:

```python
@app.post("/projects/{name}/save")
def save_code(name: str, req: CodeRequest, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    file = req.file or "design.py"
    try:
        saved = ProjectRepository(db, ctx.tenant_id).save_code(name, file, req.code, ctx.user_id, f"Manual save {file} via Intus")
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid filename"})
    if not saved:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    return {"success": True}
```

For delete:

```python
@app.delete("/projects/{name}/file")
def delete_file(name: str, file: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    try:
        deleted = ProjectRepository(db, ctx.tenant_id).delete_file(name, file)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return {"success": True}
```

For history:

```python
@app.get("/projects/{name}/git_status")
def get_git_status(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
    history = ProjectRepository(db, ctx.tenant_id).snapshot_history(name)
    if history is None:
        return JSONResponse(status_code=404, content={"error": "Project not found"})
    commit = history[0].split(" ", 1)[0] if history else ""
    return {"is_git": True, "commit": commit, "history": history}
```

Add this method to `ProjectRepository`:

```python
    def snapshot_history(self, project_name: str) -> list[str] | None:
        project = self.get_project(project_name)
        if project is None:
            return None
        rows = self.db.scalars(
            select(SourceSnapshot)
            .where(SourceSnapshot.tenant_id == self.tenant_id, SourceSnapshot.project_id == project.id)
            .order_by(SourceSnapshot.created_at.desc())
            .limit(50)
        ).all()
        return [f"{row.content_hash[:7]} {row.message}" for row in rows]
```

- [ ] **Step 5: Run endpoint tests**

Run:

```bash
rtk pytest server/tests/test_intus_endpoints.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add server/workflows/intus/intus_server.py server/tests/test_intus_endpoints.py
git commit -m "feat: scope intus projects by authenticated tenant"
```

## Task 8: Compile Flow With DB Files And Artifact Metadata

**Files:**
- Modify: `server/workflows/intus/intus_server.py`
- Modify: `server/core/repositories.py`
- Test: `server/tests/test_compile_flow.py`

- [ ] **Step 1: Write failing compile metadata test**

Create `server/tests/test_compile_flow.py`:

```python
from sqlalchemy import select

from core.models import Artifact, CompileJob


def test_compile_records_failed_job_for_invalid_code(authenticated_intus_client, db_session):
    response = authenticated_intus_client.post(
        "/projects/default_purlin/compile",
        json={"code": "raise RuntimeError('boom')", "export_format": "stl", "file": "design.py"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert db_session.scalar(select(CompileJob)).status == "failed"
    assert db_session.scalar(select(Artifact)) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
rtk pytest server/tests/test_compile_flow.py -q
```

Expected: failure because compile jobs and artifacts are not recorded.

- [ ] **Step 3: Add repository helpers for compile jobs and artifacts**

In `server/core/repositories.py`, add:

```python
from core.models import Artifact, CompileJob


class CompileRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def start_job(self, project_id: UUID, user_id: UUID, export_format: str) -> CompileJob:
        job = CompileJob(
            tenant_id=self.tenant_id,
            project_id=project_id,
            requested_by=user_id,
            status="running",
            export_format=export_format,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def finish_job(self, job: CompileJob, status: str, error: str | None = None) -> None:
        job.status = status
        job.error = error
        job.finished_at = now_utc()

    def record_artifact(self, project_id: UUID, job_id: UUID | None, kind: str, storage_key: str, content_type: str, byte_size: int) -> Artifact:
        artifact = Artifact(
            tenant_id=self.tenant_id,
            project_id=project_id,
            compile_job_id=job_id,
            kind=kind,
            storage_key=storage_key,
            content_type=content_type,
            byte_size=byte_size,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact
```

- [ ] **Step 4: Refactor Intus compile endpoint**

In `server/workflows/intus/intus_server.py`, use `ProjectRepository.files_for_runtime()`, `hydrate_project_files()`, `ArtifactStore`, and `CompileRepository`. Save the edited file in DB before hydrating runtime files. On success, export into memory or a temp file, write bytes through `ArtifactStore`, record an `Artifact`, finish the job as `succeeded`, and return:

```python
return {"success": True, "format": ext, "artifact_id": str(artifact.id)}
```

On exception, finish the job as `failed`, commit, and return the existing error shape:

```python
return JSONResponse(status_code=200, content={"success": False, "error": tb, "short": str(e)})
```

- [ ] **Step 5: Run compile flow tests**

Run:

```bash
rtk pytest server/tests/test_compile_flow.py -q
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
git add server/workflows/intus/intus_server.py server/core/repositories.py server/tests/test_compile_flow.py
git commit -m "feat: record compile jobs and artifacts"
```

## Task 9: Artus, Extus, And Timus Tenant Isolation

**Files:**
- Modify: `server/workflows/artus/artus_server.py`
- Modify: `server/workflows/extus/extus_server.py`
- Modify: `server/workflows/timus/timus_server.py`
- Test: `server/tests/test_workflow_isolation.py`

- [ ] **Step 1: Write failing workflow isolation tests**

Create `server/tests/test_workflow_isolation.py`:

```python
def test_artus_features_require_authenticated_active_project(authenticated_artus_client):
    response = authenticated_artus_client.get("/features")

    assert response.status_code in (200, 404)
    assert "No active project" not in response.text or response.status_code == 404


def test_extus_model_is_not_global(authenticated_extus_client):
    response = authenticated_extus_client.get("/project_name")

    assert response.status_code == 200
    assert "active_project.txt" not in response.text


def test_timus_settings_round_trip(authenticated_timus_client):
    payload = {
        "title": "PART A",
        "stamp_text": "APPROVED",
        "show_redline": True,
        "show_hidden_lines": False,
        "scale": 0.25,
        "sheet_size": "A3",
    }

    save_response = authenticated_timus_client.put("/projects/default_purlin/settings", json=payload)
    load_response = authenticated_timus_client.get("/projects/default_purlin/settings")

    assert save_response.status_code == 200
    assert load_response.json()["sheet_size"] == "A3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
rtk pytest server/tests/test_workflow_isolation.py -q
```

Expected: failures because the workflow servers still read global files or lack settings endpoints.

- [ ] **Step 3: Refactor Artus active project lookup**

In `server/workflows/artus/artus_server.py`, replace `get_active_script()` with a DB-backed function:

```python
def get_active_design_code(db: Session, ctx: AuthContext) -> tuple[str, str] | None:
    state = db.scalar(select(UserWorkspaceState).where(UserWorkspaceState.user_id == ctx.user_id, UserWorkspaceState.tenant_id == ctx.tenant_id))
    if state is None or state.active_project_id is None:
        return None
    project = db.scalar(select(Project).where(Project.tenant_id == ctx.tenant_id, Project.id == state.active_project_id))
    file_row = db.scalar(select(ProjectFile).where(ProjectFile.tenant_id == ctx.tenant_id, ProjectFile.project_id == project.id, ProjectFile.filename == "design.py"))
    if project is None or file_row is None:
        return None
    return project.name, file_row.content
```

Update `/features` and `/update_features` to use `AuthContext` and write changed content back to `ProjectFile.content`.

- [ ] **Step 4: Refactor Extus latest artifact lookup**

In `server/workflows/extus/extus_server.py`, replace `ACTIVE_STL` and `ACTIVE_PROJECT` with DB lookups. `/project_name` reads `UserWorkspaceState` and `Project`. `/status` reads the latest `Artifact` for active project and kind `stl`. `/model` returns `FileResponse(ArtifactStore.path_for(artifact.storage_key))`.

- [ ] **Step 5: Add Timus settings endpoints**

In `server/workflows/timus/timus_server.py`, add Pydantic model:

```python
class TimusSettingsRequest(BaseModel):
    title: str
    stamp_text: str
    show_redline: bool
    show_hidden_lines: bool
    scale: float
    sheet_size: str
```

Add:

```python
@app.get("/projects/{name}/settings")
def get_timus_settings(name: str, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
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
    return {
        "title": settings.title,
        "stamp_text": settings.stamp_text,
        "show_redline": settings.show_redline,
        "show_hidden_lines": settings.show_hidden_lines,
        "scale": float(settings.scale),
        "sheet_size": settings.sheet_size,
    }


@app.put("/projects/{name}/settings")
def put_timus_settings(name: str, req: TimusSettingsRequest, ctx: AuthContext = Depends(get_auth_context), db: Session = Depends(get_db)):
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
        settings = TimusSettings(user_id=ctx.user_id, tenant_id=ctx.tenant_id, project_id=project.id)
        db.add(settings)
    settings.title = req.title
    settings.stamp_text = req.stamp_text
    settings.show_redline = req.show_redline
    settings.show_hidden_lines = req.show_hidden_lines
    settings.scale = req.scale
    settings.sheet_size = req.sheet_size
    db.commit()
    return {"success": True}
```

Persist settings to `TimusSettings` scoped by `user_id`, `tenant_id`, and `project_id`.

- [ ] **Step 6: Run workflow tests**

Run:

```bash
rtk pytest server/tests/test_workflow_isolation.py -q
```

Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add server/workflows/artus/artus_server.py server/workflows/extus/extus_server.py server/workflows/timus/timus_server.py server/tests/test_workflow_isolation.py
git commit -m "feat: isolate artus extus and timus by tenant"
```

## Task 10: Frontend Keycloak Login And Authenticated API Client

**Files:**
- Modify: `ui/package.json`
- Create: `ui/src/auth/keycloak.ts`
- Create: `ui/src/auth/AuthProvider.tsx`
- Create: `ui/src/api/client.ts`
- Modify: `ui/src/main.tsx`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Add frontend dependency**

Run:

```bash
cd ui
npm install oidc-client-ts
```

Expected: `package.json` and `package-lock.json` include `oidc-client-ts`.

- [ ] **Step 2: Create Keycloak OIDC client**

Create `ui/src/auth/keycloak.ts`:

```typescript
import { UserManager, WebStorageStateStore } from 'oidc-client-ts';

export const userManager = new UserManager({
  authority: import.meta.env.VITE_KEYCLOAK_AUTHORITY,
  client_id: import.meta.env.VITE_KEYCLOAK_CLIENT_ID,
  redirect_uri: `${window.location.origin}/`,
  post_logout_redirect_uri: `${window.location.origin}/`,
  response_type: 'code',
  scope: 'openid profile email',
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
});
```

- [ ] **Step 3: Create auth provider**

Create `ui/src/auth/AuthProvider.tsx`:

```typescript
import React, { createContext, useContext, useEffect, useState } from 'react';
import type { User } from 'oidc-client-ts';
import { userManager } from './keycloak';

interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  login: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        if (window.location.search.includes('code=')) {
          const callbackUser = await userManager.signinRedirectCallback();
          setUser(callbackUser);
          window.history.replaceState({}, document.title, window.location.pathname);
          return;
        }
        setUser(await userManager.getUser());
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, []);

  const value: AuthState = {
    user,
    token: user?.access_token ?? null,
    isLoading,
    login: () => userManager.signinRedirect(),
    logout: () => userManager.signoutRedirect(),
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
};
```

- [ ] **Step 4: Create authenticated fetch wrapper**

Create `ui/src/api/client.ts`:

```typescript
export const apiFetch = async (url: string, token: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  return fetch(url, { ...init, headers });
};
```

- [ ] **Step 5: Wrap app in auth provider**

Modify `ui/src/main.tsx`:

```typescript
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthProvider'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
```

- [ ] **Step 6: Gate app rendering in `ui/src/App.tsx`**

In `App`, import `useAuth` and render:

```typescript
const { user, isLoading, login, logout } = useAuth();

if (isLoading) {
  return <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-300">Loading...</div>;
}

if (!user) {
  login();
  return <div className="flex h-screen items-center justify-center bg-slate-950 text-slate-300">Redirecting to login...</div>;
}
```

Add a small logout button in the tab header:

```tsx
<button onClick={logout} className="px-3 py-2 mb-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition-colors">
  Sign out
</button>
```

- [ ] **Step 7: Build frontend**

Run:

```bash
cd ui
npm run build
```

Expected: TypeScript compile and Vite build complete successfully.

- [ ] **Step 8: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src/auth ui/src/api ui/src/main.tsx ui/src/App.tsx
git commit -m "feat: add keycloak frontend authentication"
```

## Task 11: Thread Auth Through Workflow UI

**Files:**
- Modify: `ui/src/workflows/intus/ui/CompilerTab.tsx`
- Modify: `ui/src/workflows/artus/ui/FeatureTreeTab.tsx`
- Modify: `ui/src/workflows/extus/ui/ViewerTab.tsx`
- Modify: `ui/src/workflows/timus/ui/DraftingTab.tsx`

- [ ] **Step 1: Replace raw fetch calls**

In each workflow component, import:

```typescript
import { apiFetch } from '../../../api/client';
import { useAuth } from '../../../auth/AuthProvider';
```

Use the correct relative path for nested workflow UI folders. Inside the component:

```typescript
const { token } = useAuth();
if (!token) return null;
```

Replace:

```typescript
fetch(`${serverUrl}/projects`)
```

with:

```typescript
apiFetch(`${serverUrl}/projects`, token)
```

Apply this to every API call in the four workflow UI files.

- [ ] **Step 2: Remove project and Timus localStorage state**

In `CompilerTab.tsx`, remove:

```typescript
const last = localStorage.getItem('intus_last_project');
localStorage.setItem('intus_last_project', name);
```

Use the first server-returned project when no active project is already selected.

In `DraftingTab.tsx`, remove all reads and writes for:

```typescript
localStorage.getItem(`timus_settings_${activeProject}`)
localStorage.setItem(`timus_settings_${activeProject}`, JSON.stringify(settings))
localStorage.getItem('intus_last_project')
```

Load and save Timus settings with:

```typescript
apiFetch(`${serverUrl}/projects/${activeProject}/settings`, token)
apiFetch(`${serverUrl}/projects/${activeProject}/settings`, token, {
  method: 'PUT',
  body: JSON.stringify(settings),
})
```

- [ ] **Step 3: Build frontend**

Run:

```bash
cd ui
npm run build
```

Expected: TypeScript compile and Vite build complete successfully.

- [ ] **Step 4: Commit**

```bash
git add ui/src/workflows
git commit -m "feat: send keycloak tokens from workflow api calls"
```

## Task 12: Local Development Stack

**Files:**
- Create: `docker-compose.yml`
- Modify: `Dockerfile`
- Modify: `README.md`

- [ ] **Step 1: Create local Postgres and Keycloak compose stack**

Create `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: tertius
      POSTGRES_USER: tertius
      POSTGRES_PASSWORD: tertius
    ports:
      - "5432:5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data

  keycloak:
    image: quay.io/keycloak/keycloak:25.0
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"

volumes:
  postgres-data:
```

- [ ] **Step 2: Modify Dockerfile for artifact directory**

Add before `EXPOSE 8000`:

```dockerfile
RUN mkdir -p /app/artifacts
ENV ARTIFACT_ROOT=/app/artifacts
```

- [ ] **Step 3: Update README development notes**

Add:

```markdown
### Authenticated local development

Start Postgres and Keycloak:

```bash
docker compose up -d postgres keycloak
```

Create a Keycloak realm named `tertius` and a public OIDC client named `tertius-web` with redirect URI `http://localhost:5173/*`.

Server environment:

```bash
export DATABASE_URL=postgresql+psycopg://tertius:tertius@localhost:5432/tertius
export KEYCLOAK_ISSUER=http://localhost:8080/realms/tertius
export KEYCLOAK_AUDIENCE=tertius-web
export ARTIFACT_ROOT=/tmp/tertius-artifacts
```

Frontend environment:

```bash
VITE_KEYCLOAK_AUTHORITY=http://localhost:8080/realms/tertius
VITE_KEYCLOAK_CLIENT_ID=tertius-web
```
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml Dockerfile README.md
git commit -m "docs: add keycloak postgres local development stack"
```

## Task 13: Final Verification

**Files:**
- Modify files only if earlier verification reveals defects.

- [ ] **Step 1: Run server tests**

Run:

```bash
rtk pytest server/tests -q
```

Expected: all server tests pass against Testcontainers-managed Postgres. Docker must be running before this command.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd ui
npm run build
```

Expected: TypeScript compile and Vite build complete successfully.

- [ ] **Step 3: Run local smoke test**

Run:

```bash
docker compose up -d postgres keycloak
```

Expected: Postgres and Keycloak containers are healthy or running.

Run:

```bash
cd server
alembic upgrade head
```

Expected: migration completes and creates the multitenant schema.

- [ ] **Step 4: Manual browser verification**

Start backend and frontend. In the browser:

1. Open `http://localhost:5173`.
2. Verify the app redirects to Keycloak.
3. Log in as User A.
4. Verify `default_purlin` exists.
5. Create project `tenant_a_only`.
6. Log out.
7. Log in as User B.
8. Verify `tenant_a_only` is not listed.
9. Compile User B's `default_purlin`.
10. Verify Extus shows User B's active project and model.
11. Change Timus settings, refresh, and verify the settings persist for User B.

- [ ] **Step 5: Commit final fixes**

```bash
git add server ui Dockerfile docker-compose.yml README.md
git commit -m "test: verify postgres keycloak multitenancy"
```

## Self-Review

- Spec coverage: The plan covers Keycloak login redirect, JWT validation, clean-slate first-login provisioning, tenant-scoped Postgres schema, source persistence, active workspace state, artifact metadata, Intus/Artus/Extus/Timus changes, frontend API token threading, local development stack, and tenant isolation tests.
- Placeholder scan: The plan intentionally omits migration/import work because the requested direction is clean slate.
- Type consistency: `AuthContext`, `ProjectRepository`, `CompileRepository`, `ArtifactStore`, `TimusSettings`, and `ProjectFile` names are used consistently across tasks.
