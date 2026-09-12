from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.ids import new_id


class EvaluationDatasetSnapshot(TimestampMixin, Base):
    __tablename__ = "evaluation_dataset_snapshots"
    __table_args__ = (
        UniqueConstraint("dataset_id", "snapshot_version", name="uq_dataset_snapshot_version"),
        Index("ix_dataset_snapshot_kb", "knowledge_base_id", "knowledge_base_version"),
    )

    id: Mapped[str] = mapped_column("snapshot_id", String(26), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(String(26), nullable=False)
    snapshot_version: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    knowledge_base_id: Mapped[str | None] = mapped_column(String(26))
    knowledge_base_version: Mapped[str | None] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(26))

    cases: Mapped[list[EvaluationDatasetCase]] = relationship(back_populates="snapshot")
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="snapshot")


class EvaluationDatasetCase(TimestampMixin, Base):
    __tablename__ = "evaluation_dataset_cases"
    __table_args__ = (UniqueConstraint("snapshot_id", "ordinal", name="uq_snapshot_case_ordinal"),)

    id: Mapped[str] = mapped_column("case_id", String(26), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_dataset_snapshots.snapshot_id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str | None] = mapped_column(Text)
    gold_chunk_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_unanswerable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_citation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_answers: Mapped[list | None] = mapped_column(JSON)
    annotations: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    case_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    snapshot: Mapped[EvaluationDatasetSnapshot] = relationship(back_populates="cases")


class EvaluationRun(TimestampMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (Index("ix_evaluation_runs_status", "status"),)

    id: Mapped[str] = mapped_column("run_id", String(26), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_dataset_snapshots.snapshot_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    run_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    aggregate_metrics: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(26))
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()

    snapshot: Mapped[EvaluationDatasetSnapshot] = relationship(back_populates="runs")


class EvaluationCaseResult(TimestampMixin, Base):
    __tablename__ = "evaluation_case_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_evaluation_run_case"),)

    id: Mapped[str] = mapped_column("result_id", String(26), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.run_id"), nullable=False)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("evaluation_dataset_cases.case_id"), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    context_snapshot: Mapped[dict | None] = mapped_column(JSON)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list | None] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    diagnosis: Mapped[dict | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column()
    error_message: Mapped[str | None] = mapped_column(Text)
