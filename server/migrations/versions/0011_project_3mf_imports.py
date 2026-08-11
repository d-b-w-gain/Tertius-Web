"""add immutable 3mf project import persistence

Revision ID: 0011_project_3mf_imports
Revises: 0010_llm_edit_progress
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_project_3mf_imports"
down_revision = "0010_llm_edit_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("logical_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("conversion_version", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('source_3mf', 'derived_brep', 'import_manifest')",
            name="ck_project_assets_kind",
        ),
        sa.CheckConstraint("byte_size >= 0", name="ck_project_assets_byte_size_nonnegative"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_project_assets_sha256"),
        sa.CheckConstraint("revision > 0", name="ck_project_assets_revision_positive"),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            name="fk_project_assets_project_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "tenant_id",
            name="uq_project_assets_id_project_tenant",
        ),
        sa.UniqueConstraint(
            "project_id",
            "kind",
            "revision",
            name="uq_project_assets_project_kind_revision",
        ),
    )
    op.create_index("ix_project_assets_project_id", "project_assets", ["project_id"])
    op.create_index("ix_project_assets_tenant_id", "project_assets", ["tenant_id"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tertius_reject_project_asset_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'project_assets rows are immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_project_assets_immutable
        BEFORE UPDATE ON project_assets
        FOR EACH ROW
        EXECUTE FUNCTION tertius_reject_project_asset_update()
        """
    )

    op.create_table(
        "project_import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("source_asset_id", sa.Uuid(), nullable=False),
        sa.Column("brep_asset_id", sa.Uuid(), nullable=True),
        sa.Column("manifest_asset_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("progress_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt > 0", name="ck_project_import_jobs_attempt_positive"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_project_import_jobs_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "tenant_id"],
            ["projects.id", "projects.tenant_id"],
            name="fk_project_import_jobs_project_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_import_jobs_requester_tenant_membership",
        ),
        sa.ForeignKeyConstraint(
            ["source_asset_id", "project_id", "tenant_id"],
            [
                "project_assets.id",
                "project_assets.project_id",
                "project_assets.tenant_id",
            ],
            name="fk_project_import_jobs_source_asset_scope",
        ),
        sa.ForeignKeyConstraint(
            ["brep_asset_id", "project_id", "tenant_id"],
            [
                "project_assets.id",
                "project_assets.project_id",
                "project_assets.tenant_id",
            ],
            name="fk_project_import_jobs_brep_asset_scope",
        ),
        sa.ForeignKeyConstraint(
            ["manifest_asset_id", "project_id", "tenant_id"],
            [
                "project_assets.id",
                "project_assets.project_id",
                "project_assets.tenant_id",
            ],
            name="fk_project_import_jobs_manifest_asset_scope",
        ),
        sa.ForeignKeyConstraint(["requested_by"], ["app_users.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "tenant_id",
            name="uq_project_import_jobs_id_project_tenant",
        ),
    )
    op.create_index("ix_project_import_jobs_project_id", "project_import_jobs", ["project_id"])
    op.create_index("ix_project_import_jobs_tenant_id", "project_import_jobs", ["tenant_id"])
    op.create_index(
        "uq_project_import_jobs_active_project",
        "project_import_jobs",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.create_table(
        "compile_job_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("project_asset_id", sa.Uuid(), nullable=False),
        sa.Column("logical_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("object_bucket", sa.String(length=255), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("byte_size >= 0", name="ck_compile_job_assets_byte_size_nonnegative"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_compile_job_assets_sha256"),
        sa.ForeignKeyConstraint(
            ["compile_job_id", "project_id", "tenant_id"],
            ["compile_jobs.id", "compile_jobs.project_id", "compile_jobs.tenant_id"],
            name="fk_compile_job_assets_compile_job_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_asset_id", "project_id", "tenant_id"],
            [
                "project_assets.id",
                "project_assets.project_id",
                "project_assets.tenant_id",
            ],
            name="fk_compile_job_assets_project_asset_scope",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "compile_job_id",
            "logical_filename",
            name="uq_compile_job_assets_job_filename",
        ),
    )
    op.create_index(
        "ix_compile_job_assets_compile_job_id",
        "compile_job_assets",
        ["compile_job_id"],
    )
    op.create_index(
        "ix_compile_job_assets_project_asset_id",
        "compile_job_assets",
        ["project_asset_id"],
    )
    op.create_index("ix_compile_job_assets_project_id", "compile_job_assets", ["project_id"])
    op.create_index("ix_compile_job_assets_tenant_id", "compile_job_assets", ["tenant_id"])
    op.execute(
        """
        CREATE OR REPLACE FUNCTION tertius_reject_compile_job_asset_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'compile_job_assets rows are immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_compile_job_assets_immutable
        BEFORE UPDATE ON compile_job_assets
        FOR EACH ROW
        EXECUTE FUNCTION tertius_reject_compile_job_asset_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_compile_job_assets_immutable ON compile_job_assets")
    op.drop_index("ix_compile_job_assets_tenant_id", table_name="compile_job_assets")
    op.drop_index("ix_compile_job_assets_project_id", table_name="compile_job_assets")
    op.drop_index("ix_compile_job_assets_project_asset_id", table_name="compile_job_assets")
    op.drop_index("ix_compile_job_assets_compile_job_id", table_name="compile_job_assets")
    op.drop_table("compile_job_assets")
    op.execute("DROP FUNCTION tertius_reject_compile_job_asset_update()")
    op.drop_index("uq_project_import_jobs_active_project", table_name="project_import_jobs")
    op.drop_index("ix_project_import_jobs_tenant_id", table_name="project_import_jobs")
    op.drop_index("ix_project_import_jobs_project_id", table_name="project_import_jobs")
    op.drop_table("project_import_jobs")
    op.execute("DROP TRIGGER trg_project_assets_immutable ON project_assets")
    op.drop_index("ix_project_assets_tenant_id", table_name="project_assets")
    op.drop_index("ix_project_assets_project_id", table_name="project_assets")
    op.drop_table("project_assets")
    op.execute("DROP FUNCTION tertius_reject_project_asset_update()")
