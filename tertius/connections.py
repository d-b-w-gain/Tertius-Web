from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence

import build123d as bd

from ._canonical import canonical_digest, required_text
from .components import ComponentPort


TransferKind = Literal["force", "shear", "moment", "wind_normal"]
AnalysisModel = Literal["pinned", "rigid", "rigid_zone", "semi_rigid", "spring"]
StiffnessStatus = Literal["unverified", "candidate", "verified"]


@dataclass(frozen=True)
class ConnectionDefinition:
    key: str
    label: str
    family: str
    transfers: tuple[TransferKind, ...]
    analysis_model: AnalysisModel
    stiffness_status: StiffnessStatus = "unverified"
    stiffness_basis: str = "Connection stiffness has not been verified."

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
