import sqlalchemy as sa
from alembic import op


revision = "0001_initial_multitenant_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("keycloak_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320)),
        sa.Column("username", sa.String(length=255)),
        sa.Column("display_name", sa.String(length=255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("keycloak_subject", name="uq_app_users_keycloak_subject"),
    )
    op.create_index("ix_app_users_keycloak_subject", "app_users", ["keycloak_subject"])
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_user"),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_project_name_per_tenant"),
        sa.UniqueConstraint("id", "tenant_id", name="uq_projects_id_tenant"),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_table(
        "project_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "filename", name="uq_project_file_name"),
    )
    op.create_index("ix_project_files_tenant_id", "project_files", ["tenant_id"])
    op.create_index("ix_project_files_project_id", "project_files", ["project_id"])
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_source_snapshots_tenant_id", "source_snapshots", ["tenant_id"])
    op.create_index("ix_source_snapshots_project_id", "source_snapshots", ["project_id"])
    op.create_table(
        "source_snapshot_files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("source_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index("ix_source_snapshot_files_snapshot_id", "source_snapshot_files", ["snapshot_id"])
    op.create_table(
        "user_workspace_state",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("active_project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("active_file_id", sa.Uuid(), sa.ForeignKey("project_files.id", ondelete="SET NULL")),
        sa.UniqueConstraint("user_id", "tenant_id", name="uq_workspace_user_tenant"),
    )
    op.create_index("ix_user_workspace_state_user_id", "user_workspace_state", ["user_id"])
    op.create_index("ix_user_workspace_state_tenant_id", "user_workspace_state", ["tenant_id"])
    op.create_table(
        "compile_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("app_users.id"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("export_format", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_compile_jobs_tenant_id", "compile_jobs", ["tenant_id"])
    op.create_index("ix_compile_jobs_project_id", "compile_jobs", ["project_id"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("compile_job_id", sa.Uuid(), sa.ForeignKey("compile_jobs.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_artifacts_tenant_id", "artifacts", ["tenant_id"])
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_table(
        "timus_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("stamp_text", sa.String(length=32), nullable=False),
        sa.Column("show_redline", sa.Boolean(), nullable=False),
        sa.Column("show_hidden_lines", sa.Boolean(), nullable=False),
        sa.Column("scale", sa.Numeric(12, 6), nullable=False),
        sa.Column("sheet_size", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(["project_id", "tenant_id"], ["projects.id", "projects.tenant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "tenant_id", "project_id", name="uq_timus_settings_user_tenant_project"),
    )
    op.create_index("ix_timus_settings_user_id", "timus_settings", ["user_id"])
    op.create_index("ix_timus_settings_tenant_id", "timus_settings", ["tenant_id"])
    op.create_index("ix_timus_settings_project_id", "timus_settings", ["project_id"])


def downgrade() -> None:
    op.drop_table("timus_settings")
    op.drop_table("artifacts")
    op.drop_table("compile_jobs")
    op.drop_table("user_workspace_state")
    op.drop_table("source_snapshot_files")
    op.drop_table("source_snapshots")
    op.drop_table("project_files")
    op.drop_table("projects")
    op.drop_table("tenant_memberships")
    op.drop_table("tenants")
    op.drop_table("app_users")
