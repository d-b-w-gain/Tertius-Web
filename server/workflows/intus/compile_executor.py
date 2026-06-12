from __future__ import annotations

import traceback
from uuid import UUID

from sqlalchemy.orm import Session

from core.compile_messages import CompileResultEvent
from core.compile_runtime import hydrate_project_files
from core.compile_sandbox import run_compile_sandbox
from core.models import CompileJob, Project, now_utc
from core.repositories import CompileRepository, ProjectRepository


def execute_compile_job(
    db: Session,
    job_id: UUID,
    timeout_seconds: int,
    artifact_retention_limit: int,
) -> CompileResultEvent:
    job = db.get(CompileJob, job_id)
    if job is None:
        raise ValueError(f"Compile job {job_id} not found")

    compile_repo = CompileRepository(db, job.tenant_id)
    project_repo = ProjectRepository(db, job.tenant_id)
    compile_repo.mark_job_running(job)
    db.commit()

    project = db.get(Project, job.project_id)
    if project is None:
        compile_repo.finish_job(
            job,
            "failed",
            error="Project not found",
            error_code="project_not_found",
            user_message="Compile failed because the project no longer exists.",
            retryable=False,
        )
        db.commit()
        return _event(
            job,
            "failed",
            error_code="project_not_found",
            user_message="Compile failed because the project no longer exists.",
            error="Project not found",
            retryable=False,
        )

    try:
        files = project_repo.files_for_runtime(project.name)
        if files is None:
            raise RuntimeError("Project not found")

        with hydrate_project_files(files) as project_dir:
            result = run_compile_sandbox(project_dir, job.export_format, timeout_seconds=timeout_seconds)
            if not result.success:
                error = result.error or result.stderr or "Compile failed"
                compile_repo.finish_job(
                    job,
                    "failed",
                    error=error,
                    error_code=_error_code(error),
                    user_message=_user_message(error),
                    retryable=True,
                )
                db.commit()
                return _event(
                    job,
                    "failed",
                    error_code=job.error_code,
                    user_message=job.user_message,
                    error=error,
                    retryable=job.retryable,
                )

            if result.output_path is None:
                compile_repo.finish_job(
                    job,
                    "failed",
                    error="Compile succeeded without an output artifact",
                    error_code="missing_artifact",
                    user_message="Compile failed before an artifact was produced. Try again.",
                    retryable=True,
                )
                db.commit()
                return _event(
                    job,
                    "failed",
                    error_code=job.error_code,
                    user_message=job.user_message,
                    error=job.error,
                    retryable=job.retryable,
                )
            output_bytes = result.output_path.read_bytes()

        artifact = compile_repo.record_artifact(job.project_id, job.id, job.export_format, output_bytes)
        compile_repo.finish_job(job, "succeeded")
        pruned = compile_repo.prunable_artifacts(
            job.project_id,
            job.export_format,
            max(1, artifact_retention_limit),
        )
        compile_repo.delete_artifacts(pruned)
        db.commit()
        return _event(job, "succeeded", artifact_id=artifact.id)
    except Exception as exc:
        db.rollback()
        job = db.get(CompileJob, job_id)
        error = traceback.format_exc()
        if job is not None:
            compile_repo = CompileRepository(db, job.tenant_id)
            compile_repo.finish_job(
                job,
                "failed",
                error=error,
                error_code="executor_error",
                user_message="Compile failed before the worker could finish. Try again.",
                retryable=True,
            )
            db.commit()
            return _event(
                job,
                "failed",
                error_code=job.error_code,
                user_message=job.user_message,
                error=str(exc),
                retryable=job.retryable,
            )
        raise


def _event(
    job: CompileJob,
    status: str,
    artifact_id: UUID | None = None,
    error_code: str | None = None,
    user_message: str | None = None,
    error: str | None = None,
    retryable: bool = False,
) -> CompileResultEvent:
    return CompileResultEvent(
        job_id=job.id,
        tenant_id=job.tenant_id,
        project_id=job.project_id,
        status=status,
        export_format=job.export_format,
        artifact_id=artifact_id,
        error_code=error_code,
        user_message=user_message,
        error=error,
        retryable=retryable,
        finished_at=job.finished_at or now_utc(),
    )


def _error_code(error: str) -> str:
    if "timed out" in error.lower():
        return "timeout"
    return "sandbox_error"


def _user_message(error: str) -> str:
    if "timed out" in error.lower():
        return "Compile timed out after 10 minutes. Try again."
    return "Compile failed. Fix the model source and try again."
