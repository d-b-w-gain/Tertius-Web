"""llm usage records

Revision ID: 0006_llm_usage_records
Revises: 0005_compile_usage_records
Create Date: 2026-06-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_llm_usage_records"
down_revision = "0005_compile_usage_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="SET NULL (project_id)"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_llm_usage_records_event_id"),
    )
    op.create_index("ix_llm_usage_records_event_id", "llm_usage_records", ["event_id"])
    op.create_index("ix_llm_usage_records_tenant_id", "llm_usage_records", ["tenant_id"])
    op.create_index("ix_llm_usage_records_user_id", "llm_usage_records", ["user_id"])
    op.create_index("ix_llm_usage_records_project_id", "llm_usage_records", ["project_id"])
    op.create_index("ix_llm_usage_records_created_at", "llm_usage_records", ["created_at"])
    op.create_index("ix_llm_usage_records_tenant_created", "llm_usage_records", ["tenant_id", "created_at"])
    op.create_index("ix_llm_usage_records_user_created", "llm_usage_records", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_records_user_created", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_tenant_created", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_created_at", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_project_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_user_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_tenant_id", table_name="llm_usage_records")
    op.drop_index("ix_llm_usage_records_event_id", table_name="llm_usage_records")
    op.drop_table("llm_usage_records")
