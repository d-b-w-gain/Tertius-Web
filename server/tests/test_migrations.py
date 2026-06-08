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
