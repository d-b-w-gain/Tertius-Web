from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from core.compile_messages import CompileBinaryAsset
from core.models import Artifact
from core.object_store import ObjectRef


@dataclass(frozen=True)
class CompileInputKind:
    artifact_kind: str
    logical_filename: str


COMPILE_INPUT_KINDS = (CompileInputKind("source_3mf", "source.3mf"),)
COMPILE_INPUT_ARTIFACT_KINDS = tuple(
    definition.artifact_kind for definition in COMPILE_INPUT_KINDS
)


class MissingCompileInputError(RuntimeError):
    pass


class CompileInputRepository(Protocol):
    def project_input_artifacts(
        self,
        project_id: UUID,
        artifact_kinds: tuple[str, ...],
    ) -> list[Artifact]: ...

    def job_input_artifacts(
        self,
        job_id: UUID,
        artifact_kinds: tuple[str, ...],
    ) -> list[Artifact]: ...

    def record_artifact(
        self,
        project_id: UUID,
        job_id: UUID | None,
        kind: str,
        content: bytes,
        *,
        content_type: str | None = None,
    ) -> Artifact: ...


def _artifact_kinds(
    definitions: tuple[CompileInputKind, ...],
) -> tuple[str, ...]:
    return tuple(definition.artifact_kind for definition in definitions)


def snapshot_project_compile_inputs(
    repo: CompileInputRepository,
    project_id: UUID,
    job_id: UUID,
    *,
    definitions: tuple[CompileInputKind, ...] = COMPILE_INPUT_KINDS,
) -> list[Artifact]:
    artifact_kinds = _artifact_kinds(definitions)
    project_inputs = {
        artifact.kind: artifact
        for artifact in repo.project_input_artifacts(project_id, artifact_kinds)
    }
    snapshots: list[Artifact] = []
    for definition in definitions:
        source = project_inputs.get(definition.artifact_kind)
        if source is None:
            continue
        if source.content is None:
            raise MissingCompileInputError(
                f"Project compile input {definition.logical_filename} content is missing"
            )
        snapshots.append(
            repo.record_artifact(
                project_id,
                job_id,
                definition.artifact_kind,
                source.content,
                content_type=source.content_type,
            )
        )
    return snapshots


async def materialize_job_binary_assets(
    repo: CompileInputRepository,
    project_id: UUID,
    job_id: UUID,
    store: Callable[[bytes], Awaitable[ObjectRef]],
    *,
    definitions: tuple[CompileInputKind, ...] = COMPILE_INPUT_KINDS,
) -> list[CompileBinaryAsset]:
    artifact_kinds = _artifact_kinds(definitions)
    expected_kinds = {
        artifact.kind
        for artifact in repo.project_input_artifacts(project_id, artifact_kinds)
    }
    if not expected_kinds:
        return []

    job_inputs = {
        artifact.kind: artifact
        for artifact in repo.job_input_artifacts(job_id, artifact_kinds)
    }
    inputs_to_materialize: list[tuple[CompileInputKind, bytes]] = []
    for definition in definitions:
        if definition.artifact_kind not in expected_kinds:
            continue
        snapshot = job_inputs.get(definition.artifact_kind)
        if snapshot is None or snapshot.content is None:
            raise MissingCompileInputError(
                f"Compile input snapshot {definition.logical_filename} is missing"
            )
        inputs_to_materialize.append((definition, snapshot.content))

    assets: list[CompileBinaryAsset] = []
    for definition, content in inputs_to_materialize:
        assets.append(
            CompileBinaryAsset(
                logical_filename=cast(
                    Literal["source.3mf"], definition.logical_filename
                ),
                object_ref=await store(content),
            )
        )
    return assets
