from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

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

    engine = create_engine(postgres_url, pool_pre_ping=True)
    upgrade(config, "head")

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
    artifact_columns = {
        column["name"]: column for column in inspector.get_columns("artifacts")
    }
    assert "content" in artifact_columns
    assert str(artifact_columns["content"]["type"]).lower() in {
        "bytea",
        "blob",
        "largebinary",
    }
    assert artifact_columns["content"]["nullable"] is True
    compile_job_columns = {
        column["name"]: column for column in inspector.get_columns("compile_jobs")
    }
    assert "claim_token" in compile_job_columns
    assert "claimed_at" in compile_job_columns
    assert "lease_expires_at" in compile_job_columns
    assert "attempt_count" in compile_job_columns
    llm_edit_job_columns = {
        column["name"]: column for column in inspector.get_columns("llm_edit_jobs")
    }
    assert llm_edit_job_columns["progress_payload"]["nullable"] is False
    assert llm_edit_job_columns["progress_payload"]["default"] is None

    assert "compile_job_files" in table_names
    assert "project_assets" in table_names
    assert "project_import_jobs" in table_names
    assert "compile_job_assets" in table_names
    import_job_columns = {
        column["name"]: column
        for column in inspector.get_columns("project_import_jobs")
    }
    assert import_job_columns["heartbeat_at"]["nullable"] is True
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

    active_indexes = {
        index["name"]: index for index in inspector.get_indexes("project_import_jobs")
    }
    assert active_indexes["uq_project_import_jobs_active_project"]["unique"] is True
    assert "status" in str(
        active_indexes["uq_project_import_jobs_active_project"].get(
            "dialect_options", {}
        )
    )


def test_3mf_import_migration_downgrades_and_reupgrades_cleanly(
    postgres_url: str, monkeypatch
):
    server_dir = Path(__file__).parents[1]
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    config = Config(str(server_dir / "alembic.ini"))
    config.set_main_option("script_location", str(server_dir / "migrations"))
    config.set_main_option("sqlalchemy.url", postgres_url)

    engine = create_engine(postgres_url, pool_pre_ping=True)
    upgrade(config, "head")

    user_id = uuid4()
    tenant_id = uuid4()
    project_id = uuid4()
    asset_id = uuid4()
    compile_job_id = uuid4()
    compile_asset_id = uuid4()
    created_at = datetime(2026, 8, 11, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_users (id, keycloak_subject, created_at, last_seen_at) "
                "VALUES (:id, :subject, :created_at, :created_at)"
            ),
            {
                "id": user_id,
                "subject": "immutable-migration-user",
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, created_at) VALUES (:id, :name, :created_at)"
            ),
            {"id": tenant_id, "name": "Immutable tenant", "created_at": created_at},
        )
        connection.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, created_by, created_at, updated_at) "
                "VALUES (:id, :tenant_id, :name, :user_id, :created_at, :created_at)"
            ),
            {
                "id": project_id,
                "tenant_id": tenant_id,
                "name": "immutable-project",
                "user_id": user_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO project_assets "
                "(id, tenant_id, project_id, logical_name, display_name, kind, media_type, content, byte_size, sha256, revision, created_at) "
                "VALUES (:id, :tenant_id, :project_id, 'source.3mf', 'source.3mf', 'source_3mf', "
                "'application/octet-stream', :content, 3, :sha256, 1, :created_at)"
            ),
            {
                "id": asset_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "content": b"3mf",
                "sha256": "0" * 64,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO compile_jobs (id, tenant_id, project_id, requested_by, status, export_format, retryable, attempt_count, created_at) "
                "VALUES (:id, :tenant_id, :project_id, :user_id, 'queued', 'glb', false, 0, :created_at)"
            ),
            {
                "id": compile_job_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "user_id": user_id,
                "created_at": created_at,
            },
        )
        connection.execute(
            text(
                "INSERT INTO compile_job_assets "
                "(id, compile_job_id, tenant_id, project_id, project_asset_id, logical_filename, sha256, byte_size, object_bucket, object_key, created_at) "
                "VALUES (:id, :job_id, :tenant_id, :project_id, :asset_id, 'source.3mf', :sha256, 3, 'assets', 'sha256/source', :created_at)"
            ),
            {
                "id": compile_asset_id,
                "job_id": compile_job_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "asset_id": asset_id,
                "sha256": "0" * 64,
                "created_at": created_at,
            },
        )

    with pytest.raises(DBAPIError, match="project_assets rows are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE project_assets SET content = :content WHERE id = :id"),
                {"content": b"changed", "id": asset_id},
            )
    with pytest.raises(DBAPIError, match="compile_job_assets rows are immutable"):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE compile_job_assets SET object_key = 'changed' WHERE id = :id"
                ),
                {"id": compile_asset_id},
            )

    downgrade(config, "0010_llm_edit_progress")

    inspector = inspect(engine)
    assert "project_assets" not in inspector.get_table_names()
    assert "project_import_jobs" not in inspector.get_table_names()
    assert "compile_job_assets" not in inspector.get_table_names()
    with engine.connect() as connection:
        immutable_functions = connection.scalar(
            text(
                "SELECT count(*) FROM pg_proc WHERE proname IN "
                "('tertius_reject_project_asset_update', 'tertius_reject_compile_job_asset_update')"
            )
        )
    assert immutable_functions == 0

    upgrade(config, "head")
    inspector = inspect(engine)
    assert {
        "project_assets",
        "project_import_jobs",
        "compile_job_assets",
    } <= set(inspector.get_table_names())
    engine.dispose()
    get_settings.cache_clear()


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
            text("SELECT progress_payload FROM llm_edit_jobs WHERE id = :job_id"),
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
