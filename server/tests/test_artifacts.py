from uuid import uuid4

from core.artifacts import artifact_storage_key, content_type_for_kind


def test_content_type_for_known_artifact_kinds():
    assert content_type_for_kind("stl") == "application/octet-stream"
    assert content_type_for_kind("step") == "application/step"
    assert content_type_for_kind("gltf") == "model/gltf+json"
    assert content_type_for_kind("glb") == "model/gltf-binary"
    assert content_type_for_kind("timus_views") == "application/json"


def test_content_type_for_unknown_kind_defaults_to_octet_stream():
    assert content_type_for_kind("custom") == "application/octet-stream"


def test_artifact_storage_key_is_tenant_and_project_scoped():
    tenant_id = uuid4()
    project_id = uuid4()

    key = artifact_storage_key(tenant_id, project_id, "STL")

    assert key.startswith(f"{tenant_id}/{project_id}/")
    assert key.endswith(".stl")
