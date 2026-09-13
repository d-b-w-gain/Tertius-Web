from __future__ import annotations

import base64
import gzip
from hashlib import sha256
import json
from typing import Iterable

from .compile_messages import CompileArtifactPayload


MAX_ARTIFACT_COUNT = 8
MAX_DECOMPRESSED_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_DECOMPRESSED_BUNDLE_BYTES = 320 * 1024 * 1024
WORKBENCH_ARTIFACT_KINDS = frozenset(
    {"compiled_design", "procurement", "structural", "drawing", "bounds"}
)
JSON_SCHEMAS = {
    "compiled_design": "1.0",
    "procurement": "tertius.procurement.v1",
    "structural": "tertius.structural.v1",
    "drawing": "tertius.drawing.v1",
    "bounds": "tertius.bounds.v1",
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_type_for_compile_artifact(kind: str) -> str:
    return {
        "stl": "application/octet-stream",
        "step": "application/step",
        "gltf": "model/gltf+json",
        "glb": "model/gltf-binary",
    }.get(kind, "application/json")


def encode_compile_artifact(
    kind: str,
    content: bytes,
    *,
    content_type: str | None = None,
) -> CompileArtifactPayload:
    compressed = gzip.compress(content)
    use_compression = len(compressed) < len(content)
    transported = compressed if use_compression else content
    return CompileArtifactPayload(
        kind=kind,
        content_type=content_type or content_type_for_compile_artifact(kind),
        content_base64=base64.b64encode(transported).decode("ascii"),
        byte_size=len(content),
        sha256=sha256(content).hexdigest(),
        is_compressed=use_compression,
    )


def decode_compile_artifact(artifact: CompileArtifactPayload) -> bytes:
    try:
        transported = base64.b64decode(
            artifact.content_base64.encode("ascii"),
            validate=True,
        )
        content = gzip.decompress(transported) if artifact.is_compressed else transported
    except (OSError, ValueError) as exc:
        raise ValueError(f"artifact {artifact.kind!r} content is invalid") from exc
    if len(content) != artifact.byte_size:
        raise ValueError(f"artifact {artifact.kind!r} byte size does not match content")
    if len(content) > MAX_DECOMPRESSED_ARTIFACT_BYTES:
        raise ValueError(
            f"artifact {artifact.kind!r} exceeds the decompressed size limit"
        )
    if sha256(content).hexdigest() != artifact.sha256:
        raise ValueError(f"artifact {artifact.kind!r} digest does not match content")
    return content


def compile_bundle_digest(artifacts: Iterable[CompileArtifactPayload]) -> str:
    manifest = [
        {
            "kind": artifact.kind,
            "content_type": artifact.content_type,
            "byte_size": artifact.byte_size,
            "sha256": artifact.sha256,
        }
        for artifact in sorted(artifacts, key=lambda item: item.kind)
    ]
    return sha256(_canonical_json_bytes(manifest)).hexdigest()


def validate_compile_bundle(
    artifacts: list[CompileArtifactPayload],
    *,
    export_format: str,
    expected_bundle_digest: str | None,
) -> dict[str, bytes]:
    if not artifacts:
        raise ValueError("succeeded compile result did not include an artifact bundle")
    if len(artifacts) > MAX_ARTIFACT_COUNT:
        raise ValueError("compile artifact bundle contains too many artifacts")
    kinds = [artifact.kind for artifact in artifacts]
    if len(kinds) != len(set(kinds)):
        raise ValueError("compile artifact bundle contains duplicate artifact kinds")
    required = set(WORKBENCH_ARTIFACT_KINDS) | {export_format}
    missing = sorted(required - set(kinds))
    if missing:
        raise ValueError(f"compile artifact bundle is missing required kinds: {missing}")
    actual_bundle_digest = compile_bundle_digest(artifacts)
    if expected_bundle_digest != actual_bundle_digest:
        raise ValueError("compile artifact bundle digest does not match its manifest")

    decoded = {artifact.kind: decode_compile_artifact(artifact) for artifact in artifacts}
    if sum(len(content) for content in decoded.values()) > MAX_DECOMPRESSED_BUNDLE_BYTES:
        raise ValueError("compile artifact bundle exceeds the decompressed size limit")
    _validate_workbench_cross_links(decoded)
    return decoded


def _validate_workbench_cross_links(decoded: dict[str, bytes]) -> None:
    documents: dict[str, dict] = {}
    for kind, schema in JSON_SCHEMAS.items():
        try:
            document = json.loads(decoded[kind])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"artifact {kind!r} is not valid JSON") from exc
        if not isinstance(document, dict):
            raise ValueError(f"artifact {kind!r} root must be a JSON object")
        if document.get("schema_version") != schema:
            raise ValueError(f"artifact {kind!r} has an unsupported schema version")
        documents[kind] = document

    compiled_digest = str(documents["compiled_design"].get("compiled_design_digest") or "")
    if len(compiled_digest) != 64:
        raise ValueError("compiled-design artifact is missing its canonical digest")
    for kind in WORKBENCH_ARTIFACT_KINDS - {"compiled_design"}:
        if documents[kind].get("compiled_design_digest") != compiled_digest:
            raise ValueError(
                f"artifact {kind!r} does not reference the compiled-design digest"
            )
