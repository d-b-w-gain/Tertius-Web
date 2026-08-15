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
    "live": "imposed",
}
_WIND_ROLE_BY_ID = {
    "wind-plus-x": "wind_positive_x",
    "wind-minus-x": "wind_negative_x",
    "wind-plus-y": "wind_positive_y",
    "wind-minus-y": "wind_negative_y",
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
    for item in legacy_cases:
        if not isinstance(item, Mapping):
            raise ValueError("v1 load case must be an object")
        case_id = str(item.get("id") or "")
        category = str(item.get("category") or "")
        role = (
            _WIND_ROLE_BY_ID.get(case_id)
            if category == "wind"
            else _ROLE_BY_LEGACY_CATEGORY.get(category)
        )
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
            "action_standard_pack_id": "as_nzs_1170_0_2002_working_v1",
            "action_cases": action_cases,
        }
    )
    return StructuralProjectConfiguration.model_validate(migrated)


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
        if schema_version == "2.0":
            StructuralProjectConfiguration.model_validate(latest.content)
            current_count += 1
            continue
        migrated = migrate_configuration_v1_content(latest.content)
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
        description="Create v2 Structural configuration revisions from latest v1 data."
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
    print(f"{mode}: {migrated}; already v2: {current}")


if __name__ == "__main__":
    main()
