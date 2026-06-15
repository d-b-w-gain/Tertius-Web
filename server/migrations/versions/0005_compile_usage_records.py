"""compile usage records

Revision ID: 0005_compile_usage_records
Revises: 0004_compile_job_hardening
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_compile_usage_records"
down_revision = "0004_compile_job_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compile_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("export_format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("compute_duration_seconds", sa.Float(), nullable=False),
        sa.Column("artifact_byte_size", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("base_rate_cents_per_hour", sa.Integer(), nullable=False),
        sa.Column("format_multiplier", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["compile_job_id", "project_id", "tenant_id"],
            ["compile_jobs.id", "compile_jobs.project_id", "compile_jobs.tenant_id"],
            name="fk_usage_records_compile_job_project_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["app_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "compile_job_id", name="uq_compile_usage_records_tenant_job"),
    )
    op.create_index("ix_compile_usage_records_tenant_id", "compile_usage_records", ["tenant_id"])
    op.create_index("ix_compile_usage_records_project_id", "compile_usage_records", ["project_id"])
    op.create_index("ix_compile_usage_records_compile_job_id", "compile_usage_records", ["compile_job_id"])
    op.create_index(
        "ix_compile_usage_records_tenant_created",
        "compile_usage_records",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_compile_usage_records_tenant_created", table_name="compile_usage_records")
    op.drop_index("ix_compile_usage_records_compile_job_id", table_name="compile_usage_records")
    op.drop_index("ix_compile_usage_records_project_id", table_name="compile_usage_records")
    op.drop_index("ix_compile_usage_records_tenant_id", table_name="compile_usage_records")
    op.drop_table("compile_usage_records")
