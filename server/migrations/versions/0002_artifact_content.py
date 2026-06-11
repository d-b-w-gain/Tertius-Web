from alembic import op
import sqlalchemy as sa


revision = "0002_artifact_content"
down_revision = "0001_initial_multitenant_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("artifacts", sa.Column("content", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("artifacts", "content")
