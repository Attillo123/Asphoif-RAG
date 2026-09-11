from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_version_id: str
    stage: str | None
    status: str
    attempt_count: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    next_retry_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document_id: str
    document_version_id: str
    ingestion_job_id: str
    idempotent: bool
    status: str
