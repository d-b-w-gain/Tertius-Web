"""Intus API contract smoke tests for guest workspace import.

These tests verify the backend endpoints used by ui/src/workflows/shared/guestImport.ts:
  - list projects
  - create projects
  - save project files
  - activate the imported project

Browser-only behavior such as localStorage cleanup and collision-safe guest naming
belongs in the UI tests that execute the production guestImport.ts implementation.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from core.models import UserWorkspaceState
from core.repositories import ProjectRepository

# ---------------------------------------------------------------------------
# Minimal guest workspace-shaped payloads for driving the Intus API contract
# ---------------------------------------------------------------------------

GUEST_WORKSPACE_VERSION = 1


def _make_guest_workspace(projects: list[dict]) -> dict:
    """Build a minimal guest workspace-shaped payload for API smoke tests."""
    return {
        "version": GUEST_WORKSPACE_VERSION,
        "activeProject": projects[0]["name"] if projects else None,
        "projects": projects,
    }


def _make_guest_project(name: str, files: list[dict]) -> dict:
    """Build a single guest project entry."""
    return {
        "name": name,
        "files": files,
    }


def _make_guest_file(filename: str, content: str) -> dict:
    """Build a single guest file entry."""
    return {
        "filename": filename,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Integration: import via Intus API endpoints
# ---------------------------------------------------------------------------

class TestGuestImportIntegration:
    """Smoke-test the Intus API endpoints called by guestImport.ts."""

    def test_import_creates_projects_and_files(self, authenticated_intus_client, db_session, seeded_tenant):
        """Import a guest workspace and verify all projects and files are created."""
        project_name = f"guest_test_{uuid4().hex[:8]}"
        guest_data = _make_guest_workspace([
            _make_guest_project(project_name, [
                _make_guest_file("design.py", "import build123d as bd\nbox = bd.Box(10,10,10)\n"),
                _make_guest_file("utils.py", "def helper():\n    pass\n"),
            ]),
        ])

        # Simulate the import flow via API calls
        for project in guest_data["projects"]:
            name = project["name"]

            # Check for collision with existing projects
            # GET /projects returns {"projects": ["name1", "name2", ...]}
            existing = authenticated_intus_client.get("/projects")
            existing_names = existing.json()["projects"]

            if name in existing_names:
                name = f"{name}_{int(time.time())}"

            # Create project
            create_resp = authenticated_intus_client.post(f"/projects/{name}/new")
            assert create_resp.status_code == 200, f"Failed to create project: {create_resp.json()}"

            # Save files (CodeRequest uses "code" and "file" fields)
            for f in project["files"]:
                save_resp = authenticated_intus_client.post(
                    f"/projects/{name}/save",
                    json={"code": f["content"], "file": f["filename"]},
                )
                assert save_resp.status_code == 200, (
                    f"Failed to save {f['filename']}: {save_resp.json()}"
                )

            # Activate the project
            activate_resp = authenticated_intus_client.post(f"/projects/{name}/activate")
            assert activate_resp.status_code == 200

        # Verify project exists in DB
        repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
        project = repo.get_project(project_name)
        assert project is not None, "Imported project should exist in DB"

        # Verify files exist
        # GET /projects/{name}/files returns {"files": ["design.py", "utils.py", ...]}
        files_resp = authenticated_intus_client.get(f"/projects/{project_name}/files")
        assert files_resp.status_code == 200
        filenames = files_resp.json()["files"]
        assert "design.py" in filenames
        assert "utils.py" in filenames

        # Verify file content
        code_resp = authenticated_intus_client.get(
            f"/projects/{project_name}/code", params={"filename": "design.py"}
        )
        assert code_resp.status_code == 200
        assert "bd.Box(10,10,10)" in code_resp.text

    def test_import_preserves_multiple_files(self, authenticated_intus_client, db_session, seeded_tenant):
        """Import a project with multiple files and verify all are preserved."""
        project_name = f"multifile_{uuid4().hex[:8]}"
        guest_data = _make_guest_workspace([
            _make_guest_project(project_name, [
                _make_guest_file("design.py", "import build123d as bd\n"),
                _make_guest_file("params.py", "WIDTH = 100\nHEIGHT = 50\n"),
                _make_guest_file("helpers.py", "def clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n"),
            ]),
        ])

        # Import
        for project in guest_data["projects"]:
            authenticated_intus_client.post(f"/projects/{project['name']}/new")
            for f in project["files"]:
                authenticated_intus_client.post(
                    f"/projects/{project['name']}/save",
                    json={"code": f["content"], "file": f["filename"]},
                )

        # Verify all files
        files_resp = authenticated_intus_client.get(f"/projects/{project_name}/files")
        assert files_resp.status_code == 200
        filenames = files_resp.json()["files"]
        assert "design.py" in filenames
        assert "params.py" in filenames
        assert "helpers.py" in filenames

        # design.py should always be first
        assert filenames[0] == "design.py"

    def test_import_handles_empty_workspace(self, authenticated_intus_client):
        """Importing an empty workspace should not error."""
        # Empty workspace with no projects should be a no-op
        _make_guest_workspace([])

        # Just verify the projects endpoint still works
        response = authenticated_intus_client.get("/projects")
        assert response.status_code == 200

    def test_import_activates_last_project(self, authenticated_intus_client, db_session, seeded_tenant):
        """After importing, the last project should be activated."""
        projects = [
            _make_guest_project(f"batch_{uuid4().hex[:8]}_1", [
                _make_guest_file("design.py", "x = 1\n"),
            ]),
            _make_guest_project(f"batch_{uuid4().hex[:8]}_2", [
                _make_guest_file("design.py", "y = 2\n"),
            ]),
        ]

        last_name = None
        for project in projects:
            last_name = project["name"]
            authenticated_intus_client.post(f"/projects/{project['name']}/new")
            for f in project["files"]:
                authenticated_intus_client.post(
                    f"/projects/{project['name']}/save",
                    json={"code": f["content"], "file": f["filename"]},
                )

        # Activate the last project
        assert last_name is not None
        authenticated_intus_client.post(f"/projects/{last_name}/activate")

        # Verify it's the active project
        state = db_session.scalar(
            select(UserWorkspaceState).where(
                UserWorkspaceState.user_id == seeded_tenant.user_id,
                UserWorkspaceState.tenant_id == seeded_tenant.tenant_id,
            )
        )
        repo = ProjectRepository(db_session, seeded_tenant.tenant_id)
        active_project = repo.get_project(last_name)
        assert active_project is not None
        assert state.active_project_id == active_project.id
