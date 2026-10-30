"""add tenant-scoped scene assets for incremental viewer scenes (#314)

Revision ID: 0018_scene_assets
Revises: 0017_timus_layout
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_scene_assets"
down_revision = "0017_timus_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scene_assets",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("asset_id", sa.String(length=80), primary_key=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scene_assets")
