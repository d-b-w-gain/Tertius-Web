"""add persisted Timus drafting layout

Revision ID: 0017_timus_layout
Revises: 0016_structural_reports
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_timus_layout"
down_revision = "0016_structural_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "timus_settings",
        sa.Column(
            "layout",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'combined'"),
        ),
    )
    op.alter_column("timus_settings", "layout", server_default=None)


def downgrade() -> None:
    op.drop_column("timus_settings", "layout")
