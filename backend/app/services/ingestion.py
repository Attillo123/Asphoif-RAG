from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ulid
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.ingestion.chunking import FixedChunker
from app.ingestion.embeddings import EmbeddingClient, EmbeddingDimensionError
from app.ingestion.indexers import ChunkIndexer
from app.ingestion.parsers import (
    ParserNotImplementedError,
    decode_text,
    get_parser,
)
from app.ingestion.storage import FileStorage, safe_file_name
from app.models.knowledge import (
    Chunk,
    Document,
    DocumentVersion,
    IngestionJob,
    KnowledgeBase,
)
from app.models.user import User
from app.services.knowledge import get_visible_knowledge_base

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".doc", ".docx"}
def stable_chunk_id(document_version_id: str, ordinal: int) -> str:
    """Return a deterministic 26-character ID so retries overwrite the same chunk."""
    digest = hashlib.sha256(f"{document_version_id}:{ordinal}".encode()).digest()[:16]
    return str(ulid.from_bytes(digest))


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def create_ingestion_job(
    session: AsyncSession,
    user: User,
    knowledge_base_id: str,
    file_name: str,
    content_type: str | None,
    data: bytes,
    storage: FileStorage,
    settings: Settings,
) -> tuple[Document, DocumentVersion, IngestionJob, bool]:
    knowledge_base = await get_visible_knowledge_base(session, user, knowledge_base_id)
    if not file_name or Path(file_name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise AppError(
            code=ErrorCode.DOCUMENT_TYPE_UNSUPPORTED,
            message="暂不支持该文档类型",
            error_type="DOCUMENT_TYPE_UNSUPPORTED",
            status_code=415,
        )
    if len(data) > settings.ingestion_max_file_size_bytes:
        raise AppError(
            code=ErrorCode.DOCUMENT_TOO_LARGE,
            message="文档超过大小限制",
            error_type="DOCUMENT_TOO_LARGE",
            status_code=413,
        )
    file_name = safe_file_name(file_name)
    digest = content_sha256(data)
    existing = await session.scalar(
        select(Document).where(
            Document.knowledge_base_id == knowledge_base.id,
            Document.content_hash == digest,
            Document.status != "deleted",
        )
    )
    if existing is not None:
        version = await session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == existing.id)
            .order_by(DocumentVersion.version.desc())
        )
        job = await session.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_version_id == version.id)
            .order_by(IngestionJob.created_at.desc())
        ) if version else None
        if version is not None and job is not None:
            return existing, version, job, True

    extension = Path(file_name).suffix.lower().lstrip(".")
    storage_uri = await storage.save(data, f"{user.id}/{knowledge_base.id}/{digest}.{extension}")
    document = Document(
        knowledge_base_id=knowledge_base.id,
        source_uri=storage_uri,
        file_name=file_name,
        mime_type=content_type or "application/octet-stream",
        content_hash=digest,
        status="active",
    )
    session.add(document)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        existing = await session.scalar(
            select(Document).where(
                Document.knowledge_base_id == knowledge_base.id,
                Document.content_hash == digest,
            )
        )
        if existing is None:
            raise exc
        version = await session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == existing.id)
            .order_by(DocumentVersion.version.desc())
        )
        job = await session.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_version_id == version.id)
            .order_by(IngestionJob.created_at.desc())
        ) if version else None
        if version is None or job is None:
            raise exc
        return existing, version, job, True

    version = DocumentVersion(
        document_id=document.id,
        version=1,
        content_hash=digest,
        storage_uri=storage_uri,
        parser_version="pending",
        chunk_strategy_version=FixedChunker.strategy_version,
        status="PENDING",
        metadata_json={"extension": extension},
    )
    session.add(version)
    await session.flush()
    job = IngestionJob(
        document_version_id=version.id,
        stage="PENDING",
        status="PENDING",
        attempt_count=0,
        max_attempts=settings.ingestion_max_attempts,
    )
    session.add(job)
    await session.commit()
    await session.refresh(document)
    await session.refresh(version)
    await session.refresh(job)
    return document, version, job, False


class IngestionFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = True,
        code: str = "INGESTION_FAILED",
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.code = code


async def process_ingestion_job(
    session: AsyncSession,
    job_id: str,
    settings: Settings,
    storage: FileStorage,
    embedding_client: EmbeddingClient,
    indexer: ChunkIndexer,
) -> IngestionJob:
    job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入库任务不存在", "RESOURCE_NOT_FOUND", 404)
    if job.status in {"READY", "PUBLISHED", "FAILED"}:
        return job
    job.attempt_count += 1
    job.worker_id = settings.ingestion_worker_id
    job.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    job.stage = "PARSING"
    job.status = "PARSING"
    await session.commit()
    try:
        version = await session.scalar(
            select(DocumentVersion).where(DocumentVersion.id == job.document_version_id)
        )
        if version is None:
            raise IngestionFailure(
                "document version not found", retryable=False, code="VERSION_NOT_FOUND"
            )
        document = await session.scalar(
            select(Document).where(Document.id == version.document_id)
        )
        knowledge_base = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.id == document.knowledge_base_id)
        ) if document else None
        if document is None or knowledge_base is None or not version.storage_uri:
            raise IngestionFailure("document storage metadata is incomplete", retryable=False)
        raw = await storage.read(version.storage_uri)
        parser = get_parser(document.file_name, document.mime_type)
        if parser.parser_version.endswith("-interface"):
            raise ParserNotImplementedError(
                f"parser for {Path(document.file_name).suffix.lower()} is not implemented"
            )
        parsed = parser.parse(decode_text(raw))
        if not parsed.blocks:
            raise IngestionFailure(
                "document contains no searchable text",
                retryable=False,
                code="EMPTY_DOCUMENT",
            )

        job.stage = "CHUNKING"
        job.status = "CHUNKING"
        version.status = "BUILDING"
        await session.commit()
        chunker = FixedChunker(settings.ingestion_chunk_size, settings.ingestion_chunk_overlap)
        drafts = chunker.chunk(parsed)
        if not drafts:
            raise IngestionFailure(
                "document produced no chunks", retryable=False, code="EMPTY_CHUNKS"
            )
        await session.execute(delete(Chunk).where(Chunk.document_version_id == version.id))
        chunk_rows = [
            Chunk(
                id=stable_chunk_id(version.id, draft.ordinal),
                document_version_id=version.id,
                content_hash=draft.content_hash,
                ordinal=draft.ordinal,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                content=draft.content,
                metadata_json={
                    "document_id": document.id,
                    "knowledge_base_id": knowledge_base.id,
                    "owner_id": knowledge_base.owner_id,
                    "file_name": document.file_name,
                    "section_path": list(draft.section_path),
                    "language": draft.language,
                    "document_version": version.version,
                },
            )
            for draft in drafts
        ]
        session.add_all(chunk_rows)
        version.parser_version = parsed.parser_version
        version.chunk_count = len(chunk_rows)
        await session.flush()

        job.stage = "EMBEDDING"
        job.status = "EMBEDDING"
        await session.commit()
        vectors = await embedding_client.embed([draft.content for draft in drafts])
        if len(vectors) != len(drafts) or any(
            len(vector) != settings.qwen_embedding_dimension for vector in vectors
        ):
            raise IngestionFailure(
                "embedding dimension or count mismatch",
                retryable=False,
                code="EMBEDDING_DIMENSION_INVALID",
            )

        job.stage = "INDEXING"
        job.status = "INDEXING"
        await session.commit()
        await indexer.upsert(
            chunk_ids=[row.id for row in chunk_rows],
            chunks=drafts,
            embeddings=vectors,
            metadata={
                "owner_id": knowledge_base.owner_id,
                "knowledge_base_id": knowledge_base.id,
                "knowledge_base_version": str(knowledge_base.current_version or version.version),
                "document_id": document.id,
                "document_version": version.version,
                "file_name": document.file_name,
            },
        )
        version.status = "READY"
        job.stage = "READY"
        job.status = "READY"
        job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        job.error_code = None
        job.error_message = None
        await session.commit()
    except ParserNotImplementedError as exc:
        await _mark_job_failed(session, job.id, str(exc), "PARSER_NOT_IMPLEMENTED")
    except EmbeddingDimensionError as exc:
        await _mark_job_failed(session, job.id, str(exc), "EMBEDDING_DIMENSION_INVALID")
    except (IngestionFailure, ValueError) as exc:
        retryable = getattr(exc, "retryable", False)
        code = getattr(exc, "code", "INGESTION_FAILED")
        if retryable and job.attempt_count < job.max_attempts:
            await _mark_job_retrying(session, job.id, str(exc), code, settings)
        else:
            await _mark_job_failed(session, job.id, str(exc), code)
    except Exception as exc:
        if job.attempt_count < job.max_attempts:
            await _mark_job_retrying(session, job.id, str(exc), "INGESTION_RETRYABLE", settings)
        else:
            await _mark_job_failed(session, job.id, str(exc), "INGESTION_FAILED")
    await session.refresh(job)
    return job


async def claim_next_ingestion_job(
    session: AsyncSession, settings: Settings
) -> IngestionJob | None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statement = (
        select(IngestionJob)
        .where(
            IngestionJob.status.in_(("PENDING", "RETRY_WAITING")),
            or_(IngestionJob.next_retry_at.is_(None), IngestionJob.next_retry_at <= now),
        )
        .order_by(IngestionJob.created_at)
        .with_for_update(skip_locked=True)
    )
    job = await session.scalar(statement)
    if job is None:
        return None
    job.worker_id = settings.ingestion_worker_id
    job.lease_expires_at = now + timedelta(seconds=300)
    await session.commit()
    return job


async def claim_next_ingestion_job_for_knowledge_base(
    session: AsyncSession, settings: Settings, knowledge_base_id: str
) -> IngestionJob | None:
    """Claim one eligible job for the selected knowledge base.

    This is used by the development console to start one worker iteration while
    preserving the same row-locking and lease semantics as the standalone worker.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    statement = (
        select(IngestionJob)
        .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.status != "deleted",
            IngestionJob.status.in_(("PENDING", "RETRY_WAITING")),
            or_(IngestionJob.next_retry_at.is_(None), IngestionJob.next_retry_at <= now),
        )
        .order_by(IngestionJob.created_at)
        .with_for_update(skip_locked=True)
    )
    job = await session.scalar(statement)
    if job is None:
        return None
    job.worker_id = settings.ingestion_worker_id
    job.lease_expires_at = now + timedelta(seconds=300)
    await session.commit()
    return job


async def reset_ingestion_job_for_retry(
    session: AsyncSession, job_id: str
) -> IngestionJob:
    job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入库任务不存在", "RESOURCE_NOT_FOUND", 404)
    if job.status != "FAILED":
        return job
    job.status = "RETRY_WAITING"
    job.stage = job.stage or "PARSING"
    job.next_retry_at = None
    job.error_code = None
    job.error_message = None
    job.finished_at = None
    await session.commit()
    await session.refresh(job)
    return job


async def reset_ingestion_job_for_reindex(
    session: AsyncSession, job_id: str
) -> IngestionJob:
    """Schedule an already completed job for an explicit index rebuild."""
    job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入库任务不存在", "RESOURCE_NOT_FOUND", 404)
    if job.status not in {"READY", "FAILED", "RETRY_WAITING"}:
        raise AppError(
            ErrorCode.INGESTION_NOT_READY,
            "当前入库任务不允许重建索引",
            "INGESTION_NOT_READY",
            409,
        )
    version = await session.scalar(
        select(DocumentVersion).where(DocumentVersion.id == job.document_version_id)
    )
    if version is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "文档版本不存在", "RESOURCE_NOT_FOUND", 404)
    job.status = "RETRY_WAITING"
    job.stage = "PARSING"
    job.attempt_count = 0
    job.worker_id = None
    job.lease_expires_at = None
    job.next_retry_at = None
    job.error_code = None
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    version.status = "PENDING"
    await session.commit()
    await session.refresh(job)
    return job


async def _mark_job_retrying(
    session: AsyncSession, job_id: str, message: str, code: str, settings: Settings
) -> None:
    job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        return
    job.status = "RETRY_WAITING"
    job.error_code = code
    job.error_message = message[:4000]
    job.next_retry_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        seconds=settings.ingestion_retry_base_seconds * (2 ** max(job.attempt_count - 1, 0))
    )
    await session.commit()


async def _mark_job_failed(session: AsyncSession, job_id: str, message: str, code: str) -> None:
    job = await session.scalar(select(IngestionJob).where(IngestionJob.id == job_id))
    if job is None:
        return
    job.status = "FAILED"
    job.error_code = code
    job.error_message = message[:4000]
    job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    version = await session.scalar(
        select(DocumentVersion).where(DocumentVersion.id == job.document_version_id)
    )
    if version is not None:
        version.status = "FAILED"
    await session.commit()
