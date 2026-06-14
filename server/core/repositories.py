from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from core.artifacts import artifact_storage_key, content_type_for_kind
from core.compile_messages import CompileCommand, CompileResultPayload
from core.models import Artifact, CompileJob, CompileJobFile, Project, ProjectFile, SourceSnapshot, SourceSnapshotFile, now_utc


FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.py$")
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def require_valid_python_filename(filename: str) -> str:
    if not FILENAME_RE.fullmatch(filename):
        raise ValueError("Invalid filename")
    return filename


def require_valid_project_name(name: str) -> str:
    if not PROJECT_NAME_RE.fullmatch(name):
        raise ValueError("Invalid project name")
    return name


class ProjectRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def list_projects(self) -> list[str]:
        projects = self.db.scalars(
            select(Project).where(Project.tenant_id == self.tenant_id).order_by(Project.name)
        ).all()
        return [project.name for project in projects]

    def get_project(self, name: str) -> Project | None:
        name = require_valid_project_name(name)
        return self.db.scalar(select(Project).where(Project.tenant_id == self.tenant_id, Project.name == name))

    def activate_project(self, project_name: str, user_id: UUID) -> bool:
        project = self.get_project(project_name)
        if project is None:
            return False

        if not self.set_active_project(user_id, project.id):
            return False
        self.db.commit()
        return True

    def set_active_project(self, user_id: UUID, project_id: UUID) -> bool:
        from core.models import UserWorkspaceState

        project = self.db.scalar(
            select(Project).where(
                Project.tenant_id == self.tenant_id,
                Project.id == project_id,
            )
        )
        if project is None:
            return False

        active_file = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == "design.py",
            )
        )
        state = self.db.scalar(
            select(UserWorkspaceState).where(
                UserWorkspaceState.user_id == user_id,
                UserWorkspaceState.tenant_id == self.tenant_id,
            )
        )
        if state is None:
            state = UserWorkspaceState(
                user_id=user_id,
                tenant_id=self.tenant_id,
                active_project_id=project.id,
                active_file_id=active_file.id if active_file else None,
            )
            self.db.add(state)
        else:
            state.active_project_id = project.id
            state.active_file_id = active_file.id if active_file else None
        self.db.flush()
        return True

    def create_project(self, name: str, user_id: UUID, default_code: str) -> Project:
        name = require_valid_project_name(name)
        project = Project(tenant_id=self.tenant_id, name=name, created_by=user_id)
        self.db.add(project)
        self.db.flush()
        self.db.add(
            ProjectFile(
                tenant_id=self.tenant_id,
                project_id=project.id,
                filename="design.py",
                content=default_code,
            )
        )
        self.db.commit()
        return project

    def list_files(self, project_name: str) -> list[str]:
        project = self.get_project(project_name)
        if project is None:
            return []

        files = self.db.scalars(
            select(ProjectFile)
            .where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)
            .order_by(ProjectFile.filename)
        ).all()
        filenames = [file.filename for file in files]
        if "design.py" in filenames:
            filenames.remove("design.py")
            filenames.insert(0, "design.py")
        return filenames

    def get_code(self, project_name: str, filename: str) -> str | None:
        filename = require_valid_python_filename(filename)
        project = self.get_project(project_name)
        if project is None:
            return None

        file = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        return None if file is None else file.content

    def stage_code_update(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
        filename = require_valid_python_filename(filename)
        project = self.get_project(project_name)
        if project is None:
            return False

        file = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        if file is None:
            file = ProjectFile(tenant_id=self.tenant_id, project_id=project.id, filename=filename, content=content)
            self.db.add(file)
        else:
            file.content = content
            file.updated_at = now_utc()

        project.updated_at = now_utc()
        self.db.flush()
        self._snapshot(project, user_id, message)
        return True

    def save_code(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
        saved = self.stage_code_update(project_name, filename, content, user_id, message)
        if saved:
            self.db.commit()
        return saved

    def delete_file(self, project_name: str, filename: str) -> bool:
        filename = require_valid_python_filename(filename)
        if filename == "design.py":
            raise ValueError("Cannot delete design.py")

        project = self.get_project(project_name)
        if project is None:
            return False

        file = self.db.scalar(
            select(ProjectFile).where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.filename == filename,
            )
        )
        if file is None:
            return False

        self.db.delete(file)
        self.db.commit()
        return True

    def files_for_runtime(self, project_name: str) -> dict[str, str] | None:
        project = self.get_project(project_name)
        if project is None:
            return None

        files = self.db.scalars(
            select(ProjectFile)
            .where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)
            .order_by(ProjectFile.filename)
        ).all()
        return {file.filename: file.content for file in files}

    def snapshot_history(self, project_name: str) -> list[str] | None:
        project = self.get_project(project_name)
        if project is None:
            return None

        rows = self.db.scalars(
            select(SourceSnapshot)
            .where(SourceSnapshot.tenant_id == self.tenant_id, SourceSnapshot.project_id == project.id)
            .order_by(SourceSnapshot.created_at.desc())
            .limit(50)
        ).all()
        return [f"{row.content_hash[:7]} {row.message}" for row in rows]

    def _snapshot(self, project: Project, user_id: UUID, message: str) -> None:
        files = self.db.scalars(
            select(ProjectFile)
            .where(ProjectFile.tenant_id == self.tenant_id, ProjectFile.project_id == project.id)
            .order_by(ProjectFile.filename)
        ).all()
        digest_input = "\n".join(f"{file.filename}:{file.content}" for file in files)
        snapshot = SourceSnapshot(
            tenant_id=self.tenant_id,
            project_id=project.id,
            message=message,
            content_hash=hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
            created_by=user_id,
        )
        self.db.add(snapshot)
        self.db.flush()

        for file in files:
            self.db.add(SourceSnapshotFile(snapshot_id=snapshot.id, filename=file.filename, content=file.content))


class CompileRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def start_job(self, project_id: UUID, user_id: UUID, export_format: str, status: str = "running") -> CompileJob:
        job = CompileJob(
            tenant_id=self.tenant_id,
            project_id=project_id,
            requested_by=user_id,
            status=status,
            export_format=export_format,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def get_job(self, project_id: UUID, job_id: UUID) -> CompileJob | None:
        return self.db.scalar(
            select(CompileJob).where(
                CompileJob.tenant_id == self.tenant_id,
                CompileJob.project_id == project_id,
                CompileJob.id == job_id,
            )
        )

    def get_job_for_command(self, command: CompileCommand) -> CompileJob | None:
        return self.db.scalar(
            select(CompileJob).where(
                CompileJob.id == command.job_id,
                CompileJob.tenant_id == command.tenant_id,
                CompileJob.project_id == command.project_id,
                CompileJob.requested_by == command.requested_by,
                CompileJob.export_format == command.export_format,
            )
        )

    def get_job_for_result(self, result: CompileResultPayload) -> CompileJob | None:
        return self.db.scalar(
            select(CompileJob).where(
                CompileJob.id == result.job_id,
                CompileJob.tenant_id == result.tenant_id,
                CompileJob.project_id == result.project_id,
                CompileJob.export_format == result.export_format,
            )
        )

    def mark_job_running(self, job: CompileJob) -> None:
        job.status = "running"
        job.error = None
        job.error_code = None
        job.user_message = None
        job.retryable = False

    def finish_job(
        self,
        job: CompileJob,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
    ) -> None:
        job.status = status
        job.error = error
        job.error_code = error_code
        job.user_message = user_message
        job.retryable = retryable
        job.finished_at = now_utc()

    def claim_job_for_command(self, command: CompileCommand, lease_seconds: int) -> CompileJob | None:
        now = now_utc()
        claim_token = uuid.uuid4()
        stmt = (
            update(CompileJob)
            .where(
                CompileJob.id == command.job_id,
                CompileJob.tenant_id == command.tenant_id,
                CompileJob.project_id == command.project_id,
                CompileJob.requested_by == command.requested_by,
                CompileJob.export_format == command.export_format,
                or_(
                    CompileJob.status == "queued",
                    (CompileJob.status == "running") & (CompileJob.lease_expires_at < now),
                ),
            )
            .values(
                status="running",
                error=None,
                error_code=None,
                user_message=None,
                retryable=False,
                finished_at=None,
                claim_token=claim_token,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempt_count=CompileJob.attempt_count + 1,
            )
            .returning(CompileJob.id)
        )
        claimed_id = self.db.scalar(stmt)
        if claimed_id is None:
            return None
        return self.db.get(CompileJob, claimed_id)

    def snapshot_job_files(self, job: CompileJob, files: dict[str, str]) -> None:
        for filename, content in files.items():
            self.db.add(
                CompileJobFile(
                    compile_job_id=job.id,
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    filename=filename,
                    content=content,
                )
            )

    def files_for_job(self, job_id: UUID) -> dict[str, str]:
        rows = self.db.scalars(
            select(CompileJobFile).where(
                CompileJobFile.compile_job_id == job_id,
                CompileJobFile.tenant_id == self.tenant_id,
            )
        ).all()
        return {row.filename: row.content for row in rows}

    def stale_queued_jobs(self, older_than_seconds: int, limit: int = 50) -> list[CompileJob]:
        cutoff = now_utc() - timedelta(seconds=older_than_seconds)
        return list(
            self.db.scalars(
                select(CompileJob)
                .where(
                    CompileJob.tenant_id == self.tenant_id,
                    CompileJob.status == "queued",
                    CompileJob.created_at < cutoff,
                )
                .order_by(CompileJob.created_at)
                .limit(limit)
            )
        )

    def stale_running_jobs(self, limit: int = 50) -> list[CompileJob]:
        now = now_utc()
        return list(
            self.db.scalars(
                select(CompileJob)
                .where(
                    CompileJob.tenant_id == self.tenant_id,
                    CompileJob.status == "running",
                    CompileJob.lease_expires_at.is_not(None),
                    CompileJob.lease_expires_at < now,
                )
                .order_by(CompileJob.lease_expires_at)
                .limit(limit)
            )
        )

    def finish_job_if_claim_current(
        self,
        job_id: UUID,
        claim_token: UUID,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
    ) -> CompileJob | None:
        stmt = (
            update(CompileJob)
            .where(
                CompileJob.id == job_id,
                CompileJob.tenant_id == self.tenant_id,
                CompileJob.status == "running",
                CompileJob.claim_token == claim_token,
            )
            .values(
                status=status,
                error=error,
                error_code=error_code,
                user_message=user_message,
                retryable=retryable,
                finished_at=now_utc(),
                lease_expires_at=None,
            )
            .returning(CompileJob.id)
        )
        finished_id = self.db.scalar(stmt)
        if finished_id is None:
            return None
        return self.db.get(CompileJob, finished_id)

    def record_artifact(
        self,
        project_id: UUID,
        job_id: UUID | None,
        kind: str,
        content: bytes,
        storage_key: str | None = None,
        content_type: str | None = None,
    ) -> Artifact:
        normalized_kind = kind.lower()
        artifact = Artifact(
            tenant_id=self.tenant_id,
            project_id=project_id,
            compile_job_id=job_id,
            kind=normalized_kind,
            storage_key=storage_key or artifact_storage_key(self.tenant_id, project_id, normalized_kind),
            content_type=content_type or content_type_for_kind(normalized_kind),
            byte_size=len(content),
            content=content,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def prunable_artifacts(self, project_id: UUID, kind: str, keep_latest: int) -> list[Artifact]:
        keep_latest = max(0, keep_latest)
        query = (
            select(Artifact)
            .where(
                Artifact.tenant_id == self.tenant_id,
                Artifact.project_id == project_id,
                Artifact.kind == kind.lower(),
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
            .offset(keep_latest)
        )
        return list(self.db.scalars(query).all())

    def delete_artifacts(self, artifacts: list[Artifact]) -> None:
        for artifact in artifacts:
            self.db.delete(artifact)
        self.db.flush()

    def artifact_for_job(self, job_id: UUID) -> Artifact | None:
        return self.db.scalar(
            select(Artifact)
            .where(
                Artifact.tenant_id == self.tenant_id,
                Artifact.compile_job_id == job_id,
            )
            .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        )
