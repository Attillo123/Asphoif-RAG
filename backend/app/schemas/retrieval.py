from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_id: str = Field(min_length=1, max_length=26)
    knowledge_base_version: str | None = Field(default=None, max_length=64)
    dense_k: int | None = Field(default=None, ge=1, le=100)
    sparse_k: int | None = Field(default=None, ge=1, le=100)
    cache_policy: Literal["enabled", "disabled"] = "enabled"


class RetrievalHitResponse(BaseModel):
    chunk_id: str
    score: float
    rank: int
    source: dict[str, Any]


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    knowledge_base_id: str = Field(min_length=1, max_length=26)
    knowledge_base_version: str | None = Field(default=None, max_length=64)
    dense_k: int | None = Field(default=None, ge=1, le=100)
    sparse_k: int | None = Field(default=None, ge=1, le=100)
    conversation_id: str | None = Field(default=None, max_length=64)
    cache_policy: Literal["enabled", "disabled"] = "enabled"
