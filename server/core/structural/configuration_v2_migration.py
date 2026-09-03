from __future__ import annotations

import argparse
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import StructuralConfigurationRevision
from core.structural.project_configuration import StructuralProjectConfiguration


_ROLE_BY_LEGACY_CATEGORY = {
    "dead": "permanent",
}
_WORKING_PACK_ID = "as_nzs_1170_0_2002_working_v1"
_VERIFIED_PACK_ID = "as_nzs_1170_0_2002_amd5_roof_wind_v1"
_WORKING_DERIVED_ROLES = {
    "imposed",
    "wind_positive_x",
    "wind_negative_x",
    "wind_positive_y",
    "wind_negative_y",
}


def migrate_configuration_v1_content(
    content: Mapping[str, Any],
) -> StructuralProjectConfiguration:
    """Translate one stored v1 revision into the sole v2 contract.

    This function exists for the flag-day data migration only. The API and
    analysis runtime do not accept v1 configuration.
    """

    if content.get("schema_version") != "1.0":
        raise ValueError("configuration is not schema version 1.0")
    migrated = deepcopy(dict(content))
    legacy_cases = migrated.pop("load_cases", None)
    if not isinstance(legacy_cases, list):
        raise ValueError("v1 configuration has no load_cases list")
    action_cases: list[dict[str, str]] = []
    discarded_case_ids: set[str] = set()
    for item in legacy_cases:
        if not isinstance(item, Mapping):
            raise ValueError("v1 load case must be an object")
        case_id = str(item.get("id") or "")
        category = str(item.get("category") or "")
        if category in {"live", "wind"}:
            # The v2 pipeline derives roof-imposed and separate SLS/ULS wind
            # actions from the Site basis and compiled mechanical roles.
            discarded_case_ids.add(case_id)
            continue
        role = _ROLE_BY_LEGACY_CATEGORY.get(category)
        if role is None:
            raise ValueError(
                f"v1 load case {case_id!r} cannot be mapped to a semantic action role"
            )
        action_cases.append(
            {
                "id": case_id,
                "label": str(item.get("label") or case_id),
                "role": role,
            }
        )

    _discard_member_loads_for_cases(migrated, discarded_case_ids)
    migrated.pop("load_combinations", None)
    for field in (
        "cross_section_verification",
        "member_stability_verification",
    ):
        verification = migrated.get(field)
        if isinstance(verification, dict):
            verification.pop("combination_ids", None)
    migrated.update(
        {
            "schema_version": "2.0",
            "action_standard_pack_id": _VERIFIED_PACK_ID,
            "action_cases": action_cases,
        }
    )
    return StructuralProjectConfiguration.model_validate(migrated)


def migrate_working_v2_content(
    content: Mapping[str, Any],
) -> StructuralProjectConfiguration:
    """Replace the retired working pack in one stored v2 revision.

    The runtime does not accept the old pack. This flag-day migration discards
    its derived roof/wind action identities so the current pipeline regenerates
    distinct SLS and ULS cases from Site data and compiled mechanical roles.
    """

    if content.get("schema_version") != "2.0":
        raise ValueError("configuration is not schema version 2.0")
    if content.get("action_standard_pack_id") != _WORKING_PACK_ID:
        raise ValueError("configuration does not use the retired working pack")
    migrated = deepcopy(dict(content))
    action_cases = migrated.get("action_cases")
    if not isinstance(action_cases, list):
        raise ValueError("v2 configuration has no action_cases list")
    retained_cases: list[Mapping[str, Any]] = []
    discarded_case_ids: set[str] = set()
    for item in action_cases:
        if not isinstance(item, Mapping):
            raise ValueError("v2 action case must be an object")
        role = str(item.get("role") or "")
        if role == "permanent":
            retained_cases.append(item)
        elif role in _WORKING_DERIVED_ROLES:
            discarded_case_ids.add(str(item.get("id") or ""))
        else:
            raise ValueError(f"working v2 action role {role!r} cannot be migrated")
    migrated["action_standard_pack_id"] = _VERIFIED_PACK_ID
    migrated["action_cases"] = retained_cases
    _discard_member_loads_for_cases(migrated, discarded_case_ids)
    return StructuralProjectConfiguration.model_validate(migrated)


def _discard_member_loads_for_cases(
    content: dict[str, Any],
    case_ids: set[str],
) -> None:
    if not case_ids:
        return
    for field in ("member_loads", "member_distributed_loads"):
        loads = content.get(field)
        if not isinstance(loads, list):
            continue
        content[field] = [
            load
            for load in loads
            if not (
                isinstance(load, Mapping)
                and str(load.get("case_id") or "") in case_ids
            )
        ]


def migrate_latest_revisions(db: Session, *, apply: bool) -> tuple[int, int]:
    latest_by_project: dict[object, StructuralConfigurationRevision] = {}
    revisions = db.scalars(
        select(StructuralConfigurationRevision).order_by(
            StructuralConfigurationRevision.project_id,
            StructuralConfigurationRevision.revision,
        )
    ).all()
    for revision in revisions:
        latest_by_project[revision.project_id] = revision

    migrated_count = 0
    current_count = 0
    for latest in latest_by_project.values():
        schema_version = latest.content.get("schema_version")
        pack_id = latest.content.get("action_standard_pack_id")
        if schema_version == "2.0" and pack_id != _WORKING_PACK_ID:
            StructuralProjectConfiguration.model_validate(latest.content)
            current_count += 1
            continue
        migrated = (
            migrate_working_v2_content(latest.content)
            if schema_version == "2.0"
            else migrate_configuration_v1_content(latest.content)
        )
        migrated_count += 1
        if apply:
            db.add(
                StructuralConfigurationRevision(
                    tenant_id=latest.tenant_id,
                    project_id=latest.project_id,
                    revision=latest.revision + 1,
                    digest=migrated.configuration_digest,
                    content=migrated.model_dump(mode="json"),
                    created_by=latest.created_by,
                )
            )
    if apply:
        db.commit()
    return migrated_count, current_count


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create current Structural configuration revisions from legacy v1 "
            "or retired working-pack data."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit new revisions. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()

    from core.db import SessionLocal

    with SessionLocal() as db:
        migrated, current = migrate_latest_revisions(db, apply=args.apply)
    mode = "applied" if args.apply else "would migrate"
    print(f"{mode}: {migrated}; already current: {current}")


if __name__ == "__main__":
    main()
