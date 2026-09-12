"""widen request id columns for end-to-end correlation

Revision ID: 0004_widen_trace_request_id
Revises: 0003_ingestion_job_stage
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_widen_trace_request_id"
down_revision = "0003_ingestion_job_stage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL MODIFY must preserve nullability, especially for the trace primary key.
    op.alter_column(
        "query_traces",
        "request_id",
        existing_type=sa.String(26),
        existing_nullable=False,
        type_=sa.String(64),
    )
    op.alter_column(
        "evaluation_case_results",
        "request_id",
        existing_type=sa.String(26),
        existing_nullable=True,
        type_=sa.String(64),
    )


def downgrade() -> None:
    op.alter_column(
        "evaluation_case_results",
        "request_id",
        existing_type=sa.String(64),
        existing_nullable=True,
        type_=sa.String(26),
    )
    op.alter_column(
        "query_traces",
        "request_id",
        existing_type=sa.String(64),
        existing_nullable=False,
        type_=sa.String(26),
    )
