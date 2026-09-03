"""add persisted Timus drafting layout

Revision ID: 0011_timus_layout
Revises: 0010_llm_edit_progress
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_timus_layout"
down_revision = "0010_llm_edit_progress"
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
