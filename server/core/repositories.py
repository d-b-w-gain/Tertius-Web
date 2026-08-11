from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer
from sqlalchemy.orm.attributes import flag_modified

from core.artifacts import artifact_storage_key, content_type_for_kind
from core.compile_messages import CompileCommand, CompileResultPayload
from core.models import (
    Artifact,
    CompileJob,
    CompileJobAsset,
    CompileJobFile,
    CompileUsageRecord,
    LlmEditJob,
    Project,
    ProjectAsset,
    ProjectFile,
    ProjectImportJob,
    SourceSnapshot,
    SourceSnapshotFile,
    now_utc,
)
from core.project_assets import (
    BREP_MEDIA_TYPE,
    IMPORT_3MF_CONVERSION_VERSION,
    MANIFEST_MEDIA_TYPE,
    Import3mfManifest,
    generated_3mf_design_source,
)
from core.pi_agent_messages import PiAgentProgressBatch, PiAgentProgressSnapshot


FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.py$")
PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
WORKER_LOST_ERROR = "Compile worker stopped before reporting a result"
WORKER_LOST_ERROR_CODE = "worker_lost"
WORKER_LOST_USER_MESSAGE = "Compile worker stopped unexpectedly. The model may have exceeded available memory or the worker was restarted."
LLM_EDIT_WORKER_LOST_ERROR = "LLM edit worker stopped before reporting a result"
LLM_EDIT_WORKER_LOST_ERROR_CODE = "worker_lost"
LLM_EDIT_WORKER_LOST_USER_MESSAGE = "AI generation stopped unexpectedly. Try again."
PI_AGENT_PROGRESS_MAX_EVENTS = 128
PI_AGENT_PROGRESS_MAX_BYTES = 64 * 1024


class ProgressBatchApplyOutcome(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    IGNORED_TERMINAL = "ignored_terminal"
    REJECTED_IDENTITY = "rejected_identity"
    REJECTED_SEQUENCE = "rejected_sequence"
    REJECTED_SNAPSHOT = "rejected_snapshot"
    STALE_EXECUTION = "stale_execution"


class FileVersionConflictError(RuntimeError):
    pass


class ActiveProjectImportError(RuntimeError):
    pass


class AssetIntegrityError(RuntimeError):
    pass


class ImportNotRetryableError(RuntimeError):
    pass


class ProjectNameConflictError(RuntimeError):
    pass


class StaleImportExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectAssetMetadata:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    logical_name: str
    display_name: str
    kind: str
    media_type: str
    byte_size: int
    sha256: str
    revision: int
    conversion_version: str | None
    created_at: datetime


def normalize_file_version(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds")


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
        projects = self.db.scalars(select(Project).where(Project.tenant_id == self.tenant_id).order_by(Project.name)).all()
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
            .where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
            )
            .order_by(ProjectFile.filename)
        ).all()
        filenames = [file.filename for file in files]
        if "design.py" in filenames:
            filenames.remove("design.py")
            filenames.insert(0, "design.py")
        return filenames

    def list_file_metadata(self, project_name: str) -> list[dict[str, object]]:
        project = self.get_project(project_name)
        if project is None:
            return []
        files = self.db.scalars(
            select(ProjectFile)
            .where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
            )
            .order_by(ProjectFile.filename)
        ).all()
        rows = [{"id": file.id, "filename": file.filename, "updated_at": file.updated_at} for file in files]
        rows.sort(key=lambda row: (row["filename"] != "design.py", row["filename"]))
        return rows

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

    def stage_code_update(
        self,
        project_name: str,
        filename: str,
        content: str,
        user_id: UUID,
        message: str,
    ) -> bool:
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
            file = ProjectFile(
                tenant_id=self.tenant_id,
                project_id=project.id,
                filename=filename,
                content=content,
            )
            self.db.add(file)
        else:
            file.content = content
            file.updated_at = now_utc()

        project.updated_at = now_utc()
        self.db.flush()
        self._snapshot(project, user_id, message)
        return True

    def save_code(
        self,
        project_name: str,
        filename: str,
        content: str,
        user_id: UUID,
        message: str,
    ) -> bool:
        saved = self.stage_code_update(project_name, filename, content, user_id, message)
        if saved:
            self.db.commit()
        return saved

    def files_by_ids(
        self,
        project_name: str,
        file_ids: list[UUID],
        for_update: bool = False,
    ) -> dict[UUID, ProjectFile]:
        project = self.get_project(project_name)
        if project is None or not file_ids:
            return {}
        stmt = (
            select(ProjectFile)
            .where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
                ProjectFile.id.in_(file_ids),
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            # Acquire row-level locks so the version re-check and the
            # subsequent content mutation in stage_file_updates happen
            # atomically. A concurrent save in another transaction will
            # either block until we commit (and then be the last writer)
            # or, if it already committed, be visible to this query so the
            # caller can detect the stale version.
            stmt = stmt.with_for_update()
        rows = self.db.scalars(stmt).all()
        return {row.id: row for row in rows}

    def stage_file_updates(
        self,
        project_name: str,
        updates: dict[UUID, str],
        user_id: UUID,
        message: str,
        expected_updated_at: dict[UUID, datetime] | None = None,
    ) -> tuple[SourceSnapshot, list[ProjectFile]] | None:
        project = self.get_project(project_name)
        if project is None:
            return None
        # When an expected version is supplied, load the target rows with
        # SELECT ... FOR UPDATE so the version re-check and the subsequent
        # content mutation happen while holding row locks. This closes the
        # race window between the endpoint's pre-check and the final write:
        # a concurrent save that commits after the pre-check but before this
        # point is already visible (-> conflict), and a concurrent save that
        # is still in-flight blocks on these locks until we commit, so it can
        # never be silently overwritten by the AI edit.
        files = self.files_by_ids(
            project_name,
            list(updates.keys()),
            for_update=expected_updated_at is not None,
        )
        if set(files) != set(updates):
            return None
        if expected_updated_at is not None:
            for file_id, expected_version in expected_updated_at.items():
                file = files.get(file_id)
                if file is None or normalize_file_version(file.updated_at) != normalize_file_version(expected_version):
                    raise FileVersionConflictError("Files changed while AI edit was running")
        now = now_utc()
        changed: list[ProjectFile] = []
        for file_id, content in updates.items():
            file = files[file_id]
            if file.content != content:
                file.content = content
                file.updated_at = now
                changed.append(file)
        if not changed:
            raise ValueError("LLM returned no file changes")
        project.updated_at = now
        self.db.flush()
        truncated_message = (message or "LLM edit")[:500]
        snapshot = self._snapshot(project, user_id, truncated_message)
        return snapshot, changed

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
            .where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
            )
            .order_by(ProjectFile.filename)
        ).all()
        return {file.filename: file.content for file in files}

    def snapshot_history(self, project_name: str) -> list[str] | None:
        project = self.get_project(project_name)
        if project is None:
            return None

        rows = self.db.scalars(
            select(SourceSnapshot)
            .where(
                SourceSnapshot.tenant_id == self.tenant_id,
                SourceSnapshot.project_id == project.id,
            )
            .order_by(SourceSnapshot.created_at.desc())
            .limit(50)
        ).all()
        return [f"{row.content_hash[:7]} {row.message}" for row in rows]

    def _snapshot(self, project: Project, user_id: UUID, message: str) -> SourceSnapshot:
        files = self.db.scalars(
            select(ProjectFile)
            .where(
                ProjectFile.tenant_id == self.tenant_id,
                ProjectFile.project_id == project.id,
            )
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
            self.db.add(
                SourceSnapshotFile(
                    snapshot_id=snapshot.id,
                    filename=file.filename,
                    content=file.content,
                )
            )
        return snapshot


class ProjectAssetRepository:
    _METADATA_COLUMNS = (
        ProjectAsset.id,
        ProjectAsset.tenant_id,
        ProjectAsset.project_id,
        ProjectAsset.logical_name,
        ProjectAsset.display_name,
        ProjectAsset.kind,
        ProjectAsset.media_type,
        ProjectAsset.byte_size,
        ProjectAsset.sha256,
        ProjectAsset.revision,
        ProjectAsset.conversion_version,
        ProjectAsset.created_at,
    )

    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def create(
        self,
        *,
        project_id: UUID,
        logical_name: str,
        display_name: str,
        kind: str,
        media_type: str,
        content: bytes,
        revision: int,
        conversion_version: str | None = None,
    ) -> ProjectAsset:
        immutable_content = bytes(content)
        row = ProjectAsset(
            tenant_id=self.tenant_id,
            project_id=project_id,
            logical_name=logical_name,
            display_name=display_name,
            kind=kind,
            media_type=media_type,
            content=immutable_content,
            byte_size=len(immutable_content),
            sha256=hashlib.sha256(immutable_content).hexdigest(),
            revision=revision,
            conversion_version=conversion_version,
        )
        self.db.add(row)
        self.db.flush()
        return row

    @staticmethod
    def _metadata(row) -> ProjectAssetMetadata:
        return ProjectAssetMetadata(**dict(row._mapping))

    def get_metadata(self, asset_id: UUID, *, project_id: UUID | None = None) -> ProjectAssetMetadata | None:
        stmt = select(*self._METADATA_COLUMNS).where(
            ProjectAsset.id == asset_id,
            ProjectAsset.tenant_id == self.tenant_id,
        )
        if project_id is not None:
            stmt = stmt.where(ProjectAsset.project_id == project_id)
        row = self.db.execute(stmt).one_or_none()
        return None if row is None else self._metadata(row)

    def list_metadata(self, project_id: UUID) -> list[ProjectAssetMetadata]:
        rows = self.db.execute(
            select(*self._METADATA_COLUMNS)
            .where(
                ProjectAsset.tenant_id == self.tenant_id,
                ProjectAsset.project_id == project_id,
            )
            .order_by(ProjectAsset.kind, ProjectAsset.revision, ProjectAsset.id)
        ).all()
        return [self._metadata(row) for row in rows]

    def get_content(self, asset_id: UUID, *, project_id: UUID | None = None) -> bytes | None:
        stmt = select(ProjectAsset.content).where(
            ProjectAsset.id == asset_id,
            ProjectAsset.tenant_id == self.tenant_id,
        )
        if project_id is not None:
            stmt = stmt.where(ProjectAsset.project_id == project_id)
        return self.db.scalar(stmt)

    def allocate_revision(self, project_id: UUID, *, import_job_id: UUID | None = None) -> int:
        if import_job_id is None:
            lock = self.db.scalar(
                select(Project)
                .where(
                    Project.id == project_id,
                    Project.tenant_id == self.tenant_id,
                )
                .with_for_update()
            )
        else:
            lock = self.db.scalar(
                select(ProjectImportJob)
                .where(
                    ProjectImportJob.id == import_job_id,
                    ProjectImportJob.project_id == project_id,
                    ProjectImportJob.tenant_id == self.tenant_id,
                )
                .with_for_update()
            )
        if lock is None:
            raise ValueError("Project import scope not found")
        current = self.db.scalar(
            select(func.max(ProjectAsset.revision)).where(
                ProjectAsset.tenant_id == self.tenant_id,
                ProjectAsset.project_id == project_id,
                ProjectAsset.kind.in_(["derived_brep", "import_manifest"]),
            )
        )
        return (current or 0) + 1

    def successful_import_pair(self, project_id: UUID) -> tuple[ProjectAsset, ProjectAsset] | None:
        job = self.db.scalar(
            select(ProjectImportJob)
            .where(
                ProjectImportJob.tenant_id == self.tenant_id,
                ProjectImportJob.project_id == project_id,
                ProjectImportJob.status == "succeeded",
                ProjectImportJob.brep_asset_id.is_not(None),
                ProjectImportJob.manifest_asset_id.is_not(None),
            )
            .order_by(
                ProjectImportJob.finished_at.desc(),
                ProjectImportJob.created_at.desc(),
                ProjectImportJob.id.desc(),
            )
        )
        if job is None:
            return None
        rows = self.db.scalars(
            select(ProjectAsset)
            .options(undefer(ProjectAsset.content))
            .where(
                ProjectAsset.tenant_id == self.tenant_id,
                ProjectAsset.project_id == project_id,
                ProjectAsset.id.in_([job.brep_asset_id, job.manifest_asset_id]),
            )
        ).all()
        by_id = {row.id: row for row in rows}
        if job.brep_asset_id not in by_id or job.manifest_asset_id not in by_id:
            raise AssetIntegrityError("successful import assets are incomplete")
        return by_id[job.brep_asset_id], by_id[job.manifest_asset_id]


class ProjectImportRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.assets = ProjectAssetRepository(db, tenant_id)

    def create_import(
        self,
        *,
        project_name: str,
        requested_by: UUID,
        display_name: str,
        media_type: str,
        content: bytes,
    ) -> tuple[Project, ProjectAsset, ProjectImportJob]:
        project_name = require_valid_project_name(project_name)
        try:
            with self.db.begin_nested():
                collision = self.db.scalar(
                    select(Project).where(
                        Project.tenant_id == self.tenant_id,
                        Project.name == project_name,
                    )
                )
                if collision is not None:
                    raise ProjectNameConflictError("Project name already exists")
                project = Project(
                    tenant_id=self.tenant_id,
                    name=project_name,
                    created_by=requested_by,
                )
                self.db.add(project)
                self.db.flush()
                source = self.assets.create(
                    project_id=project.id,
                    logical_name="source.3mf",
                    display_name=display_name,
                    kind="source_3mf",
                    media_type=media_type,
                    content=content,
                    revision=1,
                )
                job = self.create_queued(project.id, requested_by, source.id)
            return project, source, job
        except IntegrityError as exc:
            raise ProjectNameConflictError("Project name already exists") from exc

    def create_queued(self, project_id: UUID, requested_by: UUID, source_asset_id: UUID) -> ProjectImportJob:
        try:
            with self.db.begin_nested():
                project = self.db.scalar(
                    select(Project)
                    .where(
                        Project.id == project_id,
                        Project.tenant_id == self.tenant_id,
                    )
                    .with_for_update()
                )
                if project is None:
                    raise ValueError("Project not found")
                source = self.db.scalar(
                    select(ProjectAsset.id).where(
                        ProjectAsset.id == source_asset_id,
                        ProjectAsset.project_id == project_id,
                        ProjectAsset.tenant_id == self.tenant_id,
                        ProjectAsset.kind == "source_3mf",
                    )
                )
                if source is None:
                    raise AssetIntegrityError("Source asset is outside project scope")
                active = self.db.scalar(
                    select(ProjectImportJob.id).where(
                        ProjectImportJob.project_id == project_id,
                        ProjectImportJob.tenant_id == self.tenant_id,
                        ProjectImportJob.status.in_(["queued", "running"]),
                    )
                )
                if active is not None:
                    raise ActiveProjectImportError("Project already has an active import")
                job = ProjectImportJob(
                    tenant_id=self.tenant_id,
                    project_id=project_id,
                    requested_by=requested_by,
                    source_asset_id=source_asset_id,
                    attempt=1,
                    execution_id=uuid.uuid4(),
                    status="queued",
                    retryable=False,
                    progress_payload={},
                )
                self.db.add(job)
                self.db.flush()
            return job
        except IntegrityError as exc:
            raise ActiveProjectImportError("Project already has an active import") from exc

    def get_job(self, job_id: UUID) -> ProjectImportJob | None:
        return self.db.scalar(
            select(ProjectImportJob).where(
                ProjectImportJob.id == job_id,
                ProjectImportJob.tenant_id == self.tenant_id,
            )
        )

    def _lock_job(self, job_id: UUID) -> ProjectImportJob:
        job = self.db.scalar(
            select(ProjectImportJob)
            .where(
                ProjectImportJob.id == job_id,
                ProjectImportJob.tenant_id == self.tenant_id,
            )
            .with_for_update()
        )
        if job is None:
            raise ValueError("Import job not found")
        return job

    @staticmethod
    def _require_execution(job: ProjectImportJob, execution_id: UUID) -> None:
        if job.execution_id != execution_id:
            raise StaleImportExecutionError("Import execution is stale")

    def mark_running(self, job_id: UUID, execution_id: UUID) -> ProjectImportJob:
        job = self._lock_job(job_id)
        self._require_execution(job, execution_id)
        if job.status not in {"queued", "running"}:
            raise ValueError("Import job is terminal")
        job.status = "running"
        if job.started_at is None:
            job.started_at = now_utc()
        self.db.flush()
        return job

    def mark_progress(self, job_id: UUID, execution_id: UUID, progress_payload: dict) -> ProjectImportJob:
        job = self._lock_job(job_id)
        self._require_execution(job, execution_id)
        if job.status not in {"queued", "running"}:
            raise ValueError("Import job is terminal")
        job.progress_payload = dict(progress_payload)
        flag_modified(job, "progress_payload")
        self.db.flush()
        return job

    def mark_failed(
        self,
        job_id: UUID,
        execution_id: UUID,
        *,
        error: str,
        error_code: str,
        user_message: str,
        retryable: bool,
    ) -> ProjectImportJob:
        job = self._lock_job(job_id)
        self._require_execution(job, execution_id)
        if job.status not in {"queued", "running"}:
            raise ValueError("Import job is terminal")
        job.status = "failed"
        job.error = error
        job.error_code = error_code
        job.user_message = user_message
        job.retryable = retryable
        job.finished_at = now_utc()
        self.db.flush()
        return job

    def retry(self, job_id: UUID) -> ProjectImportJob:
        job = self._lock_job(job_id)
        if job.status != "failed" or not job.retryable:
            raise ImportNotRetryableError("Import job is not retryable")
        job.attempt += 1
        job.execution_id = uuid.uuid4()
        job.status = "queued"
        job.error = None
        job.error_code = None
        job.user_message = None
        job.retryable = False
        job.progress_payload = {}
        flag_modified(job, "progress_payload")
        job.brep_asset_id = None
        job.manifest_asset_id = None
        job.started_at = None
        job.finished_at = None
        self.db.flush()
        return job

    def apply_success(
        self,
        *,
        job_id: UUID,
        execution_id: UUID,
        source_sha256: str,
        brep_content: bytes,
        manifest_content: bytes,
        user_id: UUID,
    ) -> ProjectImportJob:
        with self.db.begin_nested():
            job = self._lock_job(job_id)
            self._require_execution(job, execution_id)
            source = self.db.scalar(
                select(ProjectAsset).where(
                    ProjectAsset.id == job.source_asset_id,
                    ProjectAsset.project_id == job.project_id,
                    ProjectAsset.tenant_id == self.tenant_id,
                    ProjectAsset.kind == "source_3mf",
                )
            )
            if source is None or source.sha256 != source_sha256:
                raise AssetIntegrityError("source digest mismatch")
            if job.status == "succeeded":
                return job
            if job.status not in {"queued", "running"}:
                raise ValueError("Import job is terminal")
            manifest = Import3mfManifest.model_validate_json(manifest_content)
            brep_bytes = bytes(brep_content)
            brep_sha256 = hashlib.sha256(brep_bytes).hexdigest()
            if manifest.source_sha256 != source.sha256:
                raise AssetIntegrityError("manifest source digest mismatch")
            if manifest.brep_sha256 != brep_sha256 or manifest.brep_byte_size != len(brep_bytes):
                raise AssetIntegrityError("manifest BREP integrity mismatch")
            revision = self.assets.allocate_revision(job.project_id, import_job_id=job.id)
            brep = self.assets.create(
                project_id=job.project_id,
                logical_name="source.brep",
                display_name="source.brep",
                kind="derived_brep",
                media_type=BREP_MEDIA_TYPE,
                content=brep_bytes,
                revision=revision,
                conversion_version=IMPORT_3MF_CONVERSION_VERSION,
            )
            manifest_asset = self.assets.create(
                project_id=job.project_id,
                logical_name="source.manifest.json",
                display_name="source.manifest.json",
                kind="import_manifest",
                media_type=MANIFEST_MEDIA_TYPE,
                content=bytes(manifest_content),
                revision=revision,
                conversion_version=IMPORT_3MF_CONVERSION_VERSION,
            )
            project = self.db.scalar(
                select(Project).where(
                    Project.id == job.project_id,
                    Project.tenant_id == self.tenant_id,
                )
            )
            if project is None:
                raise ValueError("Project not found")
            staged = ProjectRepository(self.db, self.tenant_id).stage_code_update(
                project.name,
                "design.py",
                generated_3mf_design_source(),
                user_id,
                "Import 3MF",
            )
            if not staged:
                raise ValueError("Project not found")
            job.brep_asset_id = brep.id
            job.manifest_asset_id = manifest_asset.id
            job.status = "succeeded"
            job.error = None
            job.error_code = None
            job.user_message = None
            job.retryable = False
            job.finished_at = now_utc()
            self.db.flush()
        return job


class CompileRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def start_job(
        self,
        project_id: UUID,
        user_id: UUID,
        export_format: str,
        status: str = "running",
        originating_llm_edit_job_id: UUID | None = None,
    ) -> CompileJob:
        job = CompileJob(
            tenant_id=self.tenant_id,
            project_id=project_id,
            requested_by=user_id,
            status=status,
            export_format=export_format,
            originating_llm_edit_job_id=originating_llm_edit_job_id,
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

    def mark_job_dispatched(self, job: CompileJob, lease_seconds: int) -> None:
        now = now_utc()
        job.status = "running"
        job.error = None
        job.error_code = None
        job.user_message = None
        job.retryable = False
        job.finished_at = None
        job.claimed_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempt_count += 1

    def mark_job_publish_pending(self, job: CompileJob, error: str) -> None:
        job.status = "queued"
        job.error = error
        job.error_code = "publish_pending"
        job.user_message = "Compile queued but could not be published immediately. It will be retried."
        job.retryable = True
        job.claimed_at = None
        job.lease_expires_at = None

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

    def snapshot_job_assets(
        self,
        job: CompileJob,
        assets: dict[str, tuple[ProjectAsset, str, str]],
    ) -> None:
        if job.tenant_id != self.tenant_id:
            raise ValueError("Compile job is outside tenant scope")
        paired_assets = [asset for asset, _, _ in assets.values()]
        by_kind = {asset.kind: asset for asset in paired_assets}
        if len(paired_assets) != 2 or set(by_kind) != {
            "derived_brep",
            "import_manifest",
        }:
            raise AssetIntegrityError("Compile assets are not a derived import pair")
        successful_pair = self.db.scalar(
            select(ProjectImportJob.id).where(
                ProjectImportJob.tenant_id == self.tenant_id,
                ProjectImportJob.project_id == job.project_id,
                ProjectImportJob.status == "succeeded",
                ProjectImportJob.brep_asset_id == by_kind["derived_brep"].id,
                ProjectImportJob.manifest_asset_id == by_kind["import_manifest"].id,
            )
        )
        if successful_pair is None:
            raise AssetIntegrityError(
                "Compile assets are not from one successful import"
            )
        for logical_filename, (asset, object_bucket, object_key) in assets.items():
            if asset.tenant_id != self.tenant_id or asset.project_id != job.project_id:
                raise AssetIntegrityError("Compile asset is outside job scope")
            self.db.add(
                CompileJobAsset(
                    compile_job_id=job.id,
                    tenant_id=job.tenant_id,
                    project_id=job.project_id,
                    project_asset_id=asset.id,
                    logical_filename=logical_filename,
                    sha256=asset.sha256,
                    byte_size=asset.byte_size,
                    object_bucket=object_bucket,
                    object_key=object_key,
                )
            )
        self.db.flush()

    def assets_for_job(self, job_id: UUID, *, project_id: UUID | None = None) -> list[CompileJobAsset]:
        stmt = select(CompileJobAsset).where(
            CompileJobAsset.compile_job_id == job_id,
            CompileJobAsset.tenant_id == self.tenant_id,
        )
        if project_id is not None:
            stmt = stmt.where(CompileJobAsset.project_id == project_id)
        return list(self.db.scalars(stmt.order_by(CompileJobAsset.logical_filename)))

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

    def stale_running_jobs(self, older_than_seconds: int, limit: int = 50) -> list[CompileJob]:
        now = now_utc()
        cutoff = now - timedelta(seconds=older_than_seconds)
        return list(
            self.db.scalars(
                select(CompileJob)
                .where(
                    CompileJob.tenant_id == self.tenant_id,
                    CompileJob.status == "running",
                    or_(
                        (CompileJob.lease_expires_at.is_not(None) & (CompileJob.lease_expires_at < now)),
                        (CompileJob.claimed_at.is_not(None) & (CompileJob.claimed_at < cutoff)),
                        (CompileJob.claimed_at.is_(None) & CompileJob.lease_expires_at.is_(None) & (CompileJob.created_at < cutoff)),
                    ),
                )
                .order_by(CompileJob.lease_expires_at, CompileJob.created_at)
                .limit(limit)
            )
        )

    def reconcile_stale_job(
        self,
        project_id: UUID,
        job_id: UUID,
        queued_older_than_seconds: int,
        running_older_than_seconds: int,
    ) -> CompileJob | None:
        now = now_utc()
        queued_cutoff = now - timedelta(seconds=queued_older_than_seconds)
        running_cutoff = now - timedelta(seconds=running_older_than_seconds)
        stmt = (
            update(CompileJob)
            .where(
                CompileJob.id == job_id,
                CompileJob.tenant_id == self.tenant_id,
                CompileJob.project_id == project_id,
                or_(
                    (
                        (CompileJob.status == "running")
                        & or_(
                            (CompileJob.lease_expires_at.is_not(None) & (CompileJob.lease_expires_at < now)),
                            (CompileJob.claimed_at.is_not(None) & (CompileJob.claimed_at < running_cutoff)),
                            (CompileJob.claimed_at.is_(None) & CompileJob.lease_expires_at.is_(None) & (CompileJob.created_at < running_cutoff)),
                        )
                    ),
                    ((CompileJob.status == "queued") & (CompileJob.created_at < queued_cutoff)),
                ),
            )
            .values(
                status="failed",
                error=WORKER_LOST_ERROR,
                error_code=WORKER_LOST_ERROR_CODE,
                user_message=WORKER_LOST_USER_MESSAGE,
                retryable=True,
                finished_at=now,
                lease_expires_at=None,
            )
            .returning(CompileJob.id)
        )
        reconciled_id = self.db.scalar(stmt)
        if reconciled_id is not None:
            self.db.flush()
        return self.get_job(project_id, job_id)

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

    def artifact_for_job(self, job_id: UUID, kind: str | None = None) -> Artifact | None:
        query = select(Artifact).where(
            Artifact.tenant_id == self.tenant_id,
            Artifact.compile_job_id == job_id,
        )
        if kind is not None:
            query = query.where(Artifact.kind == kind.lower())
        return self.db.scalar(query.order_by(Artifact.created_at.desc(), Artifact.id.desc()))

    def record_usage(
        self,
        *,
        project_id: UUID,
        compile_job_id: UUID,
        requested_by: UUID,
        export_format: str,
        status: str,
        compute_duration_seconds: float,
        artifact_byte_size: int,
        cost_cents: int,
        base_rate_cents_per_hour: int,
        format_multiplier: float,
    ) -> CompileUsageRecord:
        existing = self.db.scalar(
            select(CompileUsageRecord).where(
                CompileUsageRecord.tenant_id == self.tenant_id,
                CompileUsageRecord.compile_job_id == compile_job_id,
            )
        )
        if existing is not None:
            return existing

        record = CompileUsageRecord(
            tenant_id=self.tenant_id,
            project_id=project_id,
            compile_job_id=compile_job_id,
            requested_by=requested_by,
            export_format=export_format,
            status=status,
            compute_duration_seconds=compute_duration_seconds,
            artifact_byte_size=artifact_byte_size,
            cost_cents=cost_cents,
            base_rate_cents_per_hour=base_rate_cents_per_hour,
            format_multiplier=format_multiplier,
        )
        self.db.add(record)
        self.db.flush()
        return record


class LlmEditRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def start_job(
        self,
        project_id: UUID,
        user_id: UUID,
        request_payload: dict,
        status: str = "queued",
    ) -> LlmEditJob:
        job = LlmEditJob(
            tenant_id=self.tenant_id,
            project_id=project_id,
            requested_by=user_id,
            status=status,
            request_payload=request_payload,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def get_job(self, project_id: UUID, job_id: UUID) -> LlmEditJob | None:
        return self.db.scalar(
            select(LlmEditJob).where(
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == project_id,
                LlmEditJob.id == job_id,
            )
        )

    def apply_progress_batch(
        self,
        batch: PiAgentProgressBatch,
    ) -> ProgressBatchApplyOutcome:
        if batch.tenant_id != self.tenant_id:
            return ProgressBatchApplyOutcome.REJECTED_IDENTITY

        job = self.db.scalar(
            select(LlmEditJob)
            .where(
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == batch.project_id,
                LlmEditJob.id == batch.job_id,
            )
            .with_for_update()
        )
        if job is None:
            return ProgressBatchApplyOutcome.REJECTED_IDENTITY
        if job.status in {"succeeded", "failed"}:
            return ProgressBatchApplyOutcome.IGNORED_TERMINAL

        try:
            persisted = PiAgentProgressSnapshot.model_validate(job.progress_payload) if job.progress_payload else None
        except ValidationError:
            return ProgressBatchApplyOutcome.REJECTED_SNAPSHOT
        if persisted is not None and persisted.execution_id == batch.execution_id:
            if batch.batch_sequence <= persisted.last_batch_sequence:
                return ProgressBatchApplyOutcome.DUPLICATE
            if batch.events[0].sequence <= persisted.last_sequence:
                return ProgressBatchApplyOutcome.REJECTED_SEQUENCE
            events = [*persisted.events, *batch.events]
            truncated_before_sequence = persisted.truncated_before_sequence
        else:
            if persisted is not None and batch.execution_started_at <= persisted.execution_started_at:
                return ProgressBatchApplyOutcome.STALE_EXECUTION
            events = list(batch.events)
            truncated_before_sequence = None

        if len(events) > PI_AGENT_PROGRESS_MAX_EVENTS:
            discarded_events = events[:-PI_AGENT_PROGRESS_MAX_EVENTS]
            events = events[-PI_AGENT_PROGRESS_MAX_EVENTS:]
            truncated_before_sequence = discarded_events[-1].sequence

        snapshot = PiAgentProgressSnapshot(
            schema_version=1,
            execution_id=batch.execution_id,
            execution_started_at=batch.execution_started_at,
            last_batch_sequence=batch.batch_sequence,
            last_sequence=batch.events[-1].sequence,
            truncated_before_sequence=truncated_before_sequence,
            events=events,
        )
        while len(snapshot.model_dump_json().encode("utf-8")) > PI_AGENT_PROGRESS_MAX_BYTES:
            discarded_event = events[0]
            events = events[1:]
            snapshot = snapshot.model_copy(
                update={
                    "truncated_before_sequence": discarded_event.sequence,
                    "events": events,
                }
            )

        job.progress_payload = snapshot.model_dump(mode="json")
        flag_modified(job, "progress_payload")
        self.db.flush()
        return ProgressBatchApplyOutcome.APPLIED

    def list_jobs_for_project(self, project_id: UUID, *, limit: int = 200) -> list[LlmEditJob]:
        normalized_limit = max(1, min(limit, 200))
        return list(
            self.db.scalars(
                select(LlmEditJob)
                .where(
                    LlmEditJob.tenant_id == self.tenant_id,
                    LlmEditJob.project_id == project_id,
                )
                .order_by(LlmEditJob.created_at.asc(), LlmEditJob.id.asc())
                .limit(normalized_limit)
            )
        )

    def list_recent_terminal_jobs(
        self,
        project_id: UUID,
        *,
        limit: int = 200,
    ) -> list[LlmEditJob]:
        normalized_limit = max(1, min(limit, 200))
        jobs = list(
            self.db.scalars(
                select(LlmEditJob)
                .where(
                    LlmEditJob.tenant_id == self.tenant_id,
                    LlmEditJob.project_id == project_id,
                    LlmEditJob.status.in_(["succeeded", "failed"]),
                )
                .order_by(desc(LlmEditJob.created_at), desc(LlmEditJob.id))
                .limit(normalized_limit)
            )
        )
        return list(reversed(jobs))

    def get_compile_job_for_llm_edit(self, project_id: UUID, llm_edit_job_id: UUID) -> CompileJob | None:
        return self.db.scalar(
            select(CompileJob)
            .where(
                CompileJob.tenant_id == self.tenant_id,
                CompileJob.project_id == project_id,
                CompileJob.originating_llm_edit_job_id == llm_edit_job_id,
            )
            .order_by(CompileJob.created_at.desc(), CompileJob.id.desc())
        )

    def mark_job_dispatched(self, job: LlmEditJob) -> bool:
        dispatched_id = self.db.scalar(
            update(LlmEditJob)
            .where(
                LlmEditJob.id == job.id,
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == job.project_id,
                LlmEditJob.status == "queued",
            )
            .values(
                status="running",
                error=None,
                error_code=None,
                user_message=None,
                retryable=False,
                attempt_count=LlmEditJob.attempt_count + 1,
            )
            .returning(LlmEditJob.id)
        )
        self.db.expire(job)
        self.db.refresh(job)
        if dispatched_id is None:
            return False
        payload = dict(job.request_payload)
        payload["dispatched_at"] = now_utc().isoformat()
        job.request_payload = payload
        flag_modified(job, "request_payload")
        self.db.flush()
        return True

    def finish_job(
        self,
        job: LlmEditJob,
        status: str,
        error: str | None = None,
        error_code: str | None = None,
        user_message: str | None = None,
        retryable: bool = False,
        result_payload: dict | None = None,
    ) -> None:
        job.status = status
        job.error = error
        job.error_code = error_code
        job.user_message = user_message
        job.retryable = retryable
        job.result_payload = result_payload
        job.finished_at = now_utc()

    def reconcile_stale_job(
        self,
        project_id: UUID,
        job_id: UUID,
        older_than_seconds: int,
    ) -> LlmEditJob | None:
        cutoff = now_utc() - timedelta(seconds=older_than_seconds)
        stmt = (
            update(LlmEditJob)
            .where(
                LlmEditJob.id == job_id,
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == project_id,
                LlmEditJob.status.in_(["queued", "running"]),
                LlmEditJob.created_at < cutoff,
            )
            .values(
                status="failed",
                error=LLM_EDIT_WORKER_LOST_ERROR,
                error_code=LLM_EDIT_WORKER_LOST_ERROR_CODE,
                user_message=LLM_EDIT_WORKER_LOST_USER_MESSAGE,
                retryable=True,
                finished_at=now_utc(),
            )
            .returning(LlmEditJob.id)
        )
        reconciled_id = self.db.scalar(stmt)
        if reconciled_id is not None:
            self.db.flush()
        return self.get_job(project_id, job_id)

    def reconcile_stale_jobs_for_project(self, project_id: UUID, older_than_seconds: int) -> int:
        now = now_utc()
        cutoff = now - timedelta(seconds=older_than_seconds)
        reconciled_ids = self.db.scalars(
            update(LlmEditJob)
            .where(
                LlmEditJob.tenant_id == self.tenant_id,
                LlmEditJob.project_id == project_id,
                LlmEditJob.status.in_(["queued", "running"]),
                LlmEditJob.created_at < cutoff,
            )
            .values(
                status="failed",
                error=LLM_EDIT_WORKER_LOST_ERROR,
                error_code=LLM_EDIT_WORKER_LOST_ERROR_CODE,
                user_message=LLM_EDIT_WORKER_LOST_USER_MESSAGE,
                retryable=True,
                finished_at=now,
            )
            .returning(LlmEditJob.id)
        )
        self.db.flush()
        return len(reconciled_ids.all())


class UsageRepository:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id

    def total_summary(self) -> dict:
        row = self.db.execute(
            select(
                func.count(CompileUsageRecord.id),
                func.coalesce(func.sum(CompileUsageRecord.cost_cents), 0),
                func.coalesce(func.sum(CompileUsageRecord.compute_duration_seconds), 0.0),
                func.coalesce(func.sum(CompileUsageRecord.artifact_byte_size), 0),
            ).where(CompileUsageRecord.tenant_id == self.tenant_id)
        ).one()
        return {
            "total_jobs": row[0],
            "total_cost_cents": row[1],
            "total_compute_seconds": row[2],
            "total_artifact_bytes": row[3],
        }

    def daily_breakdown(self, days: int = 30) -> list[dict]:
        rows = self.db.execute(
            select(
                func.date_trunc("day", CompileUsageRecord.created_at).label("day"),
                func.count(CompileUsageRecord.id),
                func.coalesce(func.sum(CompileUsageRecord.cost_cents), 0),
                func.coalesce(func.sum(CompileUsageRecord.compute_duration_seconds), 0.0),
            )
            .where(
                CompileUsageRecord.tenant_id == self.tenant_id,
                CompileUsageRecord.created_at >= func.now() - func.make_interval(0, 0, 0, days),
            )
            .group_by("day")
            .order_by("day")
        ).all()
        return [
            {
                "day": str(row[0]),
                "job_count": row[1],
                "cost_cents": row[2],
                "compute_seconds": row[3],
            }
            for row in rows
        ]

    def monthly_breakdown(self, months: int = 12) -> list[dict]:
        rows = self.db.execute(
            select(
                func.date_trunc("month", CompileUsageRecord.created_at).label("month"),
                func.count(CompileUsageRecord.id),
                func.coalesce(func.sum(CompileUsageRecord.cost_cents), 0),
                func.coalesce(func.sum(CompileUsageRecord.compute_duration_seconds), 0.0),
            )
            .where(
                CompileUsageRecord.tenant_id == self.tenant_id,
                CompileUsageRecord.created_at >= func.now() - func.make_interval(0, months, 0, 0),
            )
            .group_by("month")
            .order_by("month")
        ).all()
        return [
            {
                "month": str(row[0]),
                "job_count": row[1],
                "cost_cents": row[2],
                "compute_seconds": row[3],
            }
            for row in rows
        ]

    def project_breakdown(self) -> list[dict]:
        from core.models import Project

        rows = self.db.execute(
            select(
                CompileUsageRecord.project_id,
                Project.name,
                func.count(CompileUsageRecord.id),
                func.coalesce(func.sum(CompileUsageRecord.cost_cents), 0),
                func.coalesce(func.sum(CompileUsageRecord.compute_duration_seconds), 0.0),
            )
            .join(Project, Project.id == CompileUsageRecord.project_id)
            .where(CompileUsageRecord.tenant_id == self.tenant_id)
            .group_by(CompileUsageRecord.project_id, Project.name)
            .order_by(func.sum(CompileUsageRecord.cost_cents).desc())
        ).all()
        return [
            {
                "project_id": str(row[0]),
                "project_name": row[1],
                "job_count": row[2],
                "cost_cents": row[3],
                "compute_seconds": row[4],
            }
            for row in rows
        ]

    def format_breakdown(self) -> list[dict]:
        rows = self.db.execute(
            select(
                CompileUsageRecord.export_format,
                func.count(CompileUsageRecord.id),
                func.coalesce(func.sum(CompileUsageRecord.cost_cents), 0),
                func.coalesce(func.sum(CompileUsageRecord.compute_duration_seconds), 0.0),
            )
            .where(CompileUsageRecord.tenant_id == self.tenant_id)
            .group_by(CompileUsageRecord.export_format)
            .order_by(func.sum(CompileUsageRecord.cost_cents).desc())
        ).all()
        return [
            {
                "export_format": row[0],
                "job_count": row[1],
                "cost_cents": row[2],
                "compute_seconds": row[3],
            }
            for row in rows
        ]

    def recent_jobs(self, limit: int = 50) -> list[dict]:
        from core.models import AppUser

        rows = self.db.execute(
            select(
                CompileUsageRecord.created_at,
                CompileUsageRecord.export_format,
                CompileUsageRecord.status,
                CompileUsageRecord.compute_duration_seconds,
                CompileUsageRecord.artifact_byte_size,
                CompileUsageRecord.cost_cents,
                AppUser.username,
            )
            .join(AppUser, AppUser.id == CompileUsageRecord.requested_by)
            .where(CompileUsageRecord.tenant_id == self.tenant_id)
            .order_by(CompileUsageRecord.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "created_at": str(row[0]),
                "export_format": row[1],
                "status": row[2],
                "compute_duration_seconds": row[3],
                "artifact_byte_size": row[4],
                "cost_cents": row[5],
                "username": row[6],
            }
            for row in rows
        ]
