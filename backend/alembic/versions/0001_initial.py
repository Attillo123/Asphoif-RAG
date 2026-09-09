"""create phase 1 foundation tables

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("role", sa.Enum("personal", "admin", name="user_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.UniqueConstraint("username", name="uq_users_username"))
    op.create_table("knowledge_bases",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("owner_id", sa.String(26), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False), sa.Column("description", sa.String(500)), sa.Column("current_version", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()))
    op.create_index("ix_knowledge_bases_owner_status", "knowledge_bases", ["owner_id", "status"])
    op.create_table("documents",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("knowledge_base_id", sa.String(26), sa.ForeignKey("knowledge_bases.id"), nullable=False),
        sa.Column("source_uri", sa.String(1024)), sa.Column("file_name", sa.String(255), nullable=False), sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("deleted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.UniqueConstraint("knowledge_base_id", "content_hash", name="uq_documents_kb_content_hash"))
    op.create_index("ix_documents_kb_status", "documents", ["knowledge_base_id", "status"])
    op.create_table("document_versions",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("document_id", sa.String(26), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("storage_uri", sa.String(1024)),
        sa.Column("parser_version", sa.String(32), nullable=False), sa.Column("chunk_strategy_version", sa.String(32), nullable=False), sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"))
    op.create_table("chunks",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("document_version_id", sa.String(26), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("start_offset", sa.Integer()), sa.Column("end_offset", sa.Integer()), sa.Column("content", sa.Text(), nullable=False), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()),
        sa.UniqueConstraint("document_version_id", "ordinal", name="uq_chunks_version_ordinal"))
    op.create_index("ix_chunks_version", "chunks", ["document_version_id"])
    op.create_table("ingestion_jobs",
        sa.Column("id", sa.String(26), primary_key=True), sa.Column("document_version_id", sa.String(26), sa.ForeignKey("document_versions.id"), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("max_attempts", sa.Integer(), nullable=False), sa.Column("error_code", sa.String(64)), sa.Column("error_message", sa.Text()), sa.Column("worker_id", sa.String(128)), sa.Column("lease_expires_at", sa.DateTime()), sa.Column("next_retry_at", sa.DateTime()), sa.Column("started_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()))
    op.create_index("ix_ingestion_jobs_status_next_retry", "ingestion_jobs", ["status", "next_retry_at"])
    op.create_table("evaluation_dataset_snapshots", sa.Column("snapshot_id", sa.String(26), primary_key=True), sa.Column("dataset_id", sa.String(26), nullable=False), sa.Column("snapshot_version", sa.String(32), nullable=False), sa.Column("name", sa.String(128), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("knowledge_base_id", sa.String(26)), sa.Column("knowledge_base_version", sa.String(64)), sa.Column("source_type", sa.String(32), nullable=False), sa.Column("dataset_sha256", sa.String(64), nullable=False), sa.Column("case_count", sa.Integer(), nullable=False), sa.Column("schema_version", sa.String(32), nullable=False), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_by", sa.String(26)), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.UniqueConstraint("dataset_id", "snapshot_version", name="uq_dataset_snapshot_version"))
    op.create_index("ix_dataset_snapshot_kb", "evaluation_dataset_snapshots", ["knowledge_base_id", "knowledge_base_version"])
    op.create_table("evaluation_dataset_cases", sa.Column("case_id", sa.String(26), primary_key=True), sa.Column("snapshot_id", sa.String(26), sa.ForeignKey("evaluation_dataset_snapshots.snapshot_id"), nullable=False), sa.Column("ordinal", sa.Integer(), nullable=False), sa.Column("question", sa.Text(), nullable=False), sa.Column("reference_answer", sa.Text()), sa.Column("gold_chunk_ids", sa.JSON(), nullable=False), sa.Column("question_type", sa.String(32), nullable=False), sa.Column("is_unanswerable", sa.Boolean(), nullable=False), sa.Column("requires_citation", sa.Boolean(), nullable=False), sa.Column("allowed_answers", sa.JSON()), sa.Column("annotations", sa.JSON(), nullable=False), sa.Column("case_sha256", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_snapshot_case_ordinal"))
    op.create_table("evaluation_runs", sa.Column("run_id", sa.String(26), primary_key=True), sa.Column("snapshot_id", sa.String(26), sa.ForeignKey("evaluation_dataset_snapshots.snapshot_id"), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("run_manifest", sa.JSON(), nullable=False), sa.Column("aggregate_metrics", sa.JSON()), sa.Column("error_message", sa.Text()), sa.Column("created_by", sa.String(26)), sa.Column("started_at", sa.DateTime()), sa.Column("finished_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()))
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_table("evaluation_case_results", sa.Column("result_id", sa.String(26), primary_key=True), sa.Column("run_id", sa.String(26), sa.ForeignKey("evaluation_runs.run_id"), nullable=False), sa.Column("case_id", sa.String(26), sa.ForeignKey("evaluation_dataset_cases.case_id"), nullable=False), sa.Column("request_id", sa.String(26)), sa.Column("status", sa.String(16), nullable=False), sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=False), sa.Column("context_snapshot", sa.JSON()), sa.Column("answer", sa.Text()), sa.Column("citations", sa.JSON()), sa.Column("metrics", sa.JSON(), nullable=False), sa.Column("diagnosis", sa.JSON()), sa.Column("latency_ms", sa.Integer()), sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()), sa.Column("cost", sa.Numeric(18, 8)), sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.utc_timestamp()), sa.UniqueConstraint("run_id", "case_id", name="uq_evaluation_run_case"))
    op.create_index("ix_evaluation_case_results_request_id", "evaluation_case_results", ["request_id"])
    op.create_table("query_traces", sa.Column("request_id", sa.String(26), primary_key=True), sa.Column("trace_schema_version", sa.String(32), nullable=False), sa.Column("trace_type", sa.String(16), nullable=False), sa.Column("evaluation_run_id", sa.String(26)), sa.Column("evaluation_case_id", sa.String(26)), sa.Column("user_id", sa.String(26)), sa.Column("role", sa.String(16)), sa.Column("conversation_id", sa.String(26)), sa.Column("knowledge_base_id", sa.String(26)), sa.Column("knowledge_base_version", sa.String(64)), sa.Column("query_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("cache_hit", sa.Boolean(), nullable=False), sa.Column("degraded", sa.Boolean(), nullable=False), sa.Column("deadline_ms", sa.Integer(), nullable=False), sa.Column("deadline_started_at", sa.DateTime(), nullable=False), sa.Column("deadline_ended_at", sa.DateTime()), sa.Column("timeout_at", sa.DateTime()), sa.Column("degraded_at", sa.DateTime()), sa.Column("total_latency_ms", sa.Integer()), sa.Column("first_token_latency_ms", sa.Integer()), sa.Column("input_tokens", sa.Integer()), sa.Column("output_tokens", sa.Integer()), sa.Column("cost", sa.Numeric(18, 8)), sa.Column("error_code", sa.String(64)), sa.Column("manifest", sa.JSON(), nullable=False), sa.Column("stages", sa.JSON(), nullable=False), sa.Column("timeout_summary", sa.JSON(), nullable=False), sa.Column("retrieval_snapshot", sa.JSON()), sa.Column("output_snapshot", sa.JSON()), sa.Column("degraded_reasons", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    for name, table, cols in [("ix_query_traces_created", "query_traces", ["created_at"]), ("ix_query_traces_user_created", "query_traces", ["user_id", "created_at"]), ("ix_query_traces_kb_version", "query_traces", ["knowledge_base_id", "knowledge_base_version"]), ("ix_query_traces_evaluation", "query_traces", ["evaluation_run_id", "evaluation_case_id"])]: op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table in [("ix_query_traces_evaluation", "query_traces"), ("ix_query_traces_kb_version", "query_traces"), ("ix_query_traces_user_created", "query_traces"), ("ix_query_traces_created", "query_traces"), ("ix_evaluation_case_results_request_id", "evaluation_case_results"), ("ix_evaluation_runs_status", "evaluation_runs"), ("ix_dataset_snapshot_kb", "evaluation_dataset_snapshots"), ("ix_chunks_version", "chunks"), ("ix_ingestion_jobs_status_next_retry", "ingestion_jobs"), ("ix_documents_kb_status", "documents"), ("ix_knowledge_bases_owner_status", "knowledge_bases")]: op.drop_index(name, table_name=table)
    for table in ["query_traces", "evaluation_case_results", "evaluation_runs", "evaluation_dataset_cases", "evaluation_dataset_snapshots", "ingestion_jobs", "chunks", "document_versions", "documents", "knowledge_bases", "users"]: op.drop_table(table)
