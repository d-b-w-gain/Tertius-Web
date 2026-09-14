"""persist controlled structural report exports

Revision ID: 0016_structural_reports
Revises: 0015_structural_results
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_structural_reports"
down_revision = "0015_structural_results"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_structural_analysis_result_project_tenant",
        "structural_analysis_results",
        ["id", "project_id", "tenant_id"],
    )
    op.create_table(
        "structural_report_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("structural_analysis_result_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("document_kind", sa.String(length=64), nullable=False),
        sa.Column("report_schema_version", sa.String(length=32), nullable=False),
        sa.Column("report_identity_digest", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("pdf_content", sa.LargeBinary(), nullable=False),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("pdf_byte_size", sa.Integer(), nullable=False),
        sa.Column("evidence_json_content", sa.LargeBinary(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["app_users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["structural_analysis_result_id", "project_id", "tenant_id"],
            [
                "structural_analysis_results.id",
                "structural_analysis_results.project_id",
                "structural_analysis_results.tenant_id",
            ],
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
            "structural_analysis_result_id",
            "document_kind",
            "report_schema_version",
            name="uq_structural_report_export_identity",
        ),
    )
    op.create_index(
        op.f("ix_structural_report_exports_project_id"),
        "structural_report_exports",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structural_report_exports_structural_analysis_result_id"),
        "structural_report_exports",
        ["structural_analysis_result_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structural_report_exports_tenant_id"),
        "structural_report_exports",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_structural_report_exports_tenant_id"),
        table_name="structural_report_exports",
    )
    op.drop_index(
        op.f("ix_structural_report_exports_structural_analysis_result_id"),
        table_name="structural_report_exports",
    )
    op.drop_index(
        op.f("ix_structural_report_exports_project_id"),
        table_name="structural_report_exports",
    )
    op.drop_table("structural_report_exports")
    op.drop_constraint(
        "uq_structural_analysis_result_project_tenant",
        "structural_analysis_results",
        type_="unique",
    )
