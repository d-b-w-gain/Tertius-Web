from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from ._canonical import canonical_digest, freeze_json, required_text, thaw_json


ProductClassification = Literal["orderable", "reference"]
StructuralKind = Literal["ground", "member", "surface", "connector", "support"]
EvidenceStatus = Literal["unverified", "candidate", "verified"]


@dataclass(frozen=True)
class ProcurementFacet:
    part_number: str
    unit: str = "each"
    manufacturer: str | None = None
    material: str | None = None
    finish: str | None = None
    standard: str | None = None
    ordering: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "part_number", required_text("part number", self.part_number))
        object.__setattr__(self, "unit", required_text("procurement unit", self.unit))
        for name in ("manufacturer", "material", "finish", "standard"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, required_text(name, value))
        object.__setattr__(
            self,
            "ordering",
            freeze_json(self.ordering, label="procurement ordering"),
        )

    def payload(self) -> dict[str, Any]:
        return {
            "part_number": self.part_number,
            "unit": self.unit,
            "manufacturer": self.manufacturer,
            "material": self.material,
            "finish": self.finish,
            "standard": self.standard,
            "ordering": thaw_json(self.ordering),
        }


@dataclass(frozen=True)
class StructuralFacet:
    kind: StructuralKind
    material: Mapping[str, Any] = field(default_factory=dict)
    section: Mapping[str, Any] = field(default_factory=dict)
    properties: Mapping[str, Any] = field(default_factory=dict)
    evidence_status: EvidenceStatus = "unverified"
    evidence_basis: str = "Structural product evidence has not been verified."

    def __post_init__(self) -> None:
        if self.kind not in {"ground", "member", "surface", "connector", "support"}:
            raise ValueError(f"unsupported structural kind {self.kind!r}")
        if self.evidence_status not in {"unverified", "candidate", "verified"}:
            raise ValueError(f"unsupported structural evidence status {self.evidence_status!r}")
        object.__setattr__(
            self,
            "material",
            freeze_json(self.material, label="structural material"),
        )
        object.__setattr__(
            self,
            "section",
            freeze_json(self.section, label="structural section"),
        )
        object.__setattr__(
            self,
            "properties",
            freeze_json(self.properties, label="structural properties"),
        )
        object.__setattr__(
            self,
            "evidence_basis",
            required_text("structural evidence basis", self.evidence_basis),
        )
        if self.kind == "member" and not self.section:
            raise ValueError("structural member products require section properties")

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "material": thaw_json(self.material),
            "section": thaw_json(self.section),
            "properties": thaw_json(self.properties),
            "evidence_status": self.evidence_status,
            "evidence_basis": self.evidence_basis,
        }


@dataclass(frozen=True)
class DrawingFacet:
    name: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", required_text("drawing name", self.name))
        object.__setattr__(
            self,
            "attributes",
            freeze_json(self.attributes, label="drawing attributes"),
        )

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "attributes": thaw_json(self.attributes)}


@dataclass(frozen=True)
class ProductDefinition:
    key: str
    label: str
    geometry: Mapping[str, Any]
    classification: ProductClassification = "orderable"
    catalogue_id: str | None = None
    catalogue_revision: str | None = None
    catalogue_row: Mapping[str, Any] = field(default_factory=dict)
    procurement: ProcurementFacet | None = None
    structural: StructuralFacet | None = None
    drawing: DrawingFacet | None = None
    port_families: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", required_text("product key", self.key))
        object.__setattr__(self, "label", required_text("product label", self.label))
        if self.classification not in {"orderable", "reference"}:
            raise ValueError(f"unsupported product classification {self.classification!r}")
        if (self.catalogue_id is None) != (self.catalogue_revision is None):
            raise ValueError("catalogue ID and revision must be supplied together")
        if self.catalogue_id is not None:
            object.__setattr__(
                self,
                "catalogue_id",
                required_text("catalogue ID", self.catalogue_id),
            )
            object.__setattr__(
                self,
                "catalogue_revision",
                required_text("catalogue revision", self.catalogue_revision),
            )
        object.__setattr__(self, "geometry", freeze_json(self.geometry, label="product geometry"))
        object.__setattr__(
            self,
            "catalogue_row",
            freeze_json(self.catalogue_row, label="catalogue row"),
        )
        object.__setattr__(
            self,
            "port_families",
            freeze_json(self.port_families, label="product port families"),
        )
        if self.classification == "orderable" and self.procurement is None:
            raise ValueError("orderable products require procurement identity")

    @property
    def catalogue_row_digest(self) -> str | None:
        if self.catalogue_id is None:
            return None
        return canonical_digest(self.catalogue_row)

    @property
    def definition_digest(self) -> str:
        return canonical_digest(self.payload(include_digest=False))

    def port_family_names(self, port_name: str) -> tuple[str, ...]:
        raw = self.port_families.get(
            port_name,
            self.port_families.get("*", ()),
        )
        if isinstance(raw, str):
            return (raw,)
        return tuple(str(item) for item in raw)

    def payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "classification": self.classification,
            "catalogue": (
                {
                    "id": self.catalogue_id,
                    "revision": self.catalogue_revision,
                    "row_digest": self.catalogue_row_digest,
                    "row": thaw_json(self.catalogue_row),
                }
                if self.catalogue_id is not None
                else None
            ),
            "geometry": thaw_json(self.geometry),
            "procurement": self.procurement.payload() if self.procurement else None,
            "structural": self.structural.payload() if self.structural else None,
            "drawing": self.drawing.payload() if self.drawing else None,
            "port_families": thaw_json(self.port_families),
        }
        if include_digest:
            payload["definition_digest"] = self.definition_digest
        return payload
