from __future__ import annotations

import json

import pytest

from core.compile_artifacts import (
    compile_bundle_digest,
    decode_compile_artifact,
    encode_compile_artifact,
    validate_compile_bundle,
)


def valid_bundle(*, compiled_digest: str = "d" * 64):
    documents = {
        "compiled_design": {
            "schema_version": "1.0",
            "compiled_design_digest": compiled_digest,
        },
        "procurement": {
            "schema_version": "tertius.procurement.v1",
            "compiled_design_digest": compiled_digest,
        },
        "structural": {
            "schema_version": "tertius.structural.v1",
            "compiled_design_digest": compiled_digest,
        },
        "drawing": {
            "schema_version": "tertius.drawing.v1",
            "compiled_design_digest": compiled_digest,
        },
        "bounds": {
            "schema_version": "tertius.bounds.v1",
            "compiled_design_digest": compiled_digest,
        },
    }
    artifacts = [encode_compile_artifact("stl", b"solid")]
    artifacts.extend(
        encode_compile_artifact(kind, json.dumps(document).encode("utf-8"))
        for kind, document in documents.items()
    )
    return artifacts


def test_compile_bundle_round_trips_and_verifies_every_digest() -> None:
    artifacts = valid_bundle()
    digest = compile_bundle_digest(artifacts)

    decoded = validate_compile_bundle(
        artifacts,
        export_format="stl",
        expected_bundle_digest=digest,
    )

    assert decoded["stl"] == b"solid"
    assert json.loads(decoded["procurement"])["compiled_design_digest"] == "d" * 64


def test_compile_bundle_rejects_missing_projection() -> None:
    artifacts = [item for item in valid_bundle() if item.kind != "structural"]

    with pytest.raises(ValueError, match="missing required kinds.*structural"):
        validate_compile_bundle(
            artifacts,
            export_format="stl",
            expected_bundle_digest=compile_bundle_digest(artifacts),
        )


def test_compile_bundle_rejects_projection_from_another_design() -> None:
    artifacts = valid_bundle()
    replacement = encode_compile_artifact(
        "structural",
        json.dumps(
            {
                "schema_version": "tertius.structural.v1",
                "compiled_design_digest": "e" * 64,
            }
        ).encode("utf-8"),
    )
    artifacts = [replacement if item.kind == "structural" else item for item in artifacts]

    with pytest.raises(ValueError, match="does not reference the compiled-design"):
        validate_compile_bundle(
            artifacts,
            export_format="stl",
            expected_bundle_digest=compile_bundle_digest(artifacts),
        )


def test_compile_artifact_rejects_tampered_content() -> None:
    artifact = encode_compile_artifact("stl", b"solid")
    tampered = artifact.model_copy(update={"content_base64": "dGFtcGVyZWQ="})

    with pytest.raises(ValueError, match="byte size does not match"):
        decode_compile_artifact(tampered)
