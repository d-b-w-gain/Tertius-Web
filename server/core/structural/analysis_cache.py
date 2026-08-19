from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.models import StructuralAnalysisResult

from .contracts import StructuralSnapshot


STRUCTURAL_ANALYSIS_CACHE_SCHEMA_VERSION = "1"
DEFAULT_COMBINATION_KEY = "__governing_default__"
EMPTY_INPUT_DIGEST = sha256(b"").hexdigest()
_SOURCE_COMMIT_PATH = Path("/app/.source-commit")


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def structural_engine_version() -> str:
    try:
        version = _SOURCE_COMMIT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    if not version or version in {"development", "unknown"}:
        server_root = Path(__file__).resolve().parents[2]
        repository_root = server_root.parent
        structural_roots = (
            server_root / "core" / "structural",
            server_root / "workflows" / "structural",
        )
        candidates = [
            *sorted(
                path
                for root in structural_roots
                for path in root.rglob("*")
                if path.is_file() and path.suffix in {".py", ".json", ".csv"}
            ),
            server_root / "core" / "models.py",
            server_root / "core" / "site_definition.py",
            repository_root / "pyproject.toml",
        ]
        digest = sha256()
        for path in candidates:
            if not path.is_file():
                continue
            try:
                relative_path = path.relative_to(repository_root)
            except ValueError:
                relative_path = Path(path.name)
            digest.update(relative_path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        version = f"source-{digest.hexdigest()}"
    if len(version) > 128:
        return sha256(version.encode("utf-8")).hexdigest()
    return version


@dataclass(frozen=True)
class StructuralAnalysisCacheIdentity:
    tenant_id: UUID
    project_id: UUID
    design_digest: str
    configuration_digest: str
    site_digest: str
    engine_version: str
    snapshot_schema_version: str
    combination_id: str

    @property
    def key_digest(self) -> str:
        return canonical_digest(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
                "design_digest": self.design_digest,
                "configuration_digest": self.configuration_digest,
                "site_digest": self.site_digest,
                "engine_version": self.engine_version,
                "snapshot_schema_version": self.snapshot_schema_version,
                "combination_id": self.combination_id,
            }
        )


def analysis_cache_identity(
    *,
    tenant_id: UUID,
    project_id: UUID,
    design_digest: str,
    configuration_digest: str | None,
    site_definition: object | None,
    combination_id: str | None,
) -> StructuralAnalysisCacheIdentity:
    return StructuralAnalysisCacheIdentity(
        tenant_id=tenant_id,
        project_id=project_id,
        design_digest=design_digest,
        configuration_digest=configuration_digest or EMPTY_INPUT_DIGEST,
        site_digest=(
            canonical_digest(site_definition)
            if site_definition is not None
            else EMPTY_INPUT_DIGEST
        ),
        engine_version=structural_engine_version(),
        snapshot_schema_version=STRUCTURAL_ANALYSIS_CACHE_SCHEMA_VERSION,
        combination_id=combination_id or DEFAULT_COMBINATION_KEY,
    )


def get_cached_structural_analysis(
    db: Session,
    identity: StructuralAnalysisCacheIdentity,
) -> tuple[StructuralAnalysisResult, StructuralSnapshot] | None:
    stored = db.scalar(
        select(StructuralAnalysisResult).where(
            StructuralAnalysisResult.tenant_id == identity.tenant_id,
            StructuralAnalysisResult.project_id == identity.project_id,
            StructuralAnalysisResult.key_digest == identity.key_digest,
        )
    )
    if stored is None:
        return None
    try:
        snapshot = StructuralSnapshot.model_validate(stored.snapshot)
    except ValidationError:
        db.delete(stored)
        db.flush()
        return None
    return stored, snapshot


def acquire_structural_analysis_lock(
    db: Session,
    identity: StructuralAnalysisCacheIdentity,
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    lock_key = int.from_bytes(
        bytes.fromhex(identity.key_digest[:16]),
        byteorder="big",
        signed=True,
    )
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def store_structural_analysis(
    db: Session,
    identity: StructuralAnalysisCacheIdentity,
    snapshot: StructuralSnapshot,
    *,
    calculation_duration_seconds: float,
) -> StructuralAnalysisResult:
    stored = StructuralAnalysisResult(
        tenant_id=identity.tenant_id,
        project_id=identity.project_id,
        key_digest=identity.key_digest,
        design_digest=identity.design_digest,
        configuration_digest=identity.configuration_digest,
        site_digest=identity.site_digest,
        engine_version=identity.engine_version,
        snapshot_schema_version=identity.snapshot_schema_version,
        combination_id=identity.combination_id,
        snapshot=snapshot.model_dump(mode="json"),
        calculation_duration_seconds=calculation_duration_seconds,
    )
    db.add(stored)
    db.flush()
    return stored
