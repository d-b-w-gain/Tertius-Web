from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import model_validator

from core.structural.contracts import (
    ActionStandardPackEvidence,
    LoadCase,
    LoadCombination,
    StructuralContract,
    UnavailableLoadCombination,
)


ActionStandardPackId = Literal["as_nzs_1170_0_2002_working_v1"]
ActionRole = Literal[
    "permanent",
    "imposed",
    "wind_positive_x",
    "wind_negative_x",
    "wind_positive_y",
    "wind_negative_y",
]


class StructuralActionCase(StructuralContract):
    """A project action identity without any standard-owned factors."""

    id: str
    label: str
    role: ActionRole

    def to_load_case(self) -> LoadCase:
        category: Literal["dead", "live", "wind"] = (
            "dead"
            if self.role == "permanent"
            else "live"
            if self.role == "imposed"
            else "wind"
        )
        return LoadCase(id=self.id, label=self.label, category=category)


class ResolvedActionStandardPack(StructuralContract):
    evidence: ActionStandardPackEvidence
    load_cases: list[LoadCase]
    load_combinations: list[LoadCombination]
    unavailable_combinations: list[UnavailableLoadCombination]

    @model_validator(mode="after")
    def validate_evidence(self) -> ResolvedActionStandardPack:
        if self.evidence.combination_ids != [
            combination.id for combination in self.load_combinations
        ]:
            raise ValueError("action-standard evidence does not match combinations")
        return self


_DIRECTION_SUFFIXES: dict[ActionRole, tuple[str, str]] = {
    "wind_positive_x": ("WX+", "transverse wind +X"),
    "wind_negative_x": ("WX-", "transverse wind -X"),
    "wind_positive_y": ("WY+", "longitudinal wind +Y"),
    "wind_negative_y": ("WY-", "longitudinal wind -Y"),
}


def resolve_action_standard_pack(
    pack_id: ActionStandardPackId,
    action_cases: Sequence[StructuralActionCase],
) -> ResolvedActionStandardPack:
    """Resolve semantic actions into the selected Tertius-owned envelope.

    This first pack deliberately preserves the factors already used by the
    working structural example.  Its ``working`` status is evidence that a
    licensed/checkable production pack is still required before certification;
    project source cannot alter the formulae.
    """

    if pack_id != "as_nzs_1170_0_2002_working_v1":
        raise ValueError(f"unsupported action standard pack {pack_id!r}")
    by_role = {action.role: action for action in action_cases}
    if len(by_role) != len(action_cases):
        raise ValueError("action cases contain duplicate semantic roles")
    permanent = by_role.get("permanent")
    if permanent is None:
        raise ValueError("action standard pack requires one permanent action")

    combinations = [
        LoadCombination(
            id="SLS-G",
            label="Permanent actions",
            limit_state="serviceability",
            factors={permanent.id: 1.0},
        ),
        LoadCombination(
            id="ULS-1.35G",
            label="ULS permanent actions only",
            limit_state="ultimate",
            factors={permanent.id: 1.35},
        ),
    ]
    imposed = by_role.get("imposed")
    unavailable: list[UnavailableLoadCombination] = []
    if imposed is not None:
        combinations.extend(
            (
                LoadCombination(
                    id="SLS-G+Q",
                    label="Permanent plus imposed actions",
                    limit_state="serviceability",
                    factors={permanent.id: 1.0, imposed.id: 1.0},
                ),
                LoadCombination(
                    id="ULS-1.2G+1.5Q",
                    label="ULS permanent plus imposed actions",
                    limit_state="ultimate",
                    factors={permanent.id: 1.2, imposed.id: 1.5},
                ),
            )
        )
    else:
        unavailable.extend(
            (
                UnavailableLoadCombination(
                    id="SLS-G+Q",
                    label="Permanent plus imposed actions",
                    limit_state="serviceability",
                    family="action_standard",
                    missing_inputs=["imposed"],
                    reason=(
                        "No imposed action (Q) is declared or derived for this project."
                    ),
                ),
                UnavailableLoadCombination(
                    id="ULS-1.2G+1.5Q",
                    label="ULS permanent plus imposed actions",
                    limit_state="ultimate",
                    family="action_standard",
                    missing_inputs=["imposed"],
                    reason=(
                        "No imposed action (Q) is declared or derived for this project."
                    ),
                ),
            )
        )
    for role, (suffix, label) in _DIRECTION_SUFFIXES.items():
        wind = by_role.get(role)
        if wind is None:
            reason = (
                f"No {label} action is generated from the Site basis and compiled "
                "structural topology."
            )
            unavailable.extend(
                (
                    UnavailableLoadCombination(
                        id=f"SLS-G+{suffix}",
                        label=f"Permanent plus {label}",
                        limit_state="serviceability",
                        family="action_standard",
                        missing_inputs=[role],
                        reason=reason,
                    ),
                    UnavailableLoadCombination(
                        id=f"ULS-1.2G+{suffix}",
                        label=f"ULS permanent plus {label} (destabilizing)",
                        limit_state="ultimate",
                        family="action_standard",
                        missing_inputs=[role],
                        reason=reason,
                    ),
                    UnavailableLoadCombination(
                        id=f"ULS-0.9G+{suffix}",
                        label=f"ULS stabilizing permanent action plus {label}",
                        limit_state="ultimate",
                        family="action_standard",
                        missing_inputs=[role],
                        reason=reason,
                    ),
                )
            )
            continue
        combinations.extend(
            (
                LoadCombination(
                    id=f"SLS-G+{suffix}",
                    label=f"Permanent plus {label}",
                    limit_state="serviceability",
                    factors={permanent.id: 1.0, wind.id: 1.0},
                ),
                LoadCombination(
                    id=f"ULS-1.2G+{suffix}",
                    label=f"ULS permanent plus {label} (destabilizing)",
                    limit_state="ultimate",
                    factors={permanent.id: 1.2, wind.id: 1.0},
                ),
                LoadCombination(
                    id=f"ULS-0.9G+{suffix}",
                    label=f"ULS stabilizing permanent action plus {label}",
                    limit_state="ultimate",
                    factors={permanent.id: 0.9, wind.id: 1.0},
                ),
            )
        )

    return ResolvedActionStandardPack(
        evidence=ActionStandardPackEvidence(
            pack_id=pack_id,
            pack_version="1.1.0",
            standard_reference=(
                "AS/NZS 1170.0:2002 Clauses 4.2.1, 4.2.2 and 4.3 "
                "working implementation"
            ),
            status="working",
            combination_ids=[combination.id for combination in combinations],
            basis=(
                "Tertius-owned deterministic action envelope including permanent-only, "
                "gravity, destabilizing wind, and 0.9G wind-reversal cases. Factors "
                "are not project-authored and this working pack is not certification "
                "evidence."
            ),
        ),
        load_cases=[action.to_load_case() for action in action_cases],
        load_combinations=combinations,
        unavailable_combinations=unavailable,
    )
