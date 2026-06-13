from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from core.config import get_settings
from core.db import Base
from core import models


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
