"""add public read-only project share links (#417)

Revision ID: 0019_project_shares
Revises: 0018_scene_assets
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_project_shares"
down_revision = "0018_scene_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_shares",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workbench", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_project_shares_tenant_id", "project_shares", ["tenant_id"])
    op.create_index("ix_project_shares_project_id", "project_shares", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_shares_project_id", table_name="project_shares")
    op.drop_index("ix_project_shares_tenant_id", table_name="project_shares")
    op.drop_table("project_shares")
