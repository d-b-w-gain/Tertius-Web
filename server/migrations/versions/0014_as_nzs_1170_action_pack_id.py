"""flag-day rename AS/NZS 1170 action-standard pack

Revision ID: 0014_as_nzs_1170_action_pack
Revises: 0013_as_nzs_4600_packs
Create Date: 2026-08-18
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0014_as_nzs_1170_action_pack"
down_revision = "0013_as_nzs_4600_packs"
branch_labels = None
depends_on = None


_OLD_PACK = "as_nzs_1170_0_2002_working_v1"
_NEW_PACK = "as_nzs_1170_0_2002_amd5_roof_wind_v1"
_DERIVED_ROLES = {
    "imposed",
    "wind_positive_x",
    "wind_negative_x",
    "wind_positive_y",
    "wind_negative_y",
}


def _digest(content: dict[str, object]) -> str:
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rewritten(
    content: dict[str, object],
    *,
    pack_from: str,
    pack_to: str,
    discard_retired_derived_actions: bool,
) -> dict[str, object] | None:
    if content.get("schema_version") != "2.0":
        return None
    if content.get("action_standard_pack_id") != pack_from:
        return None
    copied = json.loads(json.dumps(content))
    copied["action_standard_pack_id"] = pack_to
    if discard_retired_derived_actions:
        action_cases = copied.get("action_cases")
        if isinstance(action_cases, list):
            discarded_case_ids = {
                str(item.get("id") or "")
                for item in action_cases
                if (
                    isinstance(item, dict)
                    and item.get("role") in _DERIVED_ROLES
                )
            }
            copied["action_cases"] = [
                item
                for item in action_cases
                if not (
                    isinstance(item, dict)
                    and item.get("role") in _DERIVED_ROLES
                )
            ]
            for field in ("member_loads", "member_distributed_loads"):
                loads = copied.get(field)
                if isinstance(loads, list):
                    copied[field] = [
                        load
                        for load in loads
                        if not (
                            isinstance(load, dict)
                            and str(load.get("case_id") or "")
                            in discarded_case_ids
                        )
                    ]
    return copied


def _append_rewritten_latest_revisions(
    *,
    pack_from: str,
    pack_to: str,
    discard_retired_derived_actions: bool,
) -> None:
    connection = op.get_bind()
    revisions = sa.table(
        "structural_configuration_revisions",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("project_id", sa.Uuid()),
        sa.column("revision", sa.Integer()),
        sa.column("digest", sa.String(64)),
        sa.column("content", sa.JSON()),
        sa.column("created_by", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    candidate = revisions.alias("candidate")
    newer = revisions.alias("newer")
    latest_rows = connection.execute(
        sa.select(candidate).where(
            ~sa.exists(
                sa.select(sa.literal(1)).where(
                    newer.c.tenant_id == candidate.c.tenant_id,
                    newer.c.project_id == candidate.c.project_id,
                    newer.c.revision > candidate.c.revision,
                )
            )
        )
    ).mappings()
    inserts: list[dict[str, object]] = []
    for row in latest_rows:
        content = row["content"]
        if not isinstance(content, dict):
            continue
        rewritten = _rewritten(
            content,
            pack_from=pack_from,
            pack_to=pack_to,
            discard_retired_derived_actions=discard_retired_derived_actions,
        )
        if rewritten is None:
            continue
        inserts.append(
            {
                "id": uuid.uuid4(),
                "tenant_id": row["tenant_id"],
                "project_id": row["project_id"],
                "revision": int(row["revision"]) + 1,
                "digest": _digest(rewritten),
                "content": rewritten,
                "created_by": row["created_by"],
                "created_at": datetime.now(timezone.utc),
            }
        )
    if inserts:
        connection.execute(sa.insert(revisions), inserts)


def upgrade() -> None:
    _append_rewritten_latest_revisions(
        pack_from=_OLD_PACK,
        pack_to=_NEW_PACK,
        discard_retired_derived_actions=True,
    )


def downgrade() -> None:
    _append_rewritten_latest_revisions(
        pack_from=_NEW_PACK,
        pack_to=_OLD_PACK,
        discard_retired_derived_actions=False,
    )
