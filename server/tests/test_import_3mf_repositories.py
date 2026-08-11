from __future__ import annotations

import hashlib
import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.models import (
    AppUser,
    Project,
    ProjectAsset,
    ProjectFile,
    ProjectImportJob,
    SourceSnapshot,
    SourceSnapshotFile,
    Tenant,
    TenantMembership,
)
from core.project_assets import (
    BREP_MEDIA_TYPE,
    IMPORT_3MF_CONVERSION_VERSION,
    MANIFEST_MEDIA_TYPE,
    THREE_MF_MEDIA_TYPE,
    generated_3mf_design_source,
)
from core.repositories import (
    ActiveProjectImportError,
    AssetIntegrityError,
    CompileRepository,
    ImportNotRetryableError,
    ProjectAssetRepository,
    ProjectImportRepository,
    ProjectNameConflictError,
    ProjectRepository,
    StaleImportExecutionError,
)


def _manifest_bytes(source: bytes, brep: bytes) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "conversion_version": IMPORT_3MF_CONVERSION_VERSION,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "brep_sha256": hashlib.sha256(brep).hexdigest(),
            "brep_byte_size": len(brep),
            "source_unit": "MM",
            "scale_to_mm": 1.0,
            "object_count": 1,
            "total_vertices": 8,
            "total_triangles": 12,
            "warnings": [],
            "parts": [
                {
                    "index": 0,
                    "name": "part_001",
                    "source_name": "Part",
                    "shape_type": "solid",
                    "boolean_capable": True,
                    "is_valid": True,
                    "vertex_count": 8,
                    "triangle_count": 12,
                    "bounds_mm": {
                        "min": [0.0, 0.0, 0.0],
                        "max": [1.0, 1.0, 1.0],
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def _create_import(repo, seeded, *, name="imported", source=b"3mf-source"):
    return repo.create_import(
        project_name=name,
        requested_by=seeded.user_id,
        display_name="falcon9.3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=source,
    )


def test_project_asset_repository_computes_immutable_content_metadata(db_session, seeded_tenant):
    repo = ProjectAssetRepository(db_session, seeded_tenant.tenant_id)
    content = b"immutable 3mf bytes"

    asset = repo.create(
        project_id=seeded_tenant.project_id,
        logical_name="source.3mf",
        display_name="model.3mf",
        kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=content,
        revision=1,
    )

    assert asset.byte_size == len(content)
    assert asset.sha256 == hashlib.sha256(content).hexdigest()
    assert repo.get_content(asset.id) == content
    assert repo.get_content(asset.id, project_id=uuid4()) is None

    asset.content = b"mutated"
    asset.byte_size = 7
    asset.sha256 = hashlib.sha256(asset.content).hexdigest()
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()


def test_project_asset_metadata_queries_do_not_load_binary_content(db_session, seeded_tenant):
    repo = ProjectAssetRepository(db_session, seeded_tenant.tenant_id)
    asset = repo.create(
        project_id=seeded_tenant.project_id,
        logical_name="source.3mf",
        display_name="model.3mf",
        kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"large binary placeholder",
        revision=1,
    )
    db_session.expire_all()

    metadata = repo.get_metadata(asset.id)
    listed = repo.list_metadata(seeded_tenant.project_id)

    assert metadata is not None and metadata.id == asset.id
    assert listed == [metadata]
    loaded = db_session.get(ProjectAsset, asset.id)
    assert "content" in inspect(loaded).unloaded


def test_project_asset_repository_is_tenant_scoped(db_session, seeded_tenant):
    asset = ProjectAssetRepository(db_session, seeded_tenant.tenant_id).create(
        project_id=seeded_tenant.project_id,
        logical_name="source.3mf",
        display_name="model.3mf",
        kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"3mf",
        revision=1,
    )

    other_repo = ProjectAssetRepository(db_session, uuid4())
    assert other_repo.get_metadata(asset.id) is None
    assert other_repo.get_content(asset.id) is None


def test_create_import_stages_empty_project_source_asset_and_job_atomically(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)

    project, source, job = _create_import(repo, seeded_tenant)

    assert project.name == "imported"
    assert source.project_id == project.id
    assert source.kind == "source_3mf"
    assert job.source_asset_id == source.id
    assert job.status == "queued"
    assert db_session.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id)).all() == []

    db_session.rollback()
    assert db_session.scalar(select(Project).where(Project.name == "imported")) is None


def test_create_import_collision_leaves_no_partial_asset_or_job(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)

    with pytest.raises(ProjectNameConflictError):
        _create_import(repo, seeded_tenant, name="default_purlin")

    assert db_session.scalars(select(ProjectAsset)).all() == []
    assert db_session.scalars(select(ProjectImportJob)).all() == []


def test_create_import_rejects_user_from_another_tenant_without_partial_state(db_session, seeded_tenant):
    other_user = AppUser(keycloak_subject="other-tenant-user")
    other_tenant = Tenant(name="Other tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    db_session.flush()

    with pytest.raises(ValueError, match="tenant member"):
        ProjectImportRepository(db_session, seeded_tenant.tenant_id).create_import(
            project_name="cross_tenant_import",
            requested_by=other_user.id,
            display_name="model.3mf",
            media_type=THREE_MF_MEDIA_TYPE,
            content=b"3mf",
        )

    assert db_session.scalar(select(Project).where(Project.name == "cross_tenant_import")) is None
    assert db_session.scalars(select(ProjectAsset)).all() == []
    assert db_session.scalars(select(ProjectImportJob)).all() == []


def test_import_repository_allows_only_one_active_job(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _, source, job = _create_import(repo, seeded_tenant)

    with pytest.raises(ActiveProjectImportError):
        repo.create_queued(job.project_id, seeded_tenant.user_id, source.id)

    assert db_session.scalars(select(ProjectImportJob).where(ProjectImportJob.project_id == job.project_id)).all() == [job]


def test_retry_reuses_job_with_new_execution_and_clears_terminal_state(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _, _, job = _create_import(repo, seeded_tenant)
    old_execution = job.execution_id
    repo.mark_running(job.id, old_execution)
    repo.mark_progress(job.id, old_execution, {"stage": "convert", "percent": 50})
    repo.mark_failed(
        job.id,
        old_execution,
        error="worker stopped",
        error_code="worker_lost",
        user_message="Try again.",
        retryable=True,
    )

    retried = repo.retry(job.id)

    assert retried.id == job.id
    assert retried.attempt == 2
    assert retried.execution_id != old_execution
    assert retried.status == "queued"
    assert retried.error is None
    assert retried.error_code is None
    assert retried.user_message is None
    assert retried.retryable is False
    assert retried.progress_payload == {}
    assert retried.brep_asset_id is None
    assert retried.manifest_asset_id is None
    assert retried.started_at is None
    assert retried.finished_at is None


def test_retry_rejects_nonfailed_or_nonretryable_jobs(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _, _, job = _create_import(repo, seeded_tenant)

    with pytest.raises(ImportNotRetryableError):
        repo.retry(job.id)

    repo.mark_failed(
        job.id,
        job.execution_id,
        error="bad input",
        error_code="invalid_3mf",
        user_message="Invalid 3MF.",
        retryable=False,
    )
    with pytest.raises(ImportNotRetryableError):
        repo.retry(job.id)


def test_stale_execution_cannot_update_or_complete_retried_job(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    _, source, job = _create_import(repo, seeded_tenant)
    stale_execution = job.execution_id
    repo.mark_failed(
        job.id,
        stale_execution,
        error="lost",
        error_code="worker_lost",
        user_message="Try again.",
        retryable=True,
    )
    repo.retry(job.id)

    with pytest.raises(StaleImportExecutionError):
        repo.mark_progress(job.id, stale_execution, {"stage": "stale"})
    with pytest.raises(StaleImportExecutionError):
        repo.apply_success(
            job_id=job.id,
            execution_id=stale_execution,
            source_sha256=source.sha256,
            brep_content=b"brep",
            manifest_content=_manifest_bytes(b"3mf-source", b"brep"),
            user_id=seeded_tenant.user_id,
        )


def test_apply_success_is_atomic_and_persists_exact_generated_source_pair(db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    source_bytes = b"3mf-source"
    brep_bytes = b"OpenCascade BREP"
    project, source, job = _create_import(repo, seeded_tenant, source=source_bytes)
    repo.mark_running(job.id, job.execution_id)

    succeeded = repo.apply_success(
        job_id=job.id,
        execution_id=job.execution_id,
        source_sha256=source.sha256,
        brep_content=brep_bytes,
        manifest_content=_manifest_bytes(source_bytes, brep_bytes),
        user_id=seeded_tenant.user_id,
    )

    assert succeeded.status == "succeeded"
    assert succeeded.brep_asset_id is not None
    assert succeeded.manifest_asset_id is not None
    pair = ProjectAssetRepository(db_session, seeded_tenant.tenant_id).successful_import_pair(project.id)
    assert pair is not None
    brep, manifest = pair
    assert (brep.kind, brep.revision, brep.content) == (
        "derived_brep",
        1,
        brep_bytes,
    )
    assert (manifest.kind, manifest.revision) == ("import_manifest", 1)
    assert ProjectRepository(db_session, seeded_tenant.tenant_id).get_code(project.name, "design.py") == generated_3mf_design_source()

    snapshot = db_session.scalar(select(SourceSnapshot).where(SourceSnapshot.project_id == project.id))
    snapshot_files = db_session.scalars(select(SourceSnapshotFile).where(SourceSnapshotFile.snapshot_id == snapshot.id)).all()
    assert [(row.filename, row.content) for row in snapshot_files] == [("design.py", generated_3mf_design_source())]

    with pytest.raises(AssetIntegrityError):
        repo.apply_success(
            job_id=job.id,
            execution_id=job.execution_id,
            source_sha256="0" * 64,
            brep_content=brep_bytes,
            manifest_content=_manifest_bytes(source_bytes, brep_bytes),
            user_id=seeded_tenant.user_id,
        )


@pytest.mark.parametrize("failure", ["source_digest", "manifest"])
def test_apply_success_failure_leaves_no_partial_derived_state(failure, db_session, seeded_tenant):
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    source_bytes = b"3mf-source"
    brep_bytes = b"brep"
    project, source, job = _create_import(repo, seeded_tenant, source=source_bytes)
    bad_source_digest = "0" * 64 if failure == "source_digest" else source.sha256
    manifest = b"not-json" if failure == "manifest" else _manifest_bytes(source_bytes, brep_bytes)

    with pytest.raises((AssetIntegrityError, ValueError)):
        repo.apply_success(
            job_id=job.id,
            execution_id=job.execution_id,
            source_sha256=bad_source_digest,
            brep_content=brep_bytes,
            manifest_content=manifest,
            user_id=seeded_tenant.user_id,
        )

    assert (
        db_session.scalars(
            select(ProjectAsset).where(
                ProjectAsset.project_id == project.id,
                ProjectAsset.kind != "source_3mf",
            )
        ).all()
        == []
    )
    assert db_session.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id)).all() == []
    assert db_session.scalars(select(SourceSnapshot).where(SourceSnapshot.project_id == project.id)).all() == []
    assert job.status == "queued"


def test_apply_success_rejects_arbitrary_snapshot_user(db_session, seeded_tenant):
    other_user = AppUser(keycloak_subject="wrong-result-user")
    other_tenant = Tenant(name="Wrong result tenant")
    db_session.add_all([other_user, other_tenant])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=other_tenant.id, user_id=other_user.id, role="owner"))
    db_session.flush()
    repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    project, source, job = _create_import(repo, seeded_tenant)
    brep = b"brep"

    with pytest.raises(ValueError, match="requested user"):
        repo.apply_success(
            job_id=job.id,
            execution_id=job.execution_id,
            source_sha256=source.sha256,
            brep_content=brep,
            manifest_content=_manifest_bytes(b"3mf-source", brep),
            user_id=other_user.id,
        )

    assert db_session.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id)).all() == []
    assert db_session.scalars(select(SourceSnapshot).where(SourceSnapshot.project_id == project.id)).all() == []
    assert db_session.scalars(
        select(ProjectAsset).where(ProjectAsset.project_id == project.id, ProjectAsset.kind != "source_3mf")
    ).all() == []


def test_compile_asset_snapshot_is_tenant_scoped_and_immutable(db_session, seeded_tenant):
    import_repo = ProjectImportRepository(db_session, seeded_tenant.tenant_id)
    project, source, import_job = _create_import(import_repo, seeded_tenant)
    brep_content = b"brep-v1"
    import_repo.apply_success(
        job_id=import_job.id,
        execution_id=import_job.execution_id,
        source_sha256=source.sha256,
        brep_content=brep_content,
        manifest_content=_manifest_bytes(b"3mf-source", brep_content),
        user_id=seeded_tenant.user_id,
    )
    brep, manifest = ProjectAssetRepository(db_session, seeded_tenant.tenant_id).successful_import_pair(project.id)
    compile_repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    compile_job = compile_repo.start_job(project.id, seeded_tenant.user_id, "glb")
    compile_repo.snapshot_job_assets(
        compile_job,
        {
            "source.brep": (brep, "TERTIUS_ASSETS", "sha256/brep-v1"),
            "source.manifest.json": (
                manifest,
                "TERTIUS_ASSETS",
                "sha256/manifest-v1",
            ),
        },
    )

    first_snapshot = compile_repo.assets_for_job(compile_job.id)
    source_v2 = ProjectAssetRepository(db_session, seeded_tenant.tenant_id).create(
        project_id=project.id,
        logical_name="source.3mf",
        display_name="second.3mf",
        kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"3mf-source-v2",
        revision=2,
    )
    import_job_v2 = import_repo.create_queued(project.id, seeded_tenant.user_id, source_v2.id)
    brep_v2 = b"brep-v2"
    import_repo.apply_success(
        job_id=import_job_v2.id,
        execution_id=import_job_v2.execution_id,
        source_sha256=source_v2.sha256,
        brep_content=brep_v2,
        manifest_content=_manifest_bytes(b"3mf-source-v2", brep_v2),
        user_id=seeded_tenant.user_id,
    )

    _, manifest_v2 = ProjectAssetRepository(
        db_session, seeded_tenant.tenant_id
    ).successful_import_pair(project.id)
    mixed_job = compile_repo.start_job(
        project.id, seeded_tenant.user_id, "glb"
    )
    with pytest.raises(AssetIntegrityError):
        compile_repo.snapshot_job_assets(
            mixed_job,
            {
                "source.brep": (brep, "TERTIUS_ASSETS", "sha256/brep-v1"),
                "source.manifest.json": (
                    manifest_v2,
                    "TERTIUS_ASSETS",
                    "sha256/manifest-v2",
                ),
            },
        )

    assert compile_repo.assets_for_job(compile_job.id) == first_snapshot
    assert {row.logical_filename for row in first_snapshot} == {
        "source.brep",
        "source.manifest.json",
    }
    assert all(row.tenant_id == seeded_tenant.tenant_id for row in first_snapshot)
    assert CompileRepository(db_session, uuid4()).assets_for_job(compile_job.id) == []

    first_snapshot[0].object_key = "sha256/mutated"
    with pytest.raises(RuntimeError, match="immutable"):
        db_session.flush()


def test_revision_allocation_serializes_concurrent_writers(postgres_url, db_session, seeded_tenant):
    db_session.commit()
    engine = create_engine(postgres_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    barrier = threading.Barrier(2)
    revisions: list[int] = []
    errors: list[Exception] = []

    def create_pair(marker: bytes) -> None:
        try:
            with SessionFactory() as session:
                repo = ProjectAssetRepository(session, seeded_tenant.tenant_id)
                barrier.wait(timeout=10)
                revision = repo.allocate_revision(seeded_tenant.project_id)
                repo.create(
                    project_id=seeded_tenant.project_id,
                    logical_name="source.brep",
                    display_name="source.brep",
                    kind="derived_brep",
                    media_type=BREP_MEDIA_TYPE,
                    content=marker,
                    revision=revision,
                )
                repo.create(
                    project_id=seeded_tenant.project_id,
                    logical_name="source.manifest.json",
                    display_name="source.manifest.json",
                    kind="import_manifest",
                    media_type=MANIFEST_MEDIA_TYPE,
                    content=b"{}" + marker,
                    revision=revision,
                )
                session.commit()
                revisions.append(revision)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=create_pair, args=(marker,), daemon=True) for marker in (b"one", b"two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    engine.dispose()

    assert not errors
    assert not any(thread.is_alive() for thread in threads)
    assert sorted(revisions) == [1, 2]
    db_session.expire_all()
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ProjectAsset)
            .where(
                ProjectAsset.project_id == seeded_tenant.project_id,
                ProjectAsset.kind == "derived_brep",
            )
        )
        == 2
    )


def test_project_asset_cross_project_scope_is_rejected(db_session, seeded_tenant):
    other_project = Project(
        tenant_id=seeded_tenant.tenant_id,
        name="other",
        created_by=seeded_tenant.user_id,
    )
    db_session.add(other_project)
    db_session.flush()
    source = ProjectAssetRepository(db_session, seeded_tenant.tenant_id).create(
        project_id=seeded_tenant.project_id,
        logical_name="source.3mf",
        display_name="source.3mf",
        kind="source_3mf",
        media_type=THREE_MF_MEDIA_TYPE,
        content=b"source",
        revision=1,
    )
    db_session.add(
        ProjectImportJob(
            tenant_id=seeded_tenant.tenant_id,
            project_id=other_project.id,
            requested_by=seeded_tenant.user_id,
            source_asset_id=source.id,
            execution_id=uuid4(),
            status="queued",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
