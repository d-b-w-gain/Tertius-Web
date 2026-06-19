"""llm edit jobs

Revision ID: 0008_llm_edit_jobs
Revises: 0007_auth_sessions
Create Date: 2026-06-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_llm_edit_jobs"
down_revision = "0007_auth_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_edit_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", "tenant_id", name="uq_llm_edit_jobs_id_project_tenant"),
    )
    op.create_index("ix_llm_edit_jobs_tenant_id", "llm_edit_jobs", ["tenant_id"])
    op.create_index("ix_llm_edit_jobs_project_id", "llm_edit_jobs", ["project_id"])
    op.create_index("ix_llm_edit_jobs_status", "llm_edit_jobs", ["status"])
    op.create_index("ix_llm_edit_jobs_created_at", "llm_edit_jobs", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_edit_jobs_created_at", table_name="llm_edit_jobs")
    op.drop_index("ix_llm_edit_jobs_status", table_name="llm_edit_jobs")
    op.drop_index("ix_llm_edit_jobs_project_id", table_name="llm_edit_jobs")
    op.drop_index("ix_llm_edit_jobs_tenant_id", table_name="llm_edit_jobs")
    op.drop_table("llm_edit_jobs")
