"""persist content-addressed structural analysis results

Revision ID: 0015_structural_results
Revises: 0014_as_nzs_1170_action_pack
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015_structural_results"
down_revision = "0014_as_nzs_1170_action_pack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structural_analysis_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("design_digest", sa.String(length=64), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("site_digest", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=False),
        sa.Column("snapshot_schema_version", sa.String(length=32), nullable=False),
        sa.Column("combination_id", sa.String(length=128), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculation_duration_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "key_digest",
            name="uq_structural_analysis_result_identity",
        ),
    )
    op.create_index(
        op.f("ix_structural_analysis_results_project_id"),
        "structural_analysis_results",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structural_analysis_results_tenant_id"),
        "structural_analysis_results",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_structural_analysis_results_tenant_id"),
        table_name="structural_analysis_results",
    )
    op.drop_index(
        op.f("ix_structural_analysis_results_project_id"),
        table_name="structural_analysis_results",
    )
    op.drop_table("structural_analysis_results")
