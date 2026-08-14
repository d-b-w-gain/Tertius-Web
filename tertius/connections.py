from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Literal, Sequence

import build123d as bd

from ._canonical import canonical_digest, required_text
from .components import ComponentPort


TransferKind = Literal["force", "shear", "moment", "wind_normal"]
AnalysisModel = Literal["pinned", "rigid", "rigid_zone", "semi_rigid", "spring"]
StiffnessStatus = Literal["unverified", "candidate", "verified"]
ResistanceStatus = Literal["unverified", "candidate", "verified"]


@dataclass(frozen=True)
class ConnectionResistanceDefinition:
    pack_id: str
    version: str
    status: ResistanceStatus
    basis: str
    connector_part_numbers: tuple[str, ...]
    source: str | None = None
    source_sha256: str | None = None
    design_axial_capacity_kN: float | None = None
    design_shear_capacity_kN: float | None = None
    design_moment_capacity_kNm: float | None = None
    assumptions: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        pack_id: str,
        version: str,
        status: ResistanceStatus,
        basis: str,
        connector_part_numbers: Iterable[str],
        source: str | None = None,
        source_sha256: str | None = None,
        design_axial_capacity_kN: float | None = None,
        design_shear_capacity_kN: float | None = None,
        design_moment_capacity_kNm: float | None = None,
        assumptions: Iterable[str] = (),
    ) -> None:
        object.__setattr__(self, "pack_id", required_text("resistance pack ID", pack_id))
        object.__setattr__(self, "version", required_text("resistance pack version", version))
        if status not in {"unverified", "candidate", "verified"}:
            raise ValueError(f"unsupported connection resistance status {status!r}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "basis", required_text("resistance basis", basis))
        normalized_parts = tuple(
            required_text("connector part number", part_number)
            for part_number in connector_part_numbers
        )
        if not normalized_parts:
            raise ValueError("connection resistance requires exact connector identities")
        object.__setattr__(self, "connector_part_numbers", normalized_parts)
        object.__setattr__(
            self,
            "source",
            required_text("resistance source", source) if source is not None else None,
        )
        normalized_sha = source_sha256.lower() if source_sha256 is not None else None
        if normalized_sha is not None and (
            len(normalized_sha) != 64
            or any(character not in "0123456789abcdef" for character in normalized_sha)
        ):
            raise ValueError("connection resistance source SHA-256 must be hexadecimal")
        object.__setattr__(self, "source_sha256", normalized_sha)
        for field_name, value in (
            ("design_axial_capacity_kN", design_axial_capacity_kN),
            ("design_shear_capacity_kN", design_shear_capacity_kN),
            ("design_moment_capacity_kNm", design_moment_capacity_kNm),
        ):
            normalized = None if value is None else float(value)
            if normalized is not None and (not isfinite(normalized) or normalized <= 0):
                raise ValueError(f"{field_name} must be finite and positive")
            object.__setattr__(self, field_name, normalized)
        if status == "verified" and (source is None or normalized_sha is None):
            raise ValueError("verified connection resistance requires a hashed source")
        object.__setattr__(
            self,
            "assumptions",
            tuple(required_text("resistance assumption", item) for item in assumptions),
        )

    def payload(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "status": self.status,
            "basis": self.basis,
            "connector_part_numbers": list(self.connector_part_numbers),
            "source": self.source,
            "source_sha256": self.source_sha256,
            "design_axial_capacity_kN": self.design_axial_capacity_kN,
            "design_shear_capacity_kN": self.design_shear_capacity_kN,
            "design_moment_capacity_kNm": self.design_moment_capacity_kNm,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class ConnectionDefinition:
    key: str
    label: str
    family: str
    transfers: tuple[TransferKind, ...]
    analysis_model: AnalysisModel
    stiffness_status: StiffnessStatus = "unverified"
    stiffness_basis: str = "Connection stiffness has not been verified."
    maximum_port_offset_mm: float = 1.0
    resistance: ConnectionResistanceDefinition | None = None

    def __init__(
        self,
        *,
        key: str,
        label: str,
        family: str,
        transfers: Iterable[TransferKind],
        analysis_model: AnalysisModel,
        stiffness_status: StiffnessStatus = "unverified",
        stiffness_basis: str = "Connection stiffness has not been verified.",
        maximum_port_offset_mm: float = 1.0,
        resistance: ConnectionResistanceDefinition | None = None,
    ) -> None:
        object.__setattr__(self, "key", required_text("connection key", key))
        object.__setattr__(self, "label", required_text("connection label", label))
        object.__setattr__(self, "family", required_text("connection family", family))
        normalized = tuple(dict.fromkeys(str(item) for item in transfers))
        allowed = {"force", "shear", "moment", "wind_normal"}
        if not normalized or set(normalized) - allowed:
            raise ValueError("connection transfers must use supported action names")
        object.__setattr__(self, "transfers", normalized)
        if analysis_model not in {"pinned", "rigid", "rigid_zone", "semi_rigid", "spring"}:
            raise ValueError(f"unsupported connection analysis model {analysis_model!r}")
        object.__setattr__(self, "analysis_model", analysis_model)
        if stiffness_status not in {"unverified", "candidate", "verified"}:
            raise ValueError(f"unsupported connection stiffness status {stiffness_status!r}")
        object.__setattr__(self, "stiffness_status", stiffness_status)
        object.__setattr__(
            self,
            "stiffness_basis",
            required_text("connection stiffness basis", stiffness_basis),
        )
        maximum_offset = float(maximum_port_offset_mm)
        if not isfinite(maximum_offset) or maximum_offset < 0.0:
            raise ValueError("maximum port offset must be finite and non-negative")
        object.__setattr__(self, "maximum_port_offset_mm", maximum_offset)
        if resistance is not None and not isinstance(
            resistance, ConnectionResistanceDefinition
        ):
            raise TypeError("connection resistance must be a resistance definition")
        if resistance is not None and resistance.status == "verified":
            required_capacities = {
                "force": resistance.design_axial_capacity_kN,
                "shear": resistance.design_shear_capacity_kN,
                "moment": resistance.design_moment_capacity_kNm,
            }
            missing = [
                transfer
                for transfer, capacity in required_capacities.items()
                if transfer in normalized and capacity is None
            ]
            if missing:
                raise ValueError(
                    "verified connection resistance is missing capacities for "
                    + ", ".join(missing)
                )
        object.__setattr__(self, "resistance", resistance)

    @property
    def definition_digest(self) -> str:
        return canonical_digest(self.payload(include_digest=False))

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "key": self.key,
            "label": self.label,
            "family": self.family,
            "transfers": list(self.transfers),
            "analysis_model": self.analysis_model,
            "stiffness_status": self.stiffness_status,
            "stiffness_basis": self.stiffness_basis,
            "maximum_port_offset_mm": self.maximum_port_offset_mm,
            "resistance": self.resistance.payload() if self.resistance else None,
        }
        if include_digest:
            payload["definition_digest"] = self.definition_digest
        return payload


@dataclass(frozen=True)
class ConnectionRegistration:
    token: str
    connection_id: str
    shape: bd.Shape
    definition: ConnectionDefinition
    ports: tuple[ComponentPort, ...]
    connector_component_tokens: tuple[str, ...]
    mark: str | None


def physical_connection(
    shape: bd.Shape,
    *,
    definition: ConnectionDefinition,
    ports: Sequence[ComponentPort],
    connector_components: Sequence[bd.Shape],
    mark: str | None = None,
) -> bd.Shape:
    """Register one real connection and return its rendered assembly shape."""

    from .session import current_session

    if not isinstance(shape, bd.Shape):
        raise TypeError("physical_connection requires a Build123D Shape")
    if not isinstance(definition, ConnectionDefinition):
        raise TypeError("physical_connection requires a ConnectionDefinition")
    return current_session().register_connection(
        shape,
        definition=definition,
        ports=ports,
        connector_components=connector_components,
        mark=mark,
    )
