from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_id: str = Field(min_length=1, max_length=26)
    knowledge_base_version: str | None = Field(default=None, max_length=64)
    dense_k: int | None = Field(default=None, ge=1, le=100)
    sparse_k: int | None = Field(default=None, ge=1, le=100)


class RetrievalHitResponse(BaseModel):
    chunk_id: str
    score: float
    rank: int
    source: dict[str, Any]
