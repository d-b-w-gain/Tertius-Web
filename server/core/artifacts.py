from __future__ import annotations

from uuid import UUID, uuid4


CONTENT_TYPES = {
    "stl": "application/octet-stream",
    "step": "application/step",
    "gltf": "model/gltf+json",
    "glb": "model/gltf-binary",
    "pdf": "application/pdf",
    "timus_views": "application/json",
}


def content_type_for_kind(kind: str) -> str:
    return CONTENT_TYPES.get(kind.lower(), "application/octet-stream")


def artifact_storage_key(tenant_id: UUID, project_id: UUID, kind: str) -> str:
    ext = kind.lower()
    return f"{tenant_id}/{project_id}/{uuid4()}.{ext}"
