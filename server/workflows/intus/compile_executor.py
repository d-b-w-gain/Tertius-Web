from __future__ import annotations

import traceback
from uuid import UUID

from sqlalchemy.orm import Session

from core.compile_messages import CompileResultEvent
from core.compile_runtime import hydrate_project_files
from core.compile_sandbox import run_compile_sandbox
from core.models import CompileJob, now_utc
from core.repositories import CompileRepository


def execute_compile_job(
    db: Session,
    job_id: UUID,
    claim_token: UUID,
    timeout_seconds: int,
    artifact_retention_limit: int,
) -> CompileResultEvent | None:
    job = db.get(CompileJob, job_id)
    if job is None:
        raise ValueError(f"Compile job {job_id} not found")

    compile_repo = CompileRepository(db, job.tenant_id)

    try:
        files = compile_repo.files_for_job(job.id)
        if not files:
            finished = compile_repo.finish_job_if_claim_current(
                job.id,
                claim_token,
                "failed",
                error="Compile job snapshot is empty",
                error_code="missing_snapshot",
                user_message="Compile failed because the submitted source snapshot is missing. Try again.",
                retryable=True,
            )
            if finished is None:
                db.rollback()
                return None
            db.commit()
            return _event(
                finished,
                "failed",
                error_code=finished.error_code,
                user_message=finished.user_message,
                error=finished.error,
                retryable=finished.retryable,
            )

        with hydrate_project_files(files) as project_dir:
            result = run_compile_sandbox(project_dir, job.export_format, timeout_seconds=timeout_seconds)
            if not result.success:
                error = result.error or result.stderr or "Compile failed"
                finished = compile_repo.finish_job_if_claim_current(
                    job.id,
                    claim_token,
                    "failed",
                    error=error,
                    error_code=_error_code(error),
                    user_message=_user_message(error),
                    retryable=True,
                )
                if finished is None:
                    db.rollback()
                    return None
                db.commit()
                return _event(
                    finished,
                    "failed",
                    error_code=finished.error_code,
                    user_message=finished.user_message,
                    error=error,
                    retryable=finished.retryable,
                )

            if result.output_path is None:
                finished = compile_repo.finish_job_if_claim_current(
                    job.id,
                    claim_token,
                    "failed",
                    error="Compile succeeded without an output artifact",
                    error_code="missing_artifact",
                    user_message="Compile failed before an artifact was produced. Try again.",
                    retryable=True,
                )
                if finished is None:
                    db.rollback()
                    return None
                db.commit()
                return _event(
                    finished,
                    "failed",
                    error_code=finished.error_code,
                    user_message=finished.user_message,
                    error=finished.error,
                    retryable=finished.retryable,
                )
            output_bytes = result.output_path.read_bytes()

        artifact = compile_repo.record_artifact(job.project_id, job.id, job.export_format, output_bytes)
        finished = compile_repo.finish_job_if_claim_current(job.id, claim_token, "succeeded")
        if finished is None:
            db.rollback()
            return None
        pruned = compile_repo.prunable_artifacts(
            job.project_id,
            job.export_format,
            max(1, artifact_retention_limit),
        )
        compile_repo.delete_artifacts(pruned)
        db.commit()
        return _event(finished, "succeeded", artifact_id=artifact.id)
    except Exception as exc:
        db.rollback()
        job = db.get(CompileJob, job_id)
        error = traceback.format_exc()
        if job is not None:
            compile_repo = CompileRepository(db, job.tenant_id)
            finished = compile_repo.finish_job_if_claim_current(
                job.id,
                claim_token,
                "failed",
                error=error,
                error_code="executor_error",
                user_message="Compile failed before the worker could finish. Try again.",
                retryable=True,
            )
            if finished is None:
                db.rollback()
                return None
            db.commit()
            return _event(
                finished,
                "failed",
                error_code=finished.error_code,
                user_message=finished.user_message,
                error=str(exc),
                retryable=finished.retryable,
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
