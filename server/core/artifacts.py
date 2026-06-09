from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4


CONTENT_TYPES = {
    "stl": "application/octet-stream",
    "step": "application/step",
    "pdf": "application/pdf",
}


@dataclass(frozen=True)
class StoredArtifact:
    storage_key: str
    content_type: str
    byte_size: int


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def write_bytes(self, tenant_id: UUID, project_id: UUID, kind: str, content: bytes) -> StoredArtifact:
        ext = kind.lower()
        storage_key = f"{tenant_id}/{project_id}/{uuid4()}.{ext}"
        path = self.path_for(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredArtifact(
            storage_key=storage_key,
            content_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
            byte_size=len(content),
        )

    def delete(self, storage_key: str) -> None:
        path = self.path_for(storage_key)
        if path.exists():
            path.unlink()

    def path_for(self, storage_key: str) -> Path:
        key_path = Path(storage_key)
        if key_path.is_absolute() or ".." in key_path.parts:
            raise ValueError("Invalid artifact storage key")

        root = self.root.resolve()
        path = (root / key_path).resolve()
        if path != root and root not in path.parents:
            raise ValueError("Artifact path escapes artifact root")
        return path
