"""add atomic compile artifact bundle constraints

Revision ID: 0011_atomic_artifacts
Revises: 0010_llm_edit_progress
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_atomic_artifacts"
down_revision = "0010_llm_edit_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "artifacts",
        "kind",
        existing_type=sa.String(length=16),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        "uq_artifacts_tenant_compile_kind",
        "artifacts",
        ["tenant_id", "compile_job_id", "kind"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_artifacts_tenant_compile_kind",
        "artifacts",
        type_="unique",
    )
    op.alter_column(
        "artifacts",
        "kind",
        existing_type=sa.String(length=64),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
