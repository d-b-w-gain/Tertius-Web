"""add compile job originating llm edit link

Revision ID: 0009_compile_llm_origin
Revises: 0008_llm_edit_jobs
Create Date: 2026-06-19

Adds a loose nullable UUID link from compile_jobs to llm_edit_jobs.
The column intentionally has no foreign key: compile_jobs use composite
tenant/project constraints, existing rows have no origin, and manual compiles
must remain valid with a null origin.
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_compile_llm_origin"
down_revision = "0008_llm_edit_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "compile_jobs",
        sa.Column("originating_llm_edit_job_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_compile_jobs_originating_llm_edit",
        "compile_jobs",
        ["originating_llm_edit_job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_compile_jobs_originating_llm_edit", table_name="compile_jobs")
    op.drop_column("compile_jobs", "originating_llm_edit_job_id")
