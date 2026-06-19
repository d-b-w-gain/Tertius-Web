from alembic import op
import sqlalchemy as sa


revision = "0003_compile_job_error_fields"
down_revision = "0002_artifact_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compile_jobs", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("compile_jobs", sa.Column("user_message", sa.Text(), nullable=True))
    op.add_column("compile_jobs", sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("compile_jobs", "retryable", server_default=None)


def downgrade() -> None:
    op.drop_column("compile_jobs", "retryable")
    op.drop_column("compile_jobs", "user_message")
    op.drop_column("compile_jobs", "error_code")
