"""add llm edit job progress snapshot

Revision ID: 0010_llm_edit_progress
Revises: 0009_compile_llm_origin
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_llm_edit_progress"
down_revision = "0009_compile_llm_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_edit_jobs",
        sa.Column(
            "progress_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.alter_column(
        "llm_edit_jobs",
        "progress_payload",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("llm_edit_jobs", "progress_payload")
