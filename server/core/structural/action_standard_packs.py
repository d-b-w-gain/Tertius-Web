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


ActionStandardPackId = Literal["as_nzs_1170_0_2002_amd5_roof_wind_v1"]
ImposedActionProfile = Literal[
    "all_other_roofs_distributed",
    "all_other_roofs_concentrated",
]
ActionRole = Literal[
    "permanent",
    "imposed",
    "wind_serviceability_positive_x",
    "wind_serviceability_negative_x",
    "wind_serviceability_positive_y",
    "wind_serviceability_negative_y",
    "wind_ultimate_positive_x",
    "wind_ultimate_negative_x",
    "wind_ultimate_positive_y",
    "wind_ultimate_negative_y",
]


class StructuralActionCase(StructuralContract):
    """A project action identity without any standard-owned factors."""

    id: str
    label: str
    role: ActionRole
    imposed_profile: ImposedActionProfile | None = None

    @model_validator(mode="after")
    def validate_imposed_profile(self) -> StructuralActionCase:
        if self.role == "imposed" and self.imposed_profile is None:
            raise ValueError("imposed actions require an imposed_profile")
        if self.role != "imposed" and self.imposed_profile is not None:
            raise ValueError("imposed_profile is only valid for imposed actions")
        return self

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


_DIRECTION_ROLES: dict[str, tuple[ActionRole, ActionRole, str]] = {
    "WX+": (
        "wind_serviceability_positive_x",
        "wind_ultimate_positive_x",
        "transverse wind +X",
    ),
    "WX-": (
        "wind_serviceability_negative_x",
        "wind_ultimate_negative_x",
        "transverse wind -X",
    ),
    "WY+": (
        "wind_serviceability_positive_y",
        "wind_ultimate_positive_y",
        "longitudinal wind +Y",
    ),
    "WY-": (
        "wind_serviceability_negative_y",
        "wind_ultimate_negative_y",
        "longitudinal wind -Y",
    ),
}

_PACK_SOURCE_SHA256 = "df3c4e7afa753fe06ddf94fb1ae4fe103d62c633db04cf5360478443be247b37"


def resolve_action_standard_pack(
    pack_id: ActionStandardPackId,
    action_cases: Sequence[StructuralActionCase],
) -> ResolvedActionStandardPack:
    """Resolve semantic actions into the selected Tertius-owned envelope.

    This pack is deliberately limited to ordinary roof imposed action and
    directional wind. It separates serviceability and ultimate wind events,
    applies the Table 4.1 all-other-roofs factors, and fails closed for action
    profiles that are outside that scope.
    """

    if pack_id != "as_nzs_1170_0_2002_amd5_roof_wind_v1":
        raise ValueError(f"unsupported action standard pack {pack_id!r}")
    non_imposed_actions = [action for action in action_cases if action.role != "imposed"]
    by_role = {action.role: action for action in non_imposed_actions}
    if len(by_role) != len(non_imposed_actions):
        raise ValueError("non-imposed action cases contain duplicate semantic roles")
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
            id="ULS-0.9G",
            label="ULS stabilizing permanent action only",
            limit_state="ultimate",
            factors={permanent.id: 0.9},
        ),
        LoadCombination(
            id="ULS-1.35G",
            label="ULS permanent actions only",
            limit_state="ultimate",
            factors={permanent.id: 1.35},
        ),
    ]
    distributed_imposed = [
        action
        for action in action_cases
        if action.imposed_profile == "all_other_roofs_distributed"
    ]
    concentrated_imposed = [
        action
        for action in action_cases
        if action.imposed_profile == "all_other_roofs_concentrated"
    ]
    if len(distributed_imposed) > 1:
        raise ValueError(
            "all-other-roofs distributed action requires one semantic case"
        )
    unavailable: list[UnavailableLoadCombination] = []
    if distributed_imposed:
        imposed = distributed_imposed[0]
        combinations.extend(
            (
                LoadCombination(
                    id="SLS-G+Q",
                    label="Permanent plus imposed actions",
                    limit_state="serviceability",
                    factors={permanent.id: 1.0, imposed.id: 0.7},
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
    if concentrated_imposed:
        for imposed in sorted(concentrated_imposed, key=lambda action: action.id):
            combinations.extend(
                (
                    LoadCombination(
                        id=f"SLS-G+Qc:{imposed.id}",
                        label=f"Permanent plus {imposed.label}",
                        limit_state="serviceability",
                        factors={permanent.id: 1.0, imposed.id: 1.0},
                    ),
                    LoadCombination(
                        id=f"ULS-1.2G+1.5Qc:{imposed.id}",
                        label=f"ULS permanent plus {imposed.label}",
                        limit_state="ultimate",
                        factors={permanent.id: 1.2, imposed.id: 1.5},
                    ),
                )
            )
    else:
        unavailable.extend(
            (
                UnavailableLoadCombination(
                    id="SLS-G+Qc:roof-local",
                    label="Permanent plus concentrated roof action",
                    limit_state="serviceability",
                    family="action_standard",
                    missing_inputs=["all_other_roofs_concentrated"],
                    reason=(
                        "No concentrated roof-action receiver was derived from the "
                        "compiled mechanical roof members."
                    ),
                ),
                UnavailableLoadCombination(
                    id="ULS-1.2G+1.5Qc:roof-local",
                    label="ULS permanent plus concentrated roof action",
                    limit_state="ultimate",
                    family="action_standard",
                    missing_inputs=["all_other_roofs_concentrated"],
                    reason=(
                        "No concentrated roof-action receiver was derived from the "
                        "compiled mechanical roof members."
                    ),
                ),
            )
        )
    for suffix, (serviceability_role, ultimate_role, label) in _DIRECTION_ROLES.items():
        serviceability_wind = by_role.get(serviceability_role)
        ultimate_wind = by_role.get(ultimate_role)
        if serviceability_wind is None:
            unavailable.append(
                UnavailableLoadCombination(
                    id=f"SLS-G+{suffix}",
                    label=f"Permanent plus {label}",
                    limit_state="serviceability",
                    family="action_standard",
                    missing_inputs=[serviceability_role],
                    reason=(
                        f"The serviceability {label} action must be generated from "
                        "the Site basis and compiled structural topology."
                    ),
                )
            )
        else:
            combinations.append(
                LoadCombination(
                    id=f"SLS-G+{suffix}",
                    label=f"Permanent plus {label}",
                    limit_state="serviceability",
                    factors={permanent.id: 1.0, serviceability_wind.id: 1.0},
                )
            )
        if ultimate_wind is None:
            unavailable.extend(
                (
                    UnavailableLoadCombination(
                        id=f"ULS-1.2G+{suffix}",
                        label=f"ULS permanent plus {label} (destabilizing)",
                        limit_state="ultimate",
                        family="action_standard",
                        missing_inputs=[ultimate_role],
                        reason=(
                            f"The ultimate {label} action must be generated from "
                            "the Site basis and compiled structural topology."
                        ),
                    ),
                    UnavailableLoadCombination(
                        id=f"ULS-0.9G+{suffix}",
                        label=f"ULS stabilizing permanent action plus {label}",
                        limit_state="ultimate",
                        family="action_standard",
                        missing_inputs=[ultimate_role],
                        reason=(
                            f"The ultimate {label} action must be generated from "
                            "the Site basis and compiled structural topology."
                        ),
                    ),
                )
            )
        else:
            combinations.extend(
                (
                    LoadCombination(
                        id=f"ULS-1.2G+{suffix}",
                        label=f"ULS permanent plus {label} (destabilizing)",
                        limit_state="ultimate",
                        factors={permanent.id: 1.2, ultimate_wind.id: 1.0},
                    ),
                    LoadCombination(
                        id=f"ULS-0.9G+{suffix}",
                        label=f"ULS stabilizing permanent action plus {label}",
                        limit_state="ultimate",
                        factors={permanent.id: 0.9, ultimate_wind.id: 1.0},
                    ),
                )
            )

    return ResolvedActionStandardPack(
        evidence=ActionStandardPackEvidence(
            pack_id=pack_id,
            pack_version="1.1.0",
            standard_reference=(
                "AS/NZS 1170.0:2002 including Amendments 1-5, Clauses 4.2.1, "
                "4.2.2 and 4.3, and Table 4.1"
            ),
            status="verified",
            source_document_sha256=_PACK_SOURCE_SHA256,
            applicability=[
                "Permanent actions",
                "All-other-roofs distributed imposed actions",
                "Alternative all-other-roofs concentrated imposed actions",
                "Directional serviceability and ultimate wind actions",
            ],
            exclusions=[
                "Floor, storage, machinery and occupied-roof imposed actions",
                "Snow, earthquake, fire, liquid, earth and impact actions",
            ],
            combination_ids=[combination.id for combination in combinations],
            basis=(
                "Tertius-owned deterministic action envelope. SLS wind uses a distinct "
                "serviceability design event; ULS wind uses the project ultimate event. "
                "For all-other-roofs distributed action, psi_s=0.7 and psi_c=0.0; "
                "for each alternative concentrated action, psi_s=1.0 and psi_c=0.0. "
                "Distributed and concentrated roof cases are never combined. The pack "
                "does not validate the upstream action magnitudes or surface-pressure "
                "coefficients."
            ),
        ),
        load_cases=[action.to_load_case() for action in action_cases],
        load_combinations=combinations,
        unavailable_combinations=unavailable,
    )
