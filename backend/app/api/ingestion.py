from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode, api_response
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.ingestion.storage import LocalFileStorage
from app.models.knowledge import Document, DocumentVersion, IngestionJob
from app.models.user import User
from app.schemas.ingestion import DocumentUploadResponse, IngestionJobResponse
from app.services.ingestion import create_ingestion_job
from app.services.knowledge import get_visible_knowledge_base

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=dict[str, Any],
    summary="上传文档并创建入库任务",
)
async def upload_document(
    knowledge_base_id: str,
    request: Request,
    file_name: str | None = Query(default=None, min_length=1, max_length=255),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise AppError(
                code=ErrorCode.INVALID_REQUEST,
                message="multipart 请求必须包含 file 文件字段",
                error_type="INVALID_REQUEST",
                status_code=400,
            )
        data = await file.read(settings.ingestion_max_file_size_bytes + 1)
        file_name = getattr(file, "filename", None) or "document"
        file_content_type = getattr(file, "content_type", None)
    else:
        file_name = file_name or "document.txt"
        file_content_type = content_type or "text/plain"
        data = await request.body()
    if len(data) > settings.ingestion_max_file_size_bytes:
        raise AppError(
            code=ErrorCode.DOCUMENT_TOO_LARGE,
            message="文档超过大小限制",
            error_type="DOCUMENT_TOO_LARGE",
            status_code=413,
        )
    document, version, job, idempotent = await create_ingestion_job(
        session,
        user,
        knowledge_base_id,
        file_name,
        file_content_type,
        data,
        LocalFileStorage(settings.local_file_root),
        settings,
    )
    payload = DocumentUploadResponse(
        document_id=document.id,
        document_version_id=version.id,
        ingestion_job_id=job.id,
        idempotent=idempotent,
        status=job.status,
    )
    return api_response(success=True, code=0, message="文档已接收", data=payload.model_dump())


@router.get("/ingestion-jobs/{job_id}", response_model=dict[str, Any], summary="查询入库任务")
async def get_ingestion_job(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    job = await session.scalar(
        select(IngestionJob).where(IngestionJob.id == job_id)
    )
    if job is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入库任务不存在", "RESOURCE_NOT_FOUND", 404)
    await get_visible_knowledge_base_for_job(session, user, job.document_version_id)
    return api_response(
        success=True,
        code=0,
        message="ok",
        data=IngestionJobResponse.model_validate(job).model_dump(mode="json"),
    )


@router.get("/knowledge-bases/{knowledge_base_id}/ingestion-jobs", response_model=dict[str, Any], summary="查询知识库入库任务")
async def list_ingestion_jobs(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    await get_visible_knowledge_base(session, user, knowledge_base_id)
    statement = (
        select(IngestionJob, DocumentVersion.document_id)
        .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.knowledge_base_id == knowledge_base_id, Document.status != "deleted")
        .order_by(desc(IngestionJob.created_at))
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    items = []
    for job, document_id in rows:
        items.append({
            "ingestion_job_id": job.id,
            "document_id": document_id,
            "document_version_id": job.document_version_id,
            "status": job.status,
            "stage": job.stage,
            "error": job.error_message,
            "error_code": job.error_code,
            "attempt_count": job.attempt_count,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        })
    return api_response(success=True, code=0, message="ok", data={"items": items})


async def get_visible_knowledge_base_for_job(
    session: AsyncSession, user: User, document_version_id: str
) -> None:
    from app.models.knowledge import Document, DocumentVersion

    statement = (
        select(Document.knowledge_base_id)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.id == document_version_id)
    )
    knowledge_base_id = await session.scalar(statement)
    if knowledge_base_id is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入库任务不存在", "RESOURCE_NOT_FOUND", 404)
    await get_visible_knowledge_base(session, user, knowledge_base_id)
