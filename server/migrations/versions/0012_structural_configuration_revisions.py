"""add revisioned Structural workbench configuration

Revision ID: 0012_structural_config
Revises: 0011_atomic_artifacts
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_structural_config"
down_revision = "0011_atomic_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "structural_configuration_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["app_users.id"],
        ),
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
            "revision",
            name="uq_structural_configuration_project_revision",
        ),
    )
    op.create_index(
        op.f("ix_structural_configuration_revisions_project_id"),
        "structural_configuration_revisions",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_structural_configuration_revisions_tenant_id"),
        "structural_configuration_revisions",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_structural_configuration_revisions_tenant_id"),
        table_name="structural_configuration_revisions",
    )
    op.drop_index(
        op.f("ix_structural_configuration_revisions_project_id"),
        table_name="structural_configuration_revisions",
    )
    op.drop_table("structural_configuration_revisions")
