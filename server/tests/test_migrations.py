from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from alembic.command import upgrade
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from core.config import get_settings
from core.db import Base
from core import models  # noqa: F401 - register models on Base.metadata


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
    assert "compile_jobs" in table_names
    assert "structural_analysis_results" in table_names
    artifact_columns = {
        column["name"]: column for column in inspector.get_columns("artifacts")
    }
    assert "content" in artifact_columns
    assert str(artifact_columns["content"]["type"]).lower() in {"bytea", "blob", "largebinary"}
    assert artifact_columns["content"]["nullable"] is True
    compile_job_columns = {
        column["name"]: column for column in inspector.get_columns("compile_jobs")
    }
    assert "claim_token" in compile_job_columns
    assert "claimed_at" in compile_job_columns
    assert "lease_expires_at" in compile_job_columns
    assert "attempt_count" in compile_job_columns
    llm_edit_job_columns = {
        column["name"]: column
        for column in inspector.get_columns("llm_edit_jobs")
    }
    assert llm_edit_job_columns["progress_payload"]["nullable"] is False
    assert llm_edit_job_columns["progress_payload"]["default"] is None

    assert "compile_job_files" in table_names
    snapshot_columns = {
        column["name"]: column for column in inspector.get_columns("compile_job_files")
    }
    assert {
        "id",
        "compile_job_id",
        "tenant_id",
        "project_id",
        "filename",
        "content",
        "created_at",
    } <= set(snapshot_columns)
    structural_result_columns = {
        column["name"]: column
        for column in inspector.get_columns("structural_analysis_results")
    }
    assert {
        "key_digest",
        "design_digest",
        "configuration_digest",
        "site_digest",
        "engine_version",
        "snapshot_schema_version",
        "combination_id",
        "snapshot",
        "calculation_duration_seconds",
    } <= set(structural_result_columns)
    assert str(structural_result_columns["snapshot"]["type"]).lower() == "jsonb"


def test_alembic_head_matches_sqlalchemy_models(postgres_url: str, monkeypatch):
    server_dir = Path(__file__).parents[1]
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()

    engine = create_engine(postgres_url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    config = Config(str(server_dir / "alembic.ini"))
    config.set_main_option("script_location", str(server_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", postgres_url)

    upgrade(config, "head")

    with engine.connect() as connection:
        migration_context = MigrationContext.configure(connection)
        diffs = compare_metadata(migration_context, Base.metadata)

    engine.dispose()
    get_settings.cache_clear()

    assert diffs == []


def test_progress_migration_backfills_existing_llm_edit_job(
    postgres_url: str, monkeypatch
):
    server_dir = Path(__file__).parents[1]
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    engine = create_engine(postgres_url, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    config = Config(str(server_dir / "alembic.ini"))
    config.set_main_option("script_location", str(server_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", postgres_url)
    upgrade(config, "0009_compile_llm_origin")

    user_id = uuid4()
    tenant_id = uuid4()
    project_id = uuid4()
    job_id = uuid4()
    created_at = datetime(2026, 7, 28, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO app_users (
                    id, keycloak_subject, created_at, last_seen_at
                ) VALUES (
                    :id, :subject, :created_at, :created_at
                )
                """
            ),
            {
                "id": user_id,
                "subject": "migration-existing-user",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO tenants (id, name, created_at)
                VALUES (:id, :name, :created_at)
                """
            ),
            {
                "id": tenant_id,
                "name": "Migration tenant",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO projects (
                    id, tenant_id, name, created_by, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :name, :created_by, :created_at, :created_at
                )
                """
            ),
            {
                "id": project_id,
                "tenant_id": tenant_id,
                "name": "migration-project",
                "created_by": user_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO llm_edit_jobs (
                    id, tenant_id, project_id, requested_by, status,
                    retryable, request_payload, attempt_count, created_at
                ) VALUES (
                    :id, :tenant_id, :project_id, :requested_by, :status,
                    false, CAST(:request_payload AS JSON), 0, :created_at
                )
                """
            ),
            {
                "id": job_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "requested_by": user_id,
                "status": "running",
                "request_payload": "{}",
                "created_at": created_at,
            },
        )

    upgrade(config, "head")
    with engine.connect() as connection:
        progress_payload = connection.scalar(
            text(
                "SELECT progress_payload FROM llm_edit_jobs WHERE id = :job_id"
            ),
            {"job_id": job_id},
        )
    progress_column = {
        column["name"]: column
        for column in inspect(engine).get_columns("llm_edit_jobs")
    }["progress_payload"]

    engine.dispose()
    get_settings.cache_clear()
    assert progress_payload == {}
    assert progress_column["nullable"] is False
    assert progress_column["default"] is None
