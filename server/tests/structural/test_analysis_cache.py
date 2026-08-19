from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.auth_types import AuthContext
from core.models import StructuralAnalysisResult
from core.structural import analysis_cache
from core.structural.analysis_cache import analysis_cache_identity
from core.structural.cantilever_fixture import cantilever_snapshot
from workflows.structural import structural_server


@pytest.fixture
def cache_db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    StructuralAnalysisResult.__table__.create(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def set_engine_version(monkeypatch, tmp_path, version: str) -> None:
    source_commit = tmp_path / ".source-commit"
    source_commit.write_text(version, encoding="utf-8")
    monkeypatch.setattr(analysis_cache, "_SOURCE_COMMIT_PATH", source_commit)


def test_cache_identity_tracks_every_structural_input(monkeypatch, tmp_path):
    set_engine_version(monkeypatch, tmp_path, "engine-a")
    base = {
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "design_digest": "d" * 64,
        "configuration_digest": "c" * 64,
        "site_definition": {"bearing": 0, "wind": {"region": "A1"}},
        "combination_id": None,
    }
    baseline = analysis_cache_identity(**base)

    variants = [
        {**base, "design_digest": "e" * 64},
        {**base, "configuration_digest": "f" * 64},
        {**base, "site_definition": {"bearing": 90, "wind": {"region": "A1"}}},
        {**base, "combination_id": "SLS-G"},
    ]
    assert all(
        analysis_cache_identity(**variant).key_digest != baseline.key_digest
        for variant in variants
    )

    set_engine_version(monkeypatch, tmp_path, "engine-b")
    assert analysis_cache_identity(**base).key_digest != baseline.key_digest


def test_structural_solver_result_is_persisted_and_reused(
    monkeypatch,
    tmp_path,
    cache_db,
):
    set_engine_version(monkeypatch, tmp_path, "cache-test-engine")
    tenant_id = uuid4()
    user_id = uuid4()
    project = SimpleNamespace(id=uuid4())
    context = AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        keycloak_subject="cache-test",
        email="test@example.com",
    )
    capture = SimpleNamespace(
        design_hash="d" * 64,
        analysis_configuration_digest="c" * 64,
    )
    expected = cantilever_snapshot()
    solve_calls = 0

    def solve_once(_capture, *, combination_id=None):
        nonlocal solve_calls
        solve_calls += 1
        return expected

    monkeypatch.setattr(structural_server, "solve_project_structural", solve_once)

    first, first_cache = structural_server._solve_cached_structural_analysis(
        db=cache_db,
        ctx=context,
        project=project,
        capture=capture,
        site_definition={"schema_version": "1.0", "bearing": 0},
        combination_id=None,
    )
    second, second_cache = structural_server._solve_cached_structural_analysis(
        db=cache_db,
        ctx=context,
        project=project,
        capture=capture,
        site_definition={"schema_version": "1.0", "bearing": 0},
        combination_id=None,
    )

    assert first == expected
    assert second == expected
    assert first_cache.status == "calculated"
    assert second_cache.status == "hit"
    assert first_cache.key_digest == second_cache.key_digest
    assert solve_calls == 1
    assert cache_db.scalar(select(func.count(StructuralAnalysisResult.id))) == 1


def test_changed_site_and_combination_create_new_results(
    monkeypatch,
    tmp_path,
    cache_db,
):
    set_engine_version(monkeypatch, tmp_path, "cache-test-engine")
    tenant_id = uuid4()
    user_id = uuid4()
    project = SimpleNamespace(id=uuid4())
    context = AuthContext(
        user_id=user_id,
        tenant_id=tenant_id,
        keycloak_subject="cache-invalidation-test",
        email="test@example.com",
    )
    capture = SimpleNamespace(
        design_hash="d" * 64,
        analysis_configuration_digest="c" * 64,
    )
    expected = cantilever_snapshot()
    solve_calls = 0

    def solve(_capture, *, combination_id=None):
        nonlocal solve_calls
        solve_calls += 1
        return expected

    monkeypatch.setattr(structural_server, "solve_project_structural", solve)

    for site, combination_id in (
        ({"bearing": 0}, None),
        ({"bearing": 90}, None),
        ({"bearing": 90}, "SLS-G"),
    ):
        _, cache = structural_server._solve_cached_structural_analysis(
            db=cache_db,
            ctx=context,
            project=project,
            capture=capture,
            site_definition=site,
            combination_id=combination_id,
        )
        assert cache.status == "calculated"

    assert solve_calls == 3
    assert cache_db.scalar(select(func.count(StructuralAnalysisResult.id))) == 3
