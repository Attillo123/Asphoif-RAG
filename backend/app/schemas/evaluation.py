from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DatasetCaseInput(BaseModel):
    question: str = Field(min_length=1)
    reference_answer: str | None = None
    gold_chunk_ids: list[str] = Field(default_factory=list)
    question_type: str = "fact"
    is_unanswerable: bool = False
    requires_citation: bool = True
    allowed_answers: list[str] | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


class DatasetSnapshotCreate(BaseModel):
    dataset_id: str | None = None
    snapshot_version: str = "1"
    name: str = Field(min_length=1, max_length=128)
    source_type: str = "manual"
    knowledge_base_id: str | None = None
    knowledge_base_version: str | None = None
    cases: list[DatasetCaseInput] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    snapshot_id: str
    trace_bindings: dict[str, str] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)


class HumanReviewInput(BaseModel):
    scores: dict[str, float | int | str | bool]
    reviewer_note: str | None = None
    rubric_version: str = "v1"

