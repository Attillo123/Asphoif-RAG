from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode, api_response
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.ingestion.embeddings import QwenEmbeddingClient
from app.models.user import User
from app.retrieval.opensearch import (
    DenseRetriever,
    OpenSearchRequestError,
    SparseRetriever,
    build_access_filters,
    retrieve_parallel,
)
from app.schemas.retrieval import RetrievalHitResponse, RetrievalRequest
from app.services.knowledge import get_visible_knowledge_base

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


def _retriever(settings: Settings, cls: type[DenseRetriever | SparseRetriever]):
    password = (
        settings.opensearch_password.get_secret_value()
        if settings.opensearch_password
        else None
    )
    return cls(
        base_url=settings.opensearch_url,
        username=settings.opensearch_username,
        password=password,
        verify_ssl=settings.opensearch_verify_ssl,
        timeout_seconds=settings.retrieval_timeout_seconds,
        read_alias=settings.opensearch_read_alias,
    )


@router.post("/search", response_model=dict[str, Any], summary="Dense/Sparse 并行检索")
async def search(
    payload: RetrievalRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    knowledge_base = await get_visible_knowledge_base(session, user, payload.knowledge_base_id)
    filters = build_access_filters(
        user_id=user.id,
        role=user.role,
        knowledge_base_id=knowledge_base.id,
        knowledge_base_version=payload.knowledge_base_version,
    )
    dense_k = payload.dense_k or settings.dense_k
    sparse_k = payload.sparse_k or settings.sparse_k
    embedding = QwenEmbeddingClient(
        base_url=settings.qwen_embedding_base_url,
        api_key=settings.qwen_embedding_api_key.get_secret_value(),
        model=settings.qwen_embedding_model,
        dimension=settings.qwen_embedding_dimension,
        timeout_seconds=settings.retrieval_timeout_seconds,
    )
    try:
        vectors = await embedding.embed([payload.query])
        result = await retrieve_parallel(
            _retriever(settings, SparseRetriever),
            _retriever(settings, DenseRetriever),
            payload.query,
            vectors[0],
            sparse_k=sparse_k,
            dense_k=dense_k,
            filters=filters,
        )
    except OpenSearchRequestError as exc:
        code = (
            ErrorCode.RETRIEVAL_TIMEOUT
            if "timed out" in str(exc)
            else ErrorCode.RETRIEVAL_UNAVAILABLE
        )
        raise AppError(
            code=code,
            message="检索服务暂时不可用",
            error_type=(
                "RETRIEVAL_TIMEOUT"
                if code == ErrorCode.RETRIEVAL_TIMEOUT
                else "RETRIEVAL_UNAVAILABLE"
            ),
            status_code=503,
            retryable=True,
        ) from exc
    except Exception as exc:
        raise AppError(
            code=ErrorCode.EMBEDDING_UNAVAILABLE,
            message="Embedding 服务暂时不可用",
            error_type="EMBEDDING_UNAVAILABLE",
            status_code=503,
            retryable=True,
        ) from exc
    return api_response(
        success=True,
        code=0,
        message="ok",
        data={
            "query": payload.query,
            "retrieval_version": settings.retrieval_version,
            "dense_k": dense_k,
            "sparse_k": sparse_k,
            "filters": filters,
            "dense": [
                RetrievalHitResponse.model_validate(hit.__dict__).model_dump()
                for hit in result.dense
            ],
            "sparse": [
                RetrievalHitResponse.model_validate(hit.__dict__).model_dump()
                for hit in result.sparse
            ],
        },
    )
