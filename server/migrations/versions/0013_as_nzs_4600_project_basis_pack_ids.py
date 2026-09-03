"""flag-day rename AS/NZS 4600 project-basis capacity packs

Revision ID: 0013_as_nzs_4600_packs
Revises: 0012_structural_config
Create Date: 2026-08-17
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "0013_as_nzs_4600_packs"
down_revision = "0012_structural_config"
branch_labels = None
depends_on = None


_OLD_CROSS_SECTION_PACK = "as_nzs_4600_2018_ewm"
_NEW_CROSS_SECTION_PACK = "as_nzs_4600_2005_a1_ewm"
_OLD_MEMBER_PACK = "as_nzs_4600_2018_ewm_member"
_NEW_MEMBER_PACK = "as_nzs_4600_2005_a1_member"
_OLD_MEMBER_STANDARD = "AS/NZS 4600:2018"
_NEW_MEMBER_STANDARD = (
    "AS/NZS 4600:2005 incorporating Amendment No. 1 with the AS/NZS 4600 "
    "developments paper as project supplement"
)


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
    cross_section_from: str,
    cross_section_to: str,
    member_from: str,
    member_to: str,
    standard_from: str,
    standard_to: str,
) -> dict[str, object] | None:
    copied = json.loads(json.dumps(content))
    changed = False
    cross_section = copied.get("cross_section_verification")
    if (
        isinstance(cross_section, dict)
        and cross_section.get("pack_id") == cross_section_from
    ):
        cross_section["pack_id"] = cross_section_to
        changed = True
    member = copied.get("member_stability_verification")
    if isinstance(member, dict) and member.get("pack_id") == member_from:
        member["pack_id"] = member_to
        changed = True
    design_basis = copied.get("design_basis")
    if isinstance(design_basis, dict):
        standards = design_basis.get("standards")
        if isinstance(standards, dict) and standards.get("members") == standard_from:
            standards["members"] = standard_to
            changed = True
    return copied if changed else None


def _append_rewritten_latest_revisions(
    *,
    cross_section_from: str,
    cross_section_to: str,
    member_from: str,
    member_to: str,
    standard_from: str,
    standard_to: str,
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
            cross_section_from=cross_section_from,
            cross_section_to=cross_section_to,
            member_from=member_from,
            member_to=member_to,
            standard_from=standard_from,
            standard_to=standard_to,
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
        cross_section_from=_OLD_CROSS_SECTION_PACK,
        cross_section_to=_NEW_CROSS_SECTION_PACK,
        member_from=_OLD_MEMBER_PACK,
        member_to=_NEW_MEMBER_PACK,
        standard_from=_OLD_MEMBER_STANDARD,
        standard_to=_NEW_MEMBER_STANDARD,
    )


def downgrade() -> None:
    _append_rewritten_latest_revisions(
        cross_section_from=_NEW_CROSS_SECTION_PACK,
        cross_section_to=_OLD_CROSS_SECTION_PACK,
        member_from=_NEW_MEMBER_PACK,
        member_to=_OLD_MEMBER_PACK,
        standard_from=_NEW_MEMBER_STANDARD,
        standard_to=_OLD_MEMBER_STANDARD,
    )
