from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class QueryTrace(Base):
    __tablename__ = "query_traces"
    __table_args__ = (
        Index("ix_query_traces_created", "created_at"),
        Index("ix_query_traces_user_created", "user_id", "created_at"),
        Index("ix_query_traces_kb_version", "knowledge_base_id", "knowledge_base_version"),
        Index("ix_query_traces_evaluation", "evaluation_run_id", "evaluation_case_id"),
    )

    request_id: Mapped[str] = mapped_column(String(26), primary_key=True)
    trace_schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    trace_type: Mapped[str] = mapped_column(String(16), nullable=False)
    evaluation_run_id: Mapped[str | None] = mapped_column(String(26))
    evaluation_case_id: Mapped[str | None] = mapped_column(String(26))
    user_id: Mapped[str | None] = mapped_column(String(26))
    role: Mapped[str | None] = mapped_column(String(16))
    conversation_id: Mapped[str | None] = mapped_column(String(26))
    knowledge_base_id: Mapped[str | None] = mapped_column(String(26))
    knowledge_base_version: Mapped[str | None] = mapped_column(String(64))
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deadline_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_started_at: Mapped[datetime] = mapped_column(nullable=False)
    deadline_ended_at: Mapped[datetime | None] = mapped_column()
    timeout_at: Mapped[datetime | None] = mapped_column()
    degraded_at: Mapped[datetime | None] = mapped_column()
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column()
    error_code: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    stages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    timeout_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    retrieval_snapshot: Mapped[dict | None] = mapped_column(JSON)
    output_snapshot: Mapped[dict | None] = mapped_column(JSON)
    degraded_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
