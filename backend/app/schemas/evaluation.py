from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


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

    @model_validator(mode="before")
    @classmethod
    def validate_fixed_rubric(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("rubric_version") != "human-0-5-v1":
            return data
        expected = {
            "answer_correctness", "answer_completeness", "faithfulness",
            "citation_correctness", "citation_completeness", "overall",
        }
        scores = data.get("scores")
        if not isinstance(scores, dict) or set(scores) != expected:
            raise ValueError("人工评分必须包含量表规定的全部六个评分项")
        if any(type(value) is not int or not 0 <= value <= 5 for value in scores.values()):
            raise ValueError("人工评分必须为 0 到 5 的整数")
        note = data.get("reviewer_note")
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValueError("评审备注必须为不超过 2000 字的文本")
        return data
