import json

import pytest
from pydantic import ValidationError

from core.project_assets import (
    IMPORT_3MF_CONVERSION_VERSION,
    MAX_3MF_ARCHIVE_ENTRIES,
    MAX_3MF_COORDINATE_MM,
    MAX_3MF_DERIVED_BREP_BYTES,
    MAX_3MF_MANIFEST_BYTES,
    MAX_3MF_OBJECTS,
    MAX_3MF_TRIANGLES,
    MAX_3MF_UNCOMPRESSED_BYTES,
    MAX_3MF_UPLOAD_BYTES,
    MAX_3MF_VERTICES,
    MAX_3MF_XML_BYTES,
    Import3mfManifestSummary,
    Import3mfManifest,
    asset_context_summary,
    generated_3mf_design_source,
    public_manifest_summary,
    safe_part_names,
)


def manifest_payload(**part_overrides):
    part = {
        "index": 0,
        "name": "part_001",
        "source_name": "Original part",
        "shape_type": "solid",
        "boolean_capable": True,
        "is_valid": True,
        "vertex_count": 8,
        "triangle_count": 12,
        "bounds_mm": {"min": [0, 0, 0], "max": [1, 2, 3]},
    }
    part.update(part_overrides)
    return {
        "schema_version": 1,
        "conversion_version": IMPORT_3MF_CONVERSION_VERSION,
        "source_sha256": "a" * 64,
        "brep_sha256": "b" * 64,
        "brep_byte_size": 123,
        "source_unit": "MM",
        "scale_to_mm": 1.0,
        "object_count": 1,
        "total_vertices": part["vertex_count"],
        "total_triangles": part["triangle_count"],
        "warnings": [],
        "parts": [part],
    }


def test_resource_constants_are_centralized_at_exact_values():
    assert MAX_3MF_UPLOAD_BYTES == 128 * 1024 * 1024
    assert MAX_3MF_ARCHIVE_ENTRIES == 2_048
    assert MAX_3MF_UNCOMPRESSED_BYTES == 512 * 1024 * 1024
    assert MAX_3MF_XML_BYTES == 64 * 1024 * 1024
    assert MAX_3MF_OBJECTS == 2_048
    assert MAX_3MF_VERTICES == 10_000_000
    assert MAX_3MF_TRIANGLES == 10_000_000
    assert MAX_3MF_COORDINATE_MM == 1_000_000.0
    assert MAX_3MF_MANIFEST_BYTES == 256 * 1024
    assert MAX_3MF_DERIVED_BREP_BYTES == 512 * 1024 * 1024
    assert IMPORT_3MF_CONVERSION_VERSION == "tertius-3mf-brep-v1-build123d-0.8.0"


def test_import_manifest_rejects_shell_marked_boolean_capable():
    payload = manifest_payload(shape_type="shell", boolean_capable=True)
    with pytest.raises(ValidationError, match="boolean_capable"):
        Import3mfManifest.model_validate(payload)


@pytest.mark.parametrize("digest", ["A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_import_manifest_rejects_invalid_digest(digest):
    payload = manifest_payload()
    payload["source_sha256"] = digest
    with pytest.raises(ValidationError):
        Import3mfManifest.model_validate(payload)


def test_import_manifest_accepts_lowercase_hex_digests():
    manifest = Import3mfManifest.model_validate(manifest_payload())
    assert manifest.source_sha256 == "a" * 64
    assert manifest.brep_sha256 == "b" * 64


def test_import_manifest_requires_contiguous_indices_from_zero():
    payload = manifest_payload(index=1)
    with pytest.raises(ValidationError, match="contiguous"):
        Import3mfManifest.model_validate(payload)


def test_import_manifest_rejects_duplicate_names():
    payload = manifest_payload()
    second = {**payload["parts"][0], "index": 1}
    payload["parts"].append(second)
    payload["object_count"] = 2
    payload["total_vertices"] = 16
    payload["total_triangles"] = 24
    with pytest.raises(ValidationError, match="unique"):
        Import3mfManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("object_count", 2),
        ("total_vertices", 7),
        ("total_triangles", 11),
    ],
)
def test_import_manifest_rejects_inconsistent_counts(field, value):
    payload = manifest_payload()
    payload[field] = value
    with pytest.raises(ValidationError, match="match"):
        Import3mfManifest.model_validate(payload)


@pytest.mark.parametrize(
    ("unit", "scale"),
    [("MC", 0.001), ("MM", 1.0), ("CM", 10.0), ("M", 1000.0), ("IN", 25.4), ("FT", 304.8)],
)
def test_import_manifest_accepts_supported_units_and_exact_scale(unit, scale):
    payload = manifest_payload()
    payload.update(source_unit=unit, scale_to_mm=scale)
    assert Import3mfManifest.model_validate(payload).scale_to_mm == scale


def test_import_manifest_rejects_unsupported_unit_or_wrong_scale():
    with pytest.raises(ValidationError):
        Import3mfManifest.model_validate({**manifest_payload(), "source_unit": "YD"})
    with pytest.raises(ValidationError, match="scale_to_mm"):
        Import3mfManifest.model_validate({**manifest_payload(), "scale_to_mm": 25.4})


def test_part_requires_boolean_capability_exactly_for_valid_solid():
    with pytest.raises(ValidationError, match="boolean_capable"):
        Import3mfManifest.model_validate(manifest_payload(is_valid=False, boolean_capable=True))
    with pytest.raises(ValidationError, match="boolean_capable"):
        Import3mfManifest.model_validate(manifest_payload(is_valid=True, boolean_capable=False))
    manifest = Import3mfManifest.model_validate(
        manifest_payload(shape_type="shell", is_valid=False, boolean_capable=False)
    )
    assert manifest.parts[0].boolean_capable is False


def test_bounds_reject_reversed_non_finite_or_extreme_coordinates():
    for bounds in (
        {"min": [2, 0, 0], "max": [1, 1, 1]},
        {"min": [0, 0, 0], "max": [float("inf"), 1, 1]},
        {"min": [0, 0, 0], "max": [MAX_3MF_COORDINATE_MM + 1, 1, 1]},
    ):
        with pytest.raises(ValidationError):
            Import3mfManifest.model_validate(manifest_payload(bounds_mm=bounds))


def test_import_manifest_rejects_serialization_over_size_limit():
    payload = manifest_payload(source_name="x" * 160)
    payload["warnings"] = ["w" * 240 for _ in range(64)]
    template = payload["parts"][0]
    payload["parts"] = [
        {**template, "index": index, "name": f"part_{index + 1:04d}"}
        for index in range(MAX_3MF_OBJECTS)
    ]
    payload["object_count"] = MAX_3MF_OBJECTS
    payload["total_vertices"] = template["vertex_count"] * MAX_3MF_OBJECTS
    payload["total_triangles"] = template["triangle_count"] * MAX_3MF_OBJECTS
    assert len(json.dumps(payload, separators=(",", ":")).encode()) > MAX_3MF_MANIFEST_BYTES
    with pytest.raises(ValidationError, match="serialized size"):
        Import3mfManifest.model_validate(payload)


def test_safe_part_names_are_unique_deterministic_ascii_identifiers():
    assert safe_part_names(["", "Fin Left", "Fin Left"]) == [
        "part_001",
        "fin_left",
        "fin_left_002",
    ]
    names = safe_part_names(["Étage supérieur", "123 bracket", "a" * 100, "a" * 100])
    assert names[0] == "etage_superieur"
    assert names[1] == "part_123_bracket"
    assert len(names[2]) == 80
    assert names[3].endswith("_002")
    assert len(names[3]) == 80


def test_generated_source_uses_repo_owned_loader_exactly():
    assert generated_3mf_design_source() == (
        "import build123d as bd\n"
        "from tertius_imports import load_3mf_model\n\n"
        'imported = load_3mf_model("source")\n'
        "parts = imported.parts\n"
        "parts_by_name = imported.parts_by_name\n"
        "model = imported.compound\n"
    )


def test_strict_models_and_public_summary_exclude_arbitrary_or_raw_metadata():
    payload = manifest_payload()
    payload["metadata"] = {"secret": "raw"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        Import3mfManifest.model_validate(payload)

    manifest = Import3mfManifest.model_validate(manifest_payload())
    summary = public_manifest_summary(manifest).model_dump()
    assert "source_sha256" not in summary
    assert "brep_sha256" not in summary
    assert "source_name" not in summary["parts"][0]
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(public_manifest_summary(manifest)).model_validate({**summary, "metadata": {}})


def test_validated_manifest_cannot_be_mutated_past_its_invariants():
    manifest = Import3mfManifest.model_validate(manifest_payload())
    with pytest.raises(ValidationError, match="frozen"):
        manifest.object_count = 2
    with pytest.raises(AttributeError):
        manifest.warnings.append("late unvalidated warning")


def test_public_summary_rejects_invalid_part_and_collection_invariants():
    summary = public_manifest_summary(Import3mfManifest.model_validate(manifest_payload())).model_dump()
    summary["parts"][0]["shape_type"] = "shell"
    with pytest.raises(ValidationError, match="boolean_capable"):
        Import3mfManifestSummary.model_validate(summary)

    summary = public_manifest_summary(Import3mfManifest.model_validate(manifest_payload())).model_dump()
    summary["object_count"] = 2
    with pytest.raises(ValidationError, match="object_count"):
        Import3mfManifestSummary.model_validate(summary)


def test_ai_safe_context_summary_excludes_warnings_digests_and_raw_names():
    payload = manifest_payload(source_name="raw metadata")
    payload["warnings"] = ["warning may contain converter detail"]
    context = asset_context_summary(Import3mfManifest.model_validate(payload)).model_dump()
    serialized = json.dumps(context)
    assert "warning may contain converter detail" not in serialized
    assert "raw metadata" not in serialized
    assert "source_sha256" not in serialized
    assert "brep_sha256" not in serialized


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        (lambda payload: payload.update(brep_byte_size=MAX_3MF_DERIVED_BREP_BYTES + 1), "less than"),
        (lambda payload: payload.update(total_vertices=MAX_3MF_VERTICES + 1), "less than"),
        (lambda payload: payload.update(total_triangles=MAX_3MF_TRIANGLES + 1), "less than"),
        (lambda payload: payload["parts"][0].update(source_name="x" * 161), "160"),
        (lambda payload: payload.update(warnings=["w"] * 65), "64"),
        (lambda payload: payload.update(warnings=["w" * 241]), "240"),
    ],
)
def test_manifest_rejects_values_above_security_boundaries(mutation, error_match):
    payload = manifest_payload()
    mutation(payload)
    with pytest.raises(ValidationError, match=error_match):
        Import3mfManifest.model_validate(payload)


def test_manifest_accepts_exact_numeric_and_metadata_boundaries():
    payload = manifest_payload(
        source_name="x" * 160,
        vertex_count=MAX_3MF_VERTICES,
        triangle_count=MAX_3MF_TRIANGLES,
    )
    payload.update(
        brep_byte_size=MAX_3MF_DERIVED_BREP_BYTES,
        total_vertices=MAX_3MF_VERTICES,
        total_triangles=MAX_3MF_TRIANGLES,
        warnings=["w" * 240] * 64,
    )
    manifest = Import3mfManifest.model_validate(payload)
    assert manifest.brep_byte_size == MAX_3MF_DERIVED_BREP_BYTES
    assert len(manifest.warnings) == 64


def test_manifest_rejects_more_than_maximum_parts():
    payload = manifest_payload(vertex_count=0, triangle_count=0)
    template = payload["parts"][0]
    payload["parts"] = [
        {**template, "index": index, "name": f"part_{index + 1:04d}"}
        for index in range(MAX_3MF_OBJECTS + 1)
    ]
    payload["object_count"] = MAX_3MF_OBJECTS
    with pytest.raises(ValidationError, match="2048"):
        Import3mfManifest.model_validate(payload)
