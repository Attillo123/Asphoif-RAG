"""add knowledge base soft delete timestamp

Revision ID: 0002_kb_deleted_at
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_kb_deleted_at"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_bases", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_bases", "deleted_at")
