"""compile job claims and snapshots

Revision ID: 0004_compile_job_hardening
Revises: 0003_compile_job_error_fields
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_compile_job_hardening"
down_revision = "0003_compile_job_error_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compile_jobs", sa.Column("claim_token", sa.Uuid(), nullable=True))
    op.add_column("compile_jobs", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compile_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("compile_jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("compile_jobs", "attempt_count", server_default=None)
    op.create_index("ix_compile_jobs_lease_expires_at", "compile_jobs", ["lease_expires_at"])

    op.create_table(
        "compile_job_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["compile_job_id", "project_id", "tenant_id"],
            ["compile_jobs.id", "compile_jobs.project_id", "compile_jobs.tenant_id"],
            name="fk_compile_job_files_compile_job_project_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("compile_job_id", "filename", name="uq_compile_job_file_name"),
    )
    op.create_index("ix_compile_job_files_compile_job_id", "compile_job_files", ["compile_job_id"])
    op.create_index("ix_compile_job_files_tenant_id", "compile_job_files", ["tenant_id"])
    op.create_index("ix_compile_job_files_project_id", "compile_job_files", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_compile_job_files_project_id", table_name="compile_job_files")
    op.drop_index("ix_compile_job_files_tenant_id", table_name="compile_job_files")
    op.drop_index("ix_compile_job_files_compile_job_id", table_name="compile_job_files")
    op.drop_table("compile_job_files")
    op.drop_index("ix_compile_jobs_lease_expires_at", table_name="compile_jobs")
    op.drop_column("compile_jobs", "attempt_count")
    op.drop_column("compile_jobs", "lease_expires_at")
    op.drop_column("compile_jobs", "claimed_at")
    op.drop_column("compile_jobs", "claim_token")
