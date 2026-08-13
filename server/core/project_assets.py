from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_3MF_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_3MF_ARCHIVE_ENTRIES = 2_048
MAX_3MF_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_3MF_XML_BYTES = 64 * 1024 * 1024
MAX_3MF_OBJECTS = 2_048
MAX_3MF_VERTICES = 10_000_000
MAX_3MF_TRIANGLES = 10_000_000
MAX_3MF_COORDINATE_MM = 1_000_000.0
MAX_3MF_MANIFEST_BYTES = 256 * 1024
MAX_3MF_DERIVED_BREP_BYTES = 512 * 1024 * 1024
IMPORT_3MF_CONVERSION_VERSION = "tertius-3mf-brep-v1-build123d-0.8.0"
Import3mfConversionVersion = Literal["tertius-3mf-brep-v1-build123d-0.8.0"]
THREE_MF_MEDIA_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
SOURCE_3MF_MEDIA_TYPE = THREE_MF_MEDIA_TYPE
OCTET_STREAM_MEDIA_TYPE = "application/octet-stream"
BREP_MEDIA_TYPE = "application/vnd.opencascade.brep"
DERIVED_BREP_MEDIA_TYPE = BREP_MEDIA_TYPE
MANIFEST_MEDIA_TYPE = "application/json"
IMPORT_MANIFEST_MEDIA_TYPE = MANIFEST_MEDIA_TYPE

MAX_3MF_SOURCE_NAME_CHARS = 160
MAX_3MF_WARNINGS = 64
MAX_3MF_WARNING_CHARS = 240

Import3mfUnit = Literal["MC", "MM", "CM", "M", "IN", "FT"]
Import3mfShapeType = Literal["solid", "shell"]
SafePartName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
WarningText = Annotated[
    str,
    StringConstraints(
        max_length=MAX_3MF_WARNING_CHARS,
        pattern=r"^[^\x00-\x1f\x7f-\x9f]*$",
    ),
]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
CoordinateMm = Annotated[
    float,
    Field(
        strict=True,
        ge=-MAX_3MF_COORDINATE_MM,
        le=MAX_3MF_COORDINATE_MM,
        allow_inf_nan=False,
    ),
]

_UNIT_SCALE_TO_MM: dict[str, float] = {
    "MC": 0.001,
    "MM": 1.0,
    "CM": 10.0,
    "M": 1000.0,
    "IN": 25.4,
    "FT": 304.8,
}
_UNSAFE_PART_NAME = re.compile(r"[^a-z0-9]+")


class StrictAssetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Import3mfBounds(StrictAssetModel):
    min: tuple[CoordinateMm, CoordinateMm, CoordinateMm]
    max: tuple[CoordinateMm, CoordinateMm, CoordinateMm]

    @model_validator(mode="after")
    def validate_axis_order(self):
        if any(minimum > maximum for minimum, maximum in zip(self.min, self.max, strict=True)):
            raise ValueError("bounds min must not exceed max on any axis")
        return self


class Import3mfPart(StrictAssetModel):
    index: StrictNonNegativeInt
    name: SafePartName
    source_name: str = Field(max_length=MAX_3MF_SOURCE_NAME_CHARS)
    shape_type: Import3mfShapeType
    boolean_capable: bool = Field(strict=True)
    is_valid: bool = Field(strict=True)
    vertex_count: StrictNonNegativeInt = Field(le=MAX_3MF_VERTICES)
    triangle_count: StrictNonNegativeInt = Field(le=MAX_3MF_TRIANGLES)
    bounds_mm: Import3mfBounds

    @model_validator(mode="after")
    def solid_boolean_invariant(self):
        if self.boolean_capable != (self.shape_type == "solid" and self.is_valid):
            raise ValueError("boolean_capable must match valid solid status")
        return self


class Import3mfManifest(StrictAssetModel):
    schema_version: Literal[1]
    conversion_version: Import3mfConversionVersion
    source_sha256: Sha256Digest
    brep_sha256: Sha256Digest
    brep_byte_size: int = Field(strict=True, ge=1, le=MAX_3MF_DERIVED_BREP_BYTES)
    source_unit: Import3mfUnit
    scale_to_mm: float = Field(strict=True, gt=0, allow_inf_nan=False)
    object_count: int = Field(strict=True, ge=1, le=MAX_3MF_OBJECTS)
    total_vertices: StrictNonNegativeInt = Field(le=MAX_3MF_VERTICES)
    total_triangles: StrictNonNegativeInt = Field(le=MAX_3MF_TRIANGLES)
    warnings: tuple[WarningText, ...] = Field(default_factory=tuple, max_length=MAX_3MF_WARNINGS)
    parts: tuple[Import3mfPart, ...] = Field(min_length=1, max_length=MAX_3MF_OBJECTS)

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_schema_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be the integer 1")
        return value

    @model_validator(mode="after")
    def validate_manifest_invariants(self):
        if self.scale_to_mm != _UNIT_SCALE_TO_MM[self.source_unit]:
            raise ValueError("scale_to_mm must match source_unit")
        if self.object_count != len(self.parts):
            raise ValueError("object_count must match parts length")
        if [part.index for part in self.parts] != list(range(len(self.parts))):
            raise ValueError("part indices must be contiguous from zero")
        names = [part.name for part in self.parts]
        if len(names) != len(set(names)):
            raise ValueError("part names must be unique")
        if self.total_vertices != sum(part.vertex_count for part in self.parts):
            raise ValueError("total_vertices must match part vertex counts")
        if self.total_triangles != sum(part.triangle_count for part in self.parts):
            raise ValueError("total_triangles must match part triangle counts")
        if len(self.model_dump_json().encode("utf-8")) > MAX_3MF_MANIFEST_BYTES:
            raise ValueError("manifest serialized size exceeds limit")
        return self


class Import3mfPartSummary(StrictAssetModel):
    index: StrictNonNegativeInt
    name: SafePartName
    shape_type: Import3mfShapeType
    boolean_capable: bool = Field(strict=True)
    is_valid: bool = Field(strict=True)
    bounds_mm: Import3mfBounds

    @model_validator(mode="after")
    def solid_boolean_invariant(self):
        if self.boolean_capable != (self.shape_type == "solid" and self.is_valid):
            raise ValueError("boolean_capable must match valid solid status")
        return self


class Import3mfManifestSummary(StrictAssetModel):
    conversion_version: Import3mfConversionVersion
    source_unit: Import3mfUnit
    scale_to_mm: float = Field(strict=True, gt=0, allow_inf_nan=False)
    object_count: int = Field(strict=True, ge=1, le=MAX_3MF_OBJECTS)
    total_vertices: StrictNonNegativeInt = Field(le=MAX_3MF_VERTICES)
    total_triangles: StrictNonNegativeInt = Field(le=MAX_3MF_TRIANGLES)
    warnings: tuple[WarningText, ...] = Field(default_factory=tuple, max_length=MAX_3MF_WARNINGS)
    parts: tuple[Import3mfPartSummary, ...] = Field(min_length=1, max_length=MAX_3MF_OBJECTS)

    @model_validator(mode="after")
    def validate_summary_invariants(self):
        if self.scale_to_mm != _UNIT_SCALE_TO_MM[self.source_unit]:
            raise ValueError("scale_to_mm must match source_unit")
        if self.object_count != len(self.parts):
            raise ValueError("object_count must match parts length")
        if [part.index for part in self.parts] != list(range(len(self.parts))):
            raise ValueError("part indices must be contiguous from zero")
        if len({part.name for part in self.parts}) != len(self.parts):
            raise ValueError("part names must be unique")
        return self


class Import3mfAssetContextSummary(StrictAssetModel):
    """Manifest subset safe to use when building external-provider context."""

    conversion_version: Import3mfConversionVersion
    source_unit: Import3mfUnit
    scale_to_mm: float = Field(strict=True, gt=0, allow_inf_nan=False)
    parts: tuple[Import3mfPartSummary, ...] = Field(min_length=1, max_length=MAX_3MF_OBJECTS)

    @model_validator(mode="after")
    def validate_context_invariants(self):
        if self.scale_to_mm != _UNIT_SCALE_TO_MM[self.source_unit]:
            raise ValueError("scale_to_mm must match source_unit")
        if [part.index for part in self.parts] != list(range(len(self.parts))):
            raise ValueError("part indices must be contiguous from zero")
        if len({part.name for part in self.parts}) != len(self.parts):
            raise ValueError("part names must be unique")
        return self


def public_manifest_summary(manifest: Import3mfManifest) -> Import3mfManifestSummary:
    """Return bounded geometry metadata for public status APIs."""
    return Import3mfManifestSummary(
        conversion_version=manifest.conversion_version,
        source_unit=manifest.source_unit,
        scale_to_mm=manifest.scale_to_mm,
        object_count=manifest.object_count,
        total_vertices=manifest.total_vertices,
        total_triangles=manifest.total_triangles,
        warnings=manifest.warnings,
        parts=tuple(
            Import3mfPartSummary(
                index=part.index,
                name=part.name,
                shape_type=part.shape_type,
                boolean_capable=part.boolean_capable,
                is_valid=part.is_valid,
                bounds_mm=part.bounds_mm,
            )
            for part in manifest.parts
        ),
    )


def asset_context_summary(manifest: Import3mfManifest) -> Import3mfAssetContextSummary:
    """Return only metadata approved for an imported-asset AI context builder."""
    public = public_manifest_summary(manifest)
    return Import3mfAssetContextSummary(
        conversion_version=public.conversion_version,
        source_unit=public.source_unit,
        scale_to_mm=public.scale_to_mm,
        parts=public.parts,
    )


def safe_part_names(source_names: list[str]) -> list[str]:
    """Normalize source labels into stable, unique Build123D/Python-safe names."""
    used: set[str] = set()
    result: list[str] = []

    for index, source_name in enumerate(source_names):
        ascii_name = unicodedata.normalize("NFKD", source_name).encode("ascii", "ignore").decode()
        base = _UNSAFE_PART_NAME.sub("_", ascii_name.lower()).strip("_")
        if not base:
            base = f"part_{index + 1:03d}"
        elif not base[0].isalpha():
            base = f"part_{base}"
        base = base[:80].rstrip("_")

        candidate = base
        suffix_number = 2
        while candidate in used:
            suffix = f"_{suffix_number:03d}"
            candidate = f"{base[: 80 - len(suffix)].rstrip('_')}{suffix}"
            suffix_number += 1
        used.add(candidate)
        result.append(candidate)

    return result


def generated_3mf_design_source() -> str:
    return (
        "import build123d as bd\n"
        "from tertius_imports import load_3mf_model\n\n"
        'imported = load_3mf_model("source")\n'
        "parts = imported.parts\n"
        "parts_by_name = imported.parts_by_name\n"
        "model = imported.compound\n"
    )
