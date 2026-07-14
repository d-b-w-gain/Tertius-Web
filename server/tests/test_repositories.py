import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from core.compile_messages import CompileCommand
from core.models import (
    AppUser,
    CompileJobFile,
    CompileJob,
    Project,
    ProjectFile,
    SourceSnapshot,
    SourceSnapshotFile,
    Tenant,
    TenantMembership,
    UserWorkspaceState,
    now_utc,
)
from core.repositories import (
    CompileRepository,
    FileVersionConflictError,
    LlmEditRepository,
    ProjectRepository,
    require_valid_project_name,
    require_valid_python_filename,
)


def seed_two_tenants(db: Session):
    user_a = AppUser(keycloak_subject="a", email="a@example.com")
    user_b = AppUser(keycloak_subject="b", email="b@example.com")
    tenant_a = Tenant(name="Tenant A")
    tenant_b = Tenant(name="Tenant B")
    db.add_all([user_a, user_b, tenant_a, tenant_b])
    db.flush()

    db.add_all(
        [
            TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"),
            TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"),
        ]
    )
    project_a = Project(tenant_id=tenant_a.id, name="same_name", created_by=user_a.id)
    project_b = Project(tenant_id=tenant_b.id, name="same_name", created_by=user_b.id)
    db.add_all([project_a, project_b])
    db.flush()

    db.add_all(
        [
            ProjectFile(tenant_id=tenant_a.id, project_id=project_a.id, filename="design.py", content="a = 1"),
            ProjectFile(tenant_id=tenant_a.id, project_id=project_a.id, filename="helper.py", content="helper = 1"),
            ProjectFile(tenant_id=tenant_b.id, project_id=project_b.id, filename="design.py", content="b = 2"),
        ]
    )
    db.commit()
    return {
        "tenant_a": tenant_a.id,
        "tenant_b": tenant_b.id,
        "user_a": user_a.id,
        "user_b": user_b.id,
        "project_a": project_a.id,
        "project_b": project_b.id,
    }


def test_project_repository_only_reads_current_tenant(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = ProjectRepository(db_session, seeded["tenant_a"])
    repo_b = ProjectRepository(db_session, seeded["tenant_b"])

    assert repo_a.list_projects() == ["same_name"]
    assert repo_b.list_projects() == ["same_name"]
    assert repo_a.get_code("same_name", "design.py") == "a = 1"
    assert repo_b.get_code("same_name", "design.py") == "b = 2"
    assert repo_a.list_files("same_name") == ["design.py", "helper.py"]
    assert repo_b.files_for_runtime("same_name") == {"design.py": "b = 2"}


def test_project_repository_create_project_stays_tenant_scoped(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = ProjectRepository(db_session, seeded["tenant_a"])
    repo_b = ProjectRepository(db_session, seeded["tenant_b"])

    project = repo_a.create_project("new_project", seeded["user_a"], "from build123d import *")

    assert project.tenant_id == seeded["tenant_a"]
    assert repo_a.list_projects() == ["new_project", "same_name"]
    assert repo_b.list_projects() == ["same_name"]
    assert repo_a.get_code("new_project", "design.py") == "from build123d import *"
    assert repo_b.get_project("new_project") is None


def test_project_repository_set_active_project_updates_workspace_state_and_active_file(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    project = repo.create_project("active_project", seeded["user_a"], "from build123d import *")
    project_file = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.project_id == project.id,
            ProjectFile.filename == "design.py",
        )
    )

    assert repo.set_active_project(seeded["user_a"], project.id) is True

    state = db_session.scalar(
        select(UserWorkspaceState).where(
            UserWorkspaceState.user_id == seeded["user_a"],
            UserWorkspaceState.tenant_id == seeded["tenant_a"],
        )
    )
    assert state.active_project_id == project.id
    assert state.active_file_id == project_file.id


def test_project_repository_set_active_project_rejects_cross_tenant_project(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.set_active_project(seeded["user_a"], seeded["project_b"]) is False
    assert db_session.scalar(select(UserWorkspaceState)) is None


def test_project_repository_save_code_updates_project_and_snapshots_all_files(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    project = db_session.get(Project, seeded["project_a"])
    project.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    assert repo.save_code("same_name", "helper.py", "helper = 42", seeded["user_a"], "Update helper") is True

    db_session.refresh(project)
    snapshot = db_session.scalar(select(SourceSnapshot).where(SourceSnapshot.project_id == project.id))
    snapshot_files = db_session.scalars(
        select(SourceSnapshotFile).where(SourceSnapshotFile.snapshot_id == snapshot.id).order_by(SourceSnapshotFile.filename)
    ).all()

    assert project.updated_at > datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert repo.get_code("same_name", "helper.py") == "helper = 42"
    assert snapshot.tenant_id == seeded["tenant_a"]
    assert snapshot.created_by == seeded["user_a"]
    assert snapshot.message == "Update helper"
    assert [(row.filename, row.content) for row in snapshot_files] == [
        ("design.py", "a = 1"),
        ("helper.py", "helper = 42"),
    ]


def test_project_repository_save_code_returns_false_for_missing_project(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.save_code("missing", "design.py", "x = 1", seeded["user_a"], "No-op") is False
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0


def test_project_repository_delete_file_rejects_design_py_and_invalid_names(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert repo.delete_file("same_name", "helper.py") is True
    assert repo.get_code("same_name", "helper.py") is None
    assert repo.delete_file("same_name", "missing.py") is False

    with pytest.raises(ValueError):
        repo.delete_file("same_name", "design.py")

    with pytest.raises(ValueError):
        repo.delete_file("same_name", "../helper.py")


def test_project_repository_rejects_invalid_filename_and_project_name(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    assert require_valid_python_filename("design.py") == "design.py"
    assert require_valid_project_name("valid-project_1") == "valid-project_1"

    with pytest.raises(ValueError):
        repo.get_code("same_name", "../design.py")

    with pytest.raises(ValueError):
        repo.get_code("same_name", "notes.txt")

    with pytest.raises(ValueError):
        repo.get_project("../same_name")


def test_project_file_cannot_cross_tenant_project_boundary(db_session):
    seeded = seed_two_tenants(db_session)
    db_session.add(
        ProjectFile(
            tenant_id=seeded["tenant_a"],
            project_id=seeded["project_b"],
            filename="illegal.py",
            content="x = 1",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_compile_repository_gets_job_and_artifact_by_scope(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    artifact = repo.record_artifact(seeded_tenant.project_id, job.id, "glb", b"model")
    db_session.commit()

    fetched_job = repo.get_job(seeded_tenant.project_id, job.id)
    assert fetched_job is not None
    assert fetched_job.id == job.id
    fetched_artifact = repo.artifact_for_job(job.id)
    assert fetched_artifact is not None
    assert fetched_artifact.id == artifact.id


def test_compile_repository_persists_originating_llm_edit_job_id(db_session, seeded_tenant):
    llm_repo = LlmEditRepository(db_session, seeded_tenant.tenant_id)
    llm_job = llm_repo.start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        {"prompt": "Make a bracket", "files": []},
        status="succeeded",
    )
    compile_repo = CompileRepository(db_session, seeded_tenant.tenant_id)

    compile_job = compile_repo.start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        "glb",
        status="queued",
        originating_llm_edit_job_id=llm_job.id,
    )
    manual_job = compile_repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl", status="queued")
    db_session.commit()

    assert db_session.get(CompileJob, compile_job.id).originating_llm_edit_job_id == llm_job.id
    assert db_session.get(CompileJob, manual_job.id).originating_llm_edit_job_id is None


def test_compile_repository_returns_none_for_wrong_project(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl", status="queued")
    db_session.commit()

    assert repo.get_job(uuid4(), job.id) is None


def test_compile_repository_validates_command_identity(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl", status="queued")
    db_session.commit()

    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="stl",
        created_at=job.created_at,
    )
    mismatched = command.model_copy(update={"export_format": "glb"})

    matched_job = repo.get_job_for_command(command)
    assert matched_job is not None
    assert matched_job.id == job.id
    assert repo.get_job_for_command(mismatched) is None


def test_llm_edit_repository_lists_jobs_for_project_ordered_and_tenant_scoped(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = LlmEditRepository(db_session, seeded["tenant_a"])
    repo_b = LlmEditRepository(db_session, seeded["tenant_b"])
    first = repo_a.start_job(seeded["project_a"], seeded["user_a"], {"prompt": "first", "files": []})
    second = repo_a.start_job(seeded["project_a"], seeded["user_a"], {"prompt": "second", "files": []})
    other_tenant = repo_b.start_job(seeded["project_b"], seeded["user_b"], {"prompt": "other", "files": []})
    first.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    second.created_at = datetime(2024, 1, 2, tzinfo=timezone.utc)
    other_tenant.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db_session.commit()

    jobs = repo_a.list_jobs_for_project(seeded["project_a"])

    assert [job.id for job in jobs] == [first.id, second.id]
    assert other_tenant.id not in [job.id for job in jobs]
    assert repo_a.list_jobs_for_project(seeded["project_a"], limit=1)[0].id == first.id


def test_llm_edit_repository_lists_bounded_terminal_jobs_oldest_first(db_session):
    seeded = seed_two_tenants(db_session)
    repo_a = LlmEditRepository(db_session, seeded["tenant_a"])
    repo_b = LlmEditRepository(db_session, seeded["tenant_b"])
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = []
    for index, status in enumerate(["succeeded", "failed", "running", "succeeded"]):
        job = repo_a.start_job(
            seeded["project_a"],
            seeded["user_a"],
            {"prompt": f"request-{index}", "files": []},
            status=status,
        )
        job.created_at = base + timedelta(minutes=index)
        if status in {"succeeded", "failed"}:
            expected.append(job.id)
    repo_b.start_job(
        seeded["project_b"],
        seeded["user_b"],
        {"prompt": "other tenant", "files": []},
        status="succeeded",
    )
    db_session.flush()

    jobs = repo_a.list_recent_terminal_jobs(seeded["project_a"], limit=200)
    assert [job.id for job in jobs] == expected
    assert repo_a.list_recent_terminal_jobs(seeded["project_a"], limit=1)[0].id == expected[-1]


def test_llm_edit_repository_gets_compile_job_for_llm_edit(db_session, seeded_tenant):
    llm_repo = LlmEditRepository(db_session, seeded_tenant.tenant_id)
    compile_repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    llm_job = llm_repo.start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        {"prompt": "Generate a fixture", "files": []},
        status="succeeded",
    )
    linked_compile = compile_repo.start_job(
        seeded_tenant.project_id,
        seeded_tenant.user_id,
        "glb",
        status="succeeded",
        originating_llm_edit_job_id=llm_job.id,
    )
    compile_repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl", status="queued")
    db_session.commit()

    fetched = llm_repo.get_compile_job_for_llm_edit(seeded_tenant.project_id, llm_job.id)

    assert fetched is not None
    assert fetched.id == linked_compile.id
    assert llm_repo.get_compile_job_for_llm_edit(seeded_tenant.project_id, uuid4()) is None


def test_compile_repository_persists_structured_failure(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "stl", status="queued")

    repo.finish_job(
        job,
        "failed",
        error="boom",
        error_code="sandbox_error",
        user_message="Compile failed. Fix the model source and try again.",
        retryable=True,
    )
    db_session.commit()

    persisted = db_session.get(CompileJob, job.id)
    assert persisted.error_code == "sandbox_error"
    assert persisted.user_message == "Compile failed. Fix the model source and try again."
    assert persisted.retryable is True


def test_compile_repository_claims_queued_job_once(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()

    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )

    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    assert claimed is not None
    first_token = claimed.claim_token
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at is not None
    db_session.commit()

    duplicate = repo.claim_job_for_command(command, lease_seconds=660)
    assert duplicate is None

    persisted = db_session.get(CompileJob, job.id)
    assert persisted.claim_token == first_token
    assert persisted.attempt_count == 1


def test_compile_repository_reclaims_expired_running_job(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    first = repo.claim_job_for_command(command, lease_seconds=1)
    assert first is not None
    first.lease_expires_at = now_utc() - timedelta(seconds=1)
    first_token = first.claim_token
    db_session.commit()

    second = repo.claim_job_for_command(command, lease_seconds=660)
    assert second is not None
    assert second.claim_token != first_token
    assert second.attempt_count == 2


def test_compile_repository_finishes_only_current_claim(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")
    db_session.commit()
    command = CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format="glb",
        created_at=job.created_at,
    )
    claimed = repo.claim_job_for_command(command, lease_seconds=660)
    assert claimed is not None
    stale_token = uuid4()
    db_session.commit()

    assert repo.finish_job_if_claim_current(job.id, stale_token, "failed", error_code="stale_claim") is None
    persisted = db_session.get(CompileJob, job.id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.claim_token == claimed.claim_token

    finished = repo.finish_job_if_claim_current(job.id, claimed.claim_token, "succeeded")
    assert finished is not None
    assert finished.status == "succeeded"
    assert finished.lease_expires_at is None


def test_compile_repository_snapshots_job_files(db_session, seeded_tenant):
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    project_repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
    project_repo.save_code("default_purlin", "design.py", "shape = 'snapshot'\n", seeded_tenant.user_id, "snapshot")
    job = repo.start_job(seeded_tenant.project_id, seeded_tenant.user_id, "glb", status="queued")

    files = project_repo.files_for_runtime("default_purlin")
    assert files is not None
    repo.snapshot_job_files(job, files)
    project_repo.save_code("default_purlin", "design.py", "shape = 'later'\n", seeded_tenant.user_id, "later")
    db_session.commit()

    snapshot = repo.files_for_job(job.id)
    snapshot_rows = db_session.scalars(select(CompileJobFile).where(CompileJobFile.compile_job_id == job.id)).all()
    assert snapshot["design.py"] == "shape = 'snapshot'\n"
    assert snapshot_rows


def test_project_repository_lists_file_metadata_with_design_first(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])

    metadata = repo.list_file_metadata("same_name")

    assert [row["filename"] for row in metadata] == ["design.py", "helper.py"]
    assert all(row["id"] for row in metadata)
    assert all(row["updated_at"] for row in metadata)


def test_project_repository_batch_file_updates_create_one_snapshot(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    file_rows = db_session.scalars(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.filename.in_(["design.py", "helper.py"]),
        )
    ).all()
    files = {row.id: row for row in file_rows}

    snapshot, changed = repo.stage_file_updates(
        "same_name",
        {
            next(row.id for row in files.values() if row.filename == "design.py"): "design = 2",
            next(row.id for row in files.values() if row.filename == "helper.py"): "helper = 2",
        },
        seeded["user_a"],
        "LLM edit: update two files",
    )
    db_session.commit()

    assert snapshot.message == "LLM edit: update two files"
    assert len(changed) == 2
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    assert repo.get_code("same_name", "design.py") == "design = 2"
    assert repo.get_code("same_name", "helper.py") == "helper = 2"


def test_project_repository_stage_file_updates_rejects_stale_versions_without_snapshot(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    file_row = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.filename == "design.py",
        )
    )
    stale_version = file_row.updated_at
    file_row.content = "design = user_change"
    file_row.updated_at = stale_version + timedelta(seconds=1)
    db_session.commit()

    with pytest.raises(FileVersionConflictError):
        repo.stage_file_updates(
            "same_name",
            {file_row.id: "design = ai_change"},
            seeded["user_a"],
            "LLM edit: update design",
            expected_updated_at={file_row.id: stale_version},
        )

    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0
    assert repo.get_code("same_name", "design.py") == "design = user_change"


def _other_session_factory(postgres_url: str):
    engine = create_engine(postgres_url, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_stage_file_updates_detects_concurrent_save_from_another_session(
    postgres_url, db_session, seeded_tenant
):
    """A concurrent save committed by a separate session between the endpoint's
    pre-check and stage_file_updates must be detected at persist time and
    rejected without creating a snapshot. The FOR UPDATE re-read inside
    stage_file_updates sees the committed change even though the caller still
    holds the stale version pointer.
    """
    repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    stale_version = design.updated_at

    # Simulate a concurrent user save in a separate session that commits
    # after the AI edit's pre-check but before stage_file_updates persists.
    other_engine, OtherSession = _other_session_factory(postgres_url)
    try:
        with OtherSession() as other_session:
            other_file = other_session.get(ProjectFile, design_id)
            other_file.content = "design = user_change"
            other_file.updated_at = stale_version + timedelta(seconds=1)
            other_session.commit()
    finally:
        other_engine.dispose()

    # The AI edit still holds the stale version pointer and tries to persist.
    with pytest.raises(FileVersionConflictError):
        repo.stage_file_updates(
            "default_purlin",
            {design_id: "design = ai_change"},
            seeded_tenant.user_id,
            "LLM edit: update design",
            expected_updated_at={design_id: stale_version},
        )

    db_session.rollback()
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0
    db_session.expire_all()
    assert db_session.get(ProjectFile, design_id).content == "design = user_change"


def test_stage_file_updates_holds_for_update_lock_against_concurrent_save(
    postgres_url, db_session, seeded_tenant
):
    """The FOR UPDATE guard in stage_file_updates holds row locks for the
    whole persist, so a concurrent save in another session cannot commit
    between the version re-check and the content mutation. This proves the
    final write is guarded: a late concurrent save is blocked until the AI
    edit commits, and can never be silently overwritten mid-persist.
    """
    repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
    design = db_session.scalar(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded_tenant.tenant_id,
            ProjectFile.filename == "design.py",
        )
    )
    design_id = design.id
    current_version = design.updated_at
    db_session.expire_all()

    other_engine, OtherSession = _other_session_factory(postgres_url)

    lock_acquired = threading.Event()
    save_started = threading.Event()
    save_completed = threading.Event()
    save_result: dict[str, object] = {}

    def concurrent_save():
        if not lock_acquired.wait(timeout=10):
            save_result["error"] = TimeoutError("AI edit never acquired the lock")
            save_completed.set()
            return
        try:
            with OtherSession() as other_session:
                other_file = other_session.get(ProjectFile, design_id)
                other_file.content = "design = user_save"
                other_file.updated_at = current_version + timedelta(seconds=2)
                save_started.set()  # about to flush/commit (UPDATE will block)
                other_session.commit()
            save_completed.set()
        except Exception as exc:  # pragma: no cover - surfaced via assertions
            save_result["error"] = exc
            save_completed.set()

    original_files_by_ids = repo.files_by_ids

    def hooked_files_by_ids(project_name, file_ids, for_update=False):
        rows = original_files_by_ids(project_name, file_ids, for_update=for_update)
        if for_update and file_ids:
            lock_acquired.set()
            save_started.wait(timeout=10)
            # The concurrent save has reached its blocked UPDATE. It must not
            # be able to commit while the AI edit holds the FOR UPDATE lock.
            assert not save_completed.wait(timeout=1.0), (
                "concurrent save committed while AI edit held the FOR UPDATE lock"
            )
        return rows

    repo.files_by_ids = hooked_files_by_ids  # type: ignore[method-assign]

    thread = threading.Thread(target=concurrent_save, daemon=True)
    thread.start()
    try:
        snapshot, changed = repo.stage_file_updates(
            "default_purlin",
            {design_id: "design = ai_edit"},
            seeded_tenant.user_id,
            "LLM edit: guard test",
            expected_updated_at={design_id: current_version},
        )
        db_session.commit()
    finally:
        thread.join(timeout=15)
        repo.files_by_ids = original_files_by_ids  # type: ignore[method-assign]
        other_engine.dispose()

    assert snapshot is not None
    assert len(changed) == 1
    # The concurrent save was blocked for the duration of the AI persist and
    # only completed after the AI edit committed and released the row locks.
    assert save_completed.is_set(), save_result.get("error")
    assert "error" not in save_result, save_result.get("error")
    # One snapshot from the AI edit; the direct ORM commit in the concurrent
    # save does not create a snapshot.
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 1
    # The user save committed last, so it is the final writer. The guarantee
    # proven above is that the AI edit was never overwritten *during* its
    # persist window.
    assert db_session.get(ProjectFile, design_id).content == "design = user_save"


def test_project_repository_stage_file_updates_truncates_long_snapshot_message(db_session):
    seeded = seed_two_tenants(db_session)
    repo = ProjectRepository(db_session, seeded["tenant_a"])
    file_rows = db_session.scalars(
        select(ProjectFile).where(
            ProjectFile.tenant_id == seeded["tenant_a"],
            ProjectFile.filename == "design.py",
        )
    ).all()
    long_message = "LLM edit: " + ("x" * 12000)

    snapshot, _ = repo.stage_file_updates(
        "same_name",
        {file_rows[0].id: "design = 3"},
        seeded["user_a"],
        long_message,
    )
    db_session.commit()

    assert len(snapshot.message) <= 500
    assert snapshot.message.startswith("LLM edit:")
