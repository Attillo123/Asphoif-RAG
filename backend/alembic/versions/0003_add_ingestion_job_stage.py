"""add current stage to ingestion jobs

Revision ID: 0003_ingestion_job_stage
Revises: 0002_kb_deleted_at
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_ingestion_job_stage"
down_revision = "0002_kb_deleted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("stage", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "stage")
