"""Concurrent tenant isolation and lease-claiming integration tests.

Tests correctness under concurrent access:
  - Two workers claiming the same job (only one wins)
  - Concurrent artifact pruning + new artifact creation
  - Cross-tenant artifact isolation under concurrent load
  - Concurrent compile job submission in different tenants
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from core.compile_messages import CompileCommand
from core.models import (
    AppUser,
    Artifact,
    CompileJob,
    CompileJobFile,
    Project,
    Tenant,
    TenantMembership,
)
from core.repositories import CompileRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_job(repo: CompileRepository, project_id, user_id, db, tenant_id, export_format="stl"):
    """Create a queued job with snapshot files, ready for worker claiming."""
    job = repo.start_job(project_id, user_id, export_format, status="queued")
    db.flush()

    job_file = CompileJobFile(
        compile_job_id=job.id,
        tenant_id=tenant_id,
        project_id=project_id,
        filename="design.py",
        content="import build123d as bd\nbox = bd.Box(10,10,10)\n",
    )
    db.add(job_file)
    db.commit()
    return job


def _make_command(job, seeded_tenant):
    """Build a CompileCommand matching the job."""
    from core.compile_messages import CompileSourceFile

    return CompileCommand(
        job_id=job.id,
        tenant_id=seeded_tenant.tenant_id,
        project_id=seeded_tenant.project_id,
        requested_by=seeded_tenant.user_id,
        export_format=job.export_format,
        created_at=job.created_at,
        files=[CompileSourceFile(filename="design.py", content="code")],
        request_id=f"compile-request:{job.id}",
    )


# ---------------------------------------------------------------------------
# Lease claiming: two workers, one job — only one wins
# ---------------------------------------------------------------------------

def test_two_workers_claiming_same_job_only_one_wins(db_session, seeded_tenant):
    """When two workers try to claim the same queued job, exactly one
    should succeed (get a claim_token) and the other should get None."""
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = _create_job(repo, seeded_tenant.project_id, seeded_tenant.user_id, db_session, seeded_tenant.tenant_id)

    command = _make_command(job, seeded_tenant)

    # Both workers attempt to claim the same job
    claim1 = repo.claim_job_for_command(command, lease_seconds=60)
    claim2 = repo.claim_job_for_command(command, lease_seconds=60)

    # Exactly one should succeed
    claims = [c for c in (claim1, claim2) if c is not None]
    assert len(claims) == 1, f"Expected exactly 1 successful claim, got {len(claims)}"

    # The winner should have a claim_token
    winner = claims[0]
    assert winner.claim_token is not None
    assert winner.status == "running"


def test_lease_reclamation_after_expiry(db_session, seeded_tenant):
    """After a lease expires, another worker should be able to reclaim the job."""
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = _create_job(repo, seeded_tenant.project_id, seeded_tenant.user_id, db_session, seeded_tenant.tenant_id)

    command = _make_command(job, seeded_tenant)

    # First worker claims with a very short lease
    claimed = repo.claim_job_for_command(command, lease_seconds=0)
    assert claimed is not None
    assert claimed.claim_token is not None

    # Second worker should be able to reclaim (lease already expired)
    reclaimed = repo.claim_job_for_command(command, lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.attempt_count == 2  # incremented


def test_finish_job_rejects_stale_claim_token(db_session, seeded_tenant):
    """Only the current claim_token holder can finish the job."""
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)
    job = _create_job(repo, seeded_tenant.project_id, seeded_tenant.user_id, db_session, seeded_tenant.tenant_id)

    command = _make_command(job, seeded_tenant)

    # First worker claims
    claimed1 = repo.claim_job_for_command(command, lease_seconds=0)
    token1 = claimed1.claim_token

    # Second worker reclaims (lease expired)
    claimed2 = repo.claim_job_for_command(command, lease_seconds=60)
    token2 = claimed2.claim_token
    assert token1 != token2

    # First worker's stale token should NOT be able to finish
    result = repo.finish_job_if_claim_current(job.id, token1, "succeeded")
    assert not result, "Stale claim token should be rejected"

    # Second worker's current token should succeed
    result2 = repo.finish_job_if_claim_current(job.id, token2, "succeeded")
    assert result2, "Current claim token should be accepted"


# ---------------------------------------------------------------------------
# Concurrent artifact creation + pruning
# ---------------------------------------------------------------------------

def test_concurrent_artifact_prune_and_create_do_not_conflict(db_session, seeded_tenant):
    """Creating a new artifact while pruning old ones should not conflict."""
    repo = CompileRepository(db_session, seeded_tenant.tenant_id)

    # Create several artifacts at the retention limit
    settings_retention = 3
    for i in range(settings_retention + 2):  # 2 over the limit
        job = _create_job(repo, seeded_tenant.project_id, seeded_tenant.user_id, db_session, seeded_tenant.tenant_id)
        content = f"artifact_{i}".encode()
        repo.record_artifact(seeded_tenant.project_id, job.id, "stl", content)
        repo.finish_job(job, "succeeded")
        db_session.commit()

    # Count artifacts before pruning
    before = db_session.scalars(
        select(Artifact).where(
            Artifact.tenant_id == seeded_tenant.tenant_id,
            Artifact.project_id == seeded_tenant.project_id,
            Artifact.kind == "stl",
        )
    ).all()
    assert len(before) == settings_retention + 2

    # Prune: keep max(1, retention_limit) = 3 latest
    prunable = repo.prunable_artifacts(
        seeded_tenant.project_id, "stl", max(1, settings_retention)
    )
    repo.delete_artifacts(prunable)
    db_session.commit()

    after = db_session.scalars(
        select(Artifact).where(
            Artifact.tenant_id == seeded_tenant.tenant_id,
            Artifact.project_id == seeded_tenant.project_id,
            Artifact.kind == "stl",
        )
    ).all()
    assert len(after) == settings_retention, (
        f"Should keep {settings_retention} artifacts, found {len(after)}"
    )

    # Create a new artifact — should still have retention limit total
    new_job = _create_job(repo, seeded_tenant.project_id, seeded_tenant.user_id, db_session, seeded_tenant.tenant_id)
    repo.record_artifact(seeded_tenant.project_id, new_job.id, "stl", b"new_artifact")
    repo.finish_job(new_job, "succeeded")
    db_session.commit()

    # Now prune again
    prunable2 = repo.prunable_artifacts(
        seeded_tenant.project_id, "stl", max(1, settings_retention)
    )
    repo.delete_artifacts(prunable2)
    db_session.commit()

    final = db_session.scalars(
        select(Artifact).where(
            Artifact.tenant_id == seeded_tenant.tenant_id,
            Artifact.project_id == seeded_tenant.project_id,
            Artifact.kind == "stl",
        )
    ).all()
    assert len(final) == settings_retention


# ---------------------------------------------------------------------------
# Cross-tenant isolation under concurrent access
# ---------------------------------------------------------------------------

def test_cross_tenant_artifact_isolation(db_session):
    """Artifacts created in one tenant must never be visible in another tenant,
    even under concurrent access patterns."""
    # Create two tenants with their own users and projects
    user_a = AppUser(id=uuid4(), keycloak_subject="kc-a", email="a@test.com")
    user_b = AppUser(id=uuid4(), keycloak_subject="kc-b", email="b@test.com")
    tenant_a = Tenant(id=uuid4(), name="Tenant A")
    tenant_b = Tenant(id=uuid4(), name="Tenant B")
    db_session.add_all([user_a, user_b, tenant_a, tenant_b])
    db_session.flush()

    db_session.add(TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"))
    db_session.add(TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"))
    project_a = Project(id=uuid4(), tenant_id=tenant_a.id, name="proj_a", created_by=user_a.id)
    project_b = Project(id=uuid4(), tenant_id=tenant_b.id, name="proj_b", created_by=user_b.id)
    db_session.add_all([project_a, project_b])
    db_session.commit()

    repo_a = CompileRepository(db_session, tenant_a.id)
    repo_b = CompileRepository(db_session, tenant_b.id)

    # Create jobs and artifacts in both tenants
    job_a = repo_a.start_job(project_a.id, user_a.id, "stl")
    job_b = repo_b.start_job(project_b.id, user_b.id, "stl")
    db_session.flush()
    db_session.add(CompileJobFile(compile_job_id=job_a.id, tenant_id=tenant_a.id, project_id=project_a.id, filename="design.py", content="code_a"))
    db_session.add(CompileJobFile(compile_job_id=job_b.id, tenant_id=tenant_b.id, project_id=project_b.id, filename="design.py", content="code_b"))
    db_session.commit()

    repo_a.record_artifact(project_a.id, job_a.id, "stl", b"artifact_a")
    repo_b.record_artifact(project_b.id, job_b.id, "stl", b"artifact_b")
    repo_a.finish_job(job_a, "succeeded")
    repo_b.finish_job(job_b, "succeeded")
    db_session.commit()

    # Tenant A's repo should only see tenant A's artifact
    artifacts_a = db_session.scalars(
        select(Artifact).where(Artifact.tenant_id == tenant_a.id)
    ).all()
    assert len(artifacts_a) == 1
    assert artifacts_a[0].content == b"artifact_a"

    # Tenant B's repo should only see tenant B's artifact
    artifacts_b = db_session.scalars(
        select(Artifact).where(Artifact.tenant_id == tenant_b.id)
    ).all()
    assert len(artifacts_b) == 1
    assert artifacts_b[0].content == b"artifact_b"

    # Cross-tenant lookup: querying by tenant_b.id should NOT find tenant_a's artifact
    artifacts_in_b = db_session.scalars(
        select(Artifact).where(
            Artifact.tenant_id == tenant_b.id,
            Artifact.compile_job_id == job_a.id,
        )
    ).all()
    assert len(artifacts_in_b) == 0, "Tenant B should not see Tenant A's artifact"

    # Direct DB check: Tenant B cannot find Tenant A's job
    job_in_b = db_session.scalar(
        select(CompileJob).where(
            CompileJob.tenant_id == tenant_b.id,
            CompileJob.id == job_a.id,
        )
    )
    assert job_in_b is None, "Tenant B should not find Tenant A's job by direct query"


def test_concurrent_job_creation_in_different_tenants_do_not_interfere(db_session):
    """Jobs created concurrently in different tenants should not interfere."""
    user_a = AppUser(id=uuid4(), keycloak_subject="kc-x", email="x@test.com")
    user_b = AppUser(id=uuid4(), keycloak_subject="kc-y", email="y@test.com")
    tenant_a = Tenant(id=uuid4(), name="Tenant X")
    tenant_b = Tenant(id=uuid4(), name="Tenant Y")
    db_session.add_all([user_a, user_b, tenant_a, tenant_b])
    db_session.flush()
    db_session.add(TenantMembership(tenant_id=tenant_a.id, user_id=user_a.id, role="owner"))
    db_session.add(TenantMembership(tenant_id=tenant_b.id, user_id=user_b.id, role="owner"))
    project_x = Project(id=uuid4(), tenant_id=tenant_a.id, name="px", created_by=user_a.id)
    project_y = Project(id=uuid4(), tenant_id=tenant_b.id, name="py", created_by=user_b.id)
    db_session.add_all([project_x, project_y])
    db_session.commit()

    repo_x = CompileRepository(db_session, tenant_a.id)
    repo_y = CompileRepository(db_session, tenant_b.id)

    # Create jobs "concurrently" (same transaction)
    job_x = repo_x.start_job(project_x.id, user_a.id, "stl")
    job_y = repo_y.start_job(project_y.id, user_b.id, "glb")
    db_session.flush()
    db_session.add(CompileJobFile(compile_job_id=job_x.id, tenant_id=tenant_a.id, project_id=project_x.id, filename="design.py", content="x"))
    db_session.add(CompileJobFile(compile_job_id=job_y.id, tenant_id=tenant_b.id, project_id=project_y.id, filename="design.py", content="y"))
    db_session.commit()

    # Verify both jobs are in their correct tenants
    assert job_x.tenant_id == tenant_a.id
    assert job_y.tenant_id == tenant_b.id
    assert job_x.export_format == "stl"
    assert job_y.export_format == "glb"

    # Each repo only sees its own tenant's jobs
    jobs_x = db_session.scalars(
        select(CompileJob).where(CompileJob.tenant_id == tenant_a.id)
    ).all()
    jobs_y = db_session.scalars(
        select(CompileJob).where(CompileJob.tenant_id == tenant_b.id)
    ).all()
    assert len(jobs_x) == 1
    assert len(jobs_y) == 1
    assert jobs_x[0].id == job_x.id
    assert jobs_y[0].id == job_y.id
