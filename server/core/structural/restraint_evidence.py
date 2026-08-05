from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Literal

from .contracts import RestraintConfigurationIdentity


class RestraintEvidenceError(ValueError):
    """Raised when the immutable restraint-evidence registry is invalid."""


@dataclass(frozen=True)
class RestraintEvidenceResolution:
    pack_id: str
    pack_version: str | None
    identity_status: Literal["pass", "fail"]
    identity_mismatches: tuple[str, ...]
    design_force_capacity_kN: float | None
    design_moment_capacity_kNm: float | None
    stiffness_status: Literal["unverified", "verified"]
    capacity_basis: str
    references: tuple[str, ...]
    assumptions: tuple[str, ...]
    exclusions: tuple[str, ...]


def _registry_path() -> Path:
    return Path(__file__).with_name("data") / "restraint_evidence_packs.json"


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RestraintEvidenceError(f"{label} must be non-empty text")
    return value.strip()


def _optional_positive(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RestraintEvidenceError(f"{label} must be numeric or null")
    normalized = float(value)
    if normalized <= 0:
        raise RestraintEvidenceError(f"{label} must be positive")
    return normalized


@lru_cache(maxsize=1)
def restraint_evidence_registry() -> dict[str, dict[str, Any]]:
    payload = json.loads(_registry_path().read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise RestraintEvidenceError("unsupported restraint-evidence schema")
    packs = payload.get("packs")
    if not isinstance(packs, list):
        raise RestraintEvidenceError("restraint-evidence packs must be a list")
    indexed: dict[str, dict[str, Any]] = {}
    for raw_pack in packs:
        if not isinstance(raw_pack, dict):
            raise RestraintEvidenceError(
                "each restraint-evidence pack must be an object"
            )
        pack_id = _required_text(raw_pack.get("id"), "restraint-evidence pack ID")
        if pack_id in indexed:
            raise RestraintEvidenceError(
                f"duplicate restraint-evidence pack {pack_id!r}"
            )
        indexed[pack_id] = raw_pack
    return indexed


def _identity_mismatches(
    expected: dict[str, Any],
    actual: RestraintConfigurationIdentity,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    for field in ("primary_part_number", "bracing_part_number"):
        expected_value = _required_text(expected.get(field), f"applicability {field}")
        actual_value = getattr(actual, field)
        if actual_value != expected_value:
            mismatches.append(
                f"{field} expected {expected_value!r}, rendered {actual_value!r}"
            )
    expected_connectors = expected.get("connector_part_numbers")
    if not isinstance(expected_connectors, list) or not expected_connectors:
        raise RestraintEvidenceError(
            "applicability connector_part_numbers must be a non-empty list"
        )
    expected_connector_set = sorted(
        _required_text(value, "connector part number") for value in expected_connectors
    )
    actual_connector_set = sorted(actual.connector_part_numbers)
    if actual_connector_set != expected_connector_set:
        mismatches.append(
            "connector_part_numbers expected "
            f"{expected_connector_set!r}, rendered {actual_connector_set!r}"
        )
    return tuple(mismatches)


def resolve_restraint_evidence(
    pack_id: str,
    configuration: RestraintConfigurationIdentity,
) -> RestraintEvidenceResolution:
    packs = restraint_evidence_registry()
    raw_pack = packs.get(pack_id)
    if raw_pack is None:
        return RestraintEvidenceResolution(
            pack_id=pack_id,
            pack_version=None,
            identity_status="fail",
            identity_mismatches=(f"evidence pack {pack_id!r} is not registered",),
            design_force_capacity_kN=None,
            design_moment_capacity_kNm=None,
            stiffness_status="unverified",
            capacity_basis="No registered evidence pack matched this candidate.",
            references=(),
            assumptions=(),
            exclusions=(),
        )

    applicability = raw_pack.get("applicability")
    resistance = raw_pack.get("resistance")
    source = raw_pack.get("source")
    if not isinstance(applicability, dict):
        raise RestraintEvidenceError(f"pack {pack_id!r} has no applicability object")
    if not isinstance(resistance, dict):
        raise RestraintEvidenceError(f"pack {pack_id!r} has no resistance object")
    if not isinstance(source, dict):
        raise RestraintEvidenceError(f"pack {pack_id!r} has no source object")
    source_sha = _required_text(source.get("sha256"), "source SHA-256").lower()
    if len(source_sha) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha
    ):
        raise RestraintEvidenceError(
            "source SHA-256 must contain 64 hexadecimal characters"
        )
    pages = source.get("pages")
    if not isinstance(pages, list) or not pages:
        raise RestraintEvidenceError("evidence source pages must be a non-empty list")
    stiffness_status = resistance.get("stiffness_status")
    if stiffness_status not in {"unverified", "verified"}:
        raise RestraintEvidenceError("evidence stiffness_status is invalid")
    mismatches = _identity_mismatches(applicability, configuration)
    references = (
        f"{_required_text(source.get('title'), 'source title')} — "
        f"{_required_text(source.get('url'), 'source URL')}",
        f"SHA-256 {source_sha}",
        *(str(page) for page in pages),
    )
    return RestraintEvidenceResolution(
        pack_id=pack_id,
        pack_version=_required_text(raw_pack.get("version"), "pack version"),
        identity_status="fail" if mismatches else "pass",
        identity_mismatches=mismatches,
        design_force_capacity_kN=_optional_positive(
            resistance.get("design_force_capacity_kN"),
            "design force capacity",
        ),
        design_moment_capacity_kNm=_optional_positive(
            resistance.get("design_moment_capacity_kNm"),
            "design moment capacity",
        ),
        stiffness_status=stiffness_status,
        capacity_basis=_required_text(resistance.get("basis"), "capacity basis"),
        references=references,
        assumptions=tuple(str(value) for value in raw_pack.get("assumptions", [])),
        exclusions=tuple(str(value) for value in raw_pack.get("exclusions", [])),
    )
