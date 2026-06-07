from __future__ import annotations

import hashlib
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Project, ProjectFile, SourceSnapshot, SourceSnapshotFile, now_utc


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

    def save_code(self, project_name: str, filename: str, content: str, user_id: UUID, message: str) -> bool:
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
        self.db.commit()
        return True

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
