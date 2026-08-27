import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.compile_inputs import (
    COMPILE_INPUT_KINDS,
    CompileInputKind,
    MissingCompileInputError,
    materialize_job_binary_assets,
    snapshot_project_compile_inputs,
)
from core.object_store import ObjectRef


class FakeCompileInputRepository:
    def __init__(self, project_inputs=None):
        self.project_inputs = list(project_inputs or [])
        self.job_inputs = []
        self.recorded = []
        self.project_input_kind_calls = []
        self.job_input_artifact_calls = []

    def project_input_artifacts(self, project_id, artifact_kinds):
        return [
            artifact
            for artifact in self.project_inputs
            if artifact.project_id == project_id
            and artifact.compile_job_id is None
            and artifact.kind in artifact_kinds
        ]

    def project_input_kinds(self, project_id, artifact_kinds):
        self.project_input_kind_calls.append((project_id, artifact_kinds))
        return {
            artifact.kind
            for artifact in self.project_inputs
            if artifact.project_id == project_id
            and artifact.compile_job_id is None
            and artifact.kind in artifact_kinds
        }

    def job_input_artifacts(self, job_id, artifact_kinds):
        self.job_input_artifact_calls.append((job_id, artifact_kinds))
        return [
            artifact
            for artifact in self.job_inputs
            if artifact.compile_job_id == job_id and artifact.kind in artifact_kinds
        ]

    def record_artifact(
        self,
        project_id,
        job_id,
        kind,
        content,
        *,
        content_type=None,
    ):
        artifact = SimpleNamespace(
            id=uuid4(),
            project_id=project_id,
            compile_job_id=job_id,
            kind=kind,
            content=content,
            content_type=content_type,
        )
        self.job_inputs.append(artifact)
        self.recorded.append(artifact)
        return artifact


def input_artifact(project_id, kind, content, *, content_type="application/octet-stream"):
    return SimpleNamespace(
        id=uuid4(),
        project_id=project_id,
        compile_job_id=None,
        kind=kind,
        content=content,
        content_type=content_type,
    )


def job_input_artifact(
    project_id,
    job_id,
    kind,
    content,
    *,
    content_type="application/octet-stream",
):
    return SimpleNamespace(
        id=uuid4(),
        project_id=project_id,
        compile_job_id=job_id,
        kind=kind,
        content=content,
        content_type=content_type,
    )


def stored_object(content):
    return ObjectRef(
        bucket="TERTIUS_COMPILE_SIDECARS",
        key=f"sha256/{'a' * 64}",
        sha256="a" * 64,
        byte_size=len(content),
    )


def test_compile_input_kinds_only_maps_source_3mf_to_source_filename():
    assert COMPILE_INPUT_KINDS == (
        CompileInputKind("source_3mf", "source.3mf"),
    )


def test_no_durable_inputs_create_no_snapshots_assets_or_uploads():
    project_id = uuid4()
    job_id = uuid4()
    repo = FakeCompileInputRepository()
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    snapshots = snapshot_project_compile_inputs(repo, project_id, job_id)
    assets = asyncio.run(
        materialize_job_binary_assets(repo, project_id, job_id, upload)
    )

    assert snapshots == []
    assert assets == []
    assert repo.recorded == []
    assert uploaded == []


def test_materialization_uploads_pinned_job_input_after_durable_input_changes():
    project_id = uuid4()
    job_id = uuid4()
    durable = input_artifact(
        project_id,
        "source_3mf",
        b"durable-a",
        content_type="model/3mf",
    )
    repo = FakeCompileInputRepository([durable])

    snapshots = snapshot_project_compile_inputs(repo, project_id, job_id)
    durable.content = b"durable-b"
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    assets = asyncio.run(
        materialize_job_binary_assets(repo, project_id, job_id, upload)
    )

    assert [(snapshot.kind, snapshot.content) for snapshot in snapshots] == [
        ("source_3mf", b"durable-a")
    ]
    assert uploaded == [b"durable-a"]
    assert repo.job_input_artifact_calls == [(job_id, ("source_3mf",))]
    assert [asset.logical_filename for asset in assets] == ["source.3mf"]


def test_materialization_uses_preloaded_job_inputs_without_repository_reread():
    project_id = uuid4()
    job_id = uuid4()
    repo = FakeCompileInputRepository(
        [input_artifact(project_id, "source_3mf", b"durable-input")]
    )
    preloaded_snapshots = [
        job_input_artifact(project_id, job_id, "source_3mf", b"preloaded-snapshot"),
        job_input_artifact(
            project_id,
            uuid4(),
            "source_3mf",
            b"other-job-snapshot",
        ),
        job_input_artifact(project_id, job_id, "ignored_kind", b"ignored-snapshot"),
    ]

    def reject_job_input_artifact_read(requested_job_id, artifact_kinds):
        repo.job_input_artifact_calls.append((requested_job_id, artifact_kinds))
        raise AssertionError("materialization reloaded job input bytes")

    repo.job_input_artifacts = reject_job_input_artifact_read
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    assets = asyncio.run(
        materialize_job_binary_assets(
            repo,
            project_id,
            job_id,
            upload,
            job_inputs=preloaded_snapshots,
        )
    )

    assert repo.job_input_artifact_calls == []
    assert uploaded == [b"preloaded-snapshot"]
    assert [asset.logical_filename for asset in assets] == ["source.3mf"]


def test_materialization_reads_durable_input_kinds_without_loading_project_bytes():
    project_id = uuid4()
    job_id = uuid4()
    repo = FakeCompileInputRepository(
        [input_artifact(project_id, "source_3mf", b"durable-input")]
    )
    repo.job_inputs.append(
        job_input_artifact(project_id, job_id, "source_3mf", b"job-snapshot")
    )

    def reject_project_input_artifact_read(project_id, artifact_kinds):
        raise AssertionError("materialization loaded durable project bytes")

    repo.project_input_artifacts = reject_project_input_artifact_read
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    assets = asyncio.run(
        materialize_job_binary_assets(repo, project_id, job_id, upload)
    )

    assert repo.project_input_kind_calls == [
        (project_id, ("source_3mf",))
    ]
    assert uploaded == [b"job-snapshot"]
    assert [asset.logical_filename for asset in assets] == ["source.3mf"]


def test_snapshot_rejects_durable_input_without_content_before_upload():
    project_id = uuid4()
    job_id = uuid4()
    repo = FakeCompileInputRepository(
        [input_artifact(project_id, "source_3mf", None)]
    )
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    async def prepare_binary_inputs():
        snapshot_project_compile_inputs(repo, project_id, job_id)
        return await materialize_job_binary_assets(
            repo, project_id, job_id, upload
        )

    with pytest.raises(MissingCompileInputError, match="source.3mf"):
        asyncio.run(prepare_binary_inputs())

    assert repo.recorded == []
    assert uploaded == []


def test_snapshot_iterates_over_multiple_compile_input_definitions():
    project_id = uuid4()
    job_id = uuid4()
    definitions = (
        CompileInputKind("test_input_a", "a.bin"),
        CompileInputKind("test_input_b", "b.bin"),
    )
    repo = FakeCompileInputRepository(
        [
            input_artifact(project_id, "test_input_a", b"input-a"),
            input_artifact(project_id, "test_input_b", b"input-b"),
        ]
    )

    snapshots = snapshot_project_compile_inputs(
        repo,
        project_id,
        job_id,
        definitions=definitions,
    )

    assert [(snapshot.kind, snapshot.content) for snapshot in snapshots] == [
        ("test_input_a", b"input-a"),
        ("test_input_b", b"input-b"),
    ]
    assert all(snapshot.compile_job_id == job_id for snapshot in snapshots)


def test_materialization_iterates_over_test_only_compile_input_definition():
    project_id = uuid4()
    job_id = uuid4()
    definitions = (
        CompileInputKind("test_custom_input", "source.3mf"),
    )
    repo = FakeCompileInputRepository(
        [input_artifact(project_id, "test_custom_input", b"custom-input")]
    )
    snapshot_project_compile_inputs(
        repo,
        project_id,
        job_id,
        definitions=definitions,
    )
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    assets = asyncio.run(
        materialize_job_binary_assets(
            repo,
            project_id,
            job_id,
            upload,
            definitions=definitions,
        )
    )

    assert uploaded == [b"custom-input"]
    assert [asset.logical_filename for asset in assets] == ["source.3mf"]


def test_materialization_rejects_job_input_without_content_before_upload():
    project_id = uuid4()
    job_id = uuid4()
    repo = FakeCompileInputRepository(
        [input_artifact(project_id, "source_3mf", b"durable-input")]
    )
    repo.job_inputs.append(
        job_input_artifact(project_id, job_id, "source_3mf", None)
    )
    uploaded = []

    async def upload(content):
        uploaded.append(content)
        return stored_object(content)

    with pytest.raises(MissingCompileInputError, match="source.3mf"):
        asyncio.run(
            materialize_job_binary_assets(
                repo,
                project_id,
                job_id,
                upload,
            )
        )

    assert uploaded == []
