from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import CacheUnavailable, RedisCache, cache_hash, normalize_query
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode, api_response
from app.core.request_id import get_request_id
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.ingestion.embeddings import EmbeddingDimensionError, QwenEmbeddingClient
from app.models.trace import QueryTrace
from app.models.user import User
from app.retrieval.circuit import CircuitOpenError, get_circuit_registry
from app.retrieval.deadline import Deadline
from app.retrieval.opensearch import (
    DenseRetriever,
    SearchHit,
    SparseRetriever,
    build_access_filters,
)
from app.retrieval.service import AllRetrieversFailed, RetrievalOutcome, retrieve_resilient
from app.schemas.retrieval import RetrievalRequest
from app.services.knowledge import get_visible_knowledge_base

router = APIRouter(prefix="/api/v1/retrieval", tags=["retrieval"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _retriever(settings: Settings, cls: type[DenseRetriever | SparseRetriever]):
    password = None
    if settings.opensearch_password:
        password = settings.opensearch_password.get_secret_value()
    return cls(
        base_url=settings.opensearch_url,
        username=settings.opensearch_username,
        password=password,
        verify_ssl=settings.opensearch_verify_ssl,
        timeout_seconds=settings.retrieval_timeout_seconds,
        read_alias=settings.opensearch_read_alias,
    )


def _hit(hit: SearchHit) -> dict[str, Any]:
    return {
        "chunk_id": hit.chunk_id,
        "score": hit.score,
        "rank": hit.rank,
        "source": hit.source,
    }


def _outcome_payload(outcome: RetrievalOutcome) -> dict[str, Any]:
    return {
        "dense": [_hit(hit) for hit in outcome.dense],
        "sparse": [_hit(hit) for hit in outcome.sparse],
        "fused": [
            {
                "chunk_id": hit.chunk_id,
                "score": hit.score,
                "rank": hit.rank,
                "channels": hit.channels,
                "source": hit.source,
                "dense_score": hit.dense_score,
                "sparse_score": hit.sparse_score,
                "dense_rank": hit.dense_rank,
                "sparse_rank": hit.sparse_rank,
            }
            for hit in outcome.fused
        ],
    }


def _stage(
    name: str,
    status: str,
    started: float,
    deadline: Deadline,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "started_at": extra.pop("started_at", _now_iso()),
        "ended_at": _now_iso(),
        "timeout_ms": extra.pop("timeout_ms", None),
        "deadline_remaining_ms": deadline.remaining_ms,
        "retry_count": 0,
        "circuit_state": extra.pop("circuit_state", None),
        "fallback": extra.pop("fallback", None),
        "error_code": extra.pop("error_code", None),
        "metadata": extra,
    }


def _timeout_summary(
    deadline: Deadline, stages: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    exceeded = deadline.remaining_seconds <= 0
    timeout_stages = [
        stage["name"]
        for stage in stages or []
        if stage.get("error_code") in {"EMBEDDING_TIMEOUT", "RETRIEVAL_TIMEOUT"}
        or stage.get("status") == "timeout"
    ]
    if exceeded and "retrieval" not in timeout_stages:
        timeout_stages.append("retrieval")
    return {
        "timed_out": bool(timeout_stages),
        "timeout_stages": list(dict.fromkeys(timeout_stages)),
        "deadline_exceeded": exceeded,
        "circuit_opened_dependencies": [],
    }


async def _write_trace(
    session: AsyncSession,
    *,
    user: User,
    knowledge_base_id: str,
    knowledge_base_version: str,
    query_hash: str,
    started: datetime,
    deadline: Deadline,
    manifest: dict[str, Any],
    status: str,
    cache_hit: bool,
    reasons: list[str],
    stages: list[dict[str, Any]],
    timeout_summary: dict[str, Any],
    retrieval_snapshot: dict[str, Any] | None,
    error_code: str | None = None,
) -> None:
    ended = datetime.now(timezone.utc).replace(tzinfo=None)
    request_id = get_request_id() or f"req_{cache_hash([started.isoformat(), query_hash])[:32]}"
    session.add(
        QueryTrace(
            request_id=request_id,
            trace_schema_version="v1",
            trace_type="online",
            user_id=user.id,
            role=user.role,
            knowledge_base_id=knowledge_base_id,
            knowledge_base_version=knowledge_base_version,
            query_hash=query_hash,
            status=status,
            cache_hit=cache_hit,
            degraded=bool(reasons),
            deadline_ms=deadline.total_ms,
            deadline_started_at=started,
            deadline_ended_at=ended,
            timeout_at=ended if timeout_summary["timed_out"] else None,
            degraded_at=ended if reasons else None,
            total_latency_ms=int((ended - started).total_seconds() * 1000),
            error_code=error_code,
            manifest=manifest,
            stages=stages,
            timeout_summary=timeout_summary,
            retrieval_snapshot=retrieval_snapshot,
            degraded_reasons=list(dict.fromkeys(reasons)),
            created_at=started,
        )
    )
    try:
        await session.commit()
    except Exception:
        # Trace persistence must not hide the retrieval outcome.
        await session.rollback()


def _manifest(
    settings: Settings,
    *,
    dense_k: int,
    sparse_k: int,
    final_top_k: int,
) -> dict[str, Any]:
    return {
        "parser_version": "unknown",
        "chunk_strategy_version": "unknown",
        "embedding_model": settings.qwen_embedding_model,
        "embedding_dimension": settings.qwen_embedding_dimension,
        "index_version": settings.opensearch_index,
        "retrieval_version": settings.retrieval_version,
        "fusion_strategy": "rrf-v1",
        "top_k": final_top_k,
        "dense_k": dense_k,
        "sparse_k": sparse_k,
        "rerank_model": settings.rerank_model,
        "prompt_version": "not-applicable",
        "chat_model": settings.chat_model,
    }


@router.post("/search", response_model=dict[str, Any], summary="并行检索、RRF 融合和 Redis 缓存")
async def search(
    payload: RetrievalRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    deadline = Deadline(settings.retrieval_timeout_seconds)
    knowledge_base = await get_visible_knowledge_base(session, user, payload.knowledge_base_id)
    normalized_query = normalize_query(payload.query)
    dense_k = payload.dense_k or settings.dense_k
    sparse_k = payload.sparse_k or settings.sparse_k
    final_top_k = settings.final_top_k
    kb_version = payload.knowledge_base_version or knowledge_base.current_version or "1"
    filters = build_access_filters(
        user_id=user.id,
        role=user.role,
        knowledge_base_id=knowledge_base.id,
        knowledge_base_version=kb_version,
    )
    query_hash = hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()
    manifest = _manifest(settings, dense_k=dense_k, sparse_k=sparse_k, final_top_k=final_top_k)
    cache = RedisCache(settings.redis_url, environment=settings.app_env)
    cache_parameters = {
        "owner_id": knowledge_base.owner_id,
        "role": user.role,
        "knowledge_base_id": knowledge_base.id,
        "knowledge_base_version": kb_version,
        "permission_version": "v1",
        "query": normalized_query,
        "filters": filters,
        "embedding_model": settings.qwen_embedding_model,
        "index_version": settings.opensearch_index,
        "retrieval_version": settings.retrieval_version,
        "dense_k": dense_k,
        "sparse_k": sparse_k,
        "final_top_k": final_top_k,
    }
    cache_key = cache.key(
        "retrieve",
        "v2",  # Excludes vectors; do not reuse v1 payloads containing content_vector.
        f"{user.role}:{knowledge_base.owner_id}",
        cache_parameters,
    )
    reasons: list[str] = []
    try:
        try:
            cached = await cache.get_json(cache_key)
        except CacheUnavailable:
            cached = None
            reasons.append("CACHE_UNAVAILABLE")
        if cached is not None:
            await _write_trace(
                session,
                user=user,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_version=kb_version,
                query_hash=query_hash,
                started=started,
                deadline=deadline,
                manifest=manifest,
                status="degraded" if reasons else "completed",
                cache_hit=True,
                reasons=reasons,
                stages=[],
                timeout_summary=_timeout_summary(deadline),
                retrieval_snapshot=cached,
            )
            return api_response(
                success=True,
                code=0,
                message="ok",
                data={
                    **cached,
                    "status": "degraded" if reasons else "completed",
                    "degraded_reasons": reasons,
                    "cache_hit": True,
                    "dense_k": dense_k,
                    "sparse_k": sparse_k,
                    "final_top_k": final_top_k,
                },
            )

        # Single-flight the cache rebuild. A contending request waits briefly
        # for the owner to populate the key, then continues without Redis if
        # the owner did not finish in time.
        lock_token = None
        try:
            lock_token = await cache.acquire_lock(cache_key)
            if lock_token is None:
                cached = await cache.wait_for_json(cache_key)
                if cached is not None:
                    return api_response(
                        success=True,
                        code=0,
                        message="ok",
                        data={
                            **cached,
                            "status": "completed",
                            "degraded_reasons": [],
                            "cache_hit": True,
                            "dense_k": dense_k,
                            "sparse_k": sparse_k,
                            "final_top_k": final_top_k,
                        },
                    )
        except CacheUnavailable:
            reasons.append("CACHE_UNAVAILABLE")

        registry = await get_circuit_registry(
            failure_threshold=settings.circuit_failure_threshold,
            failure_window_seconds=settings.circuit_failure_window_seconds,
            open_seconds=settings.circuit_open_seconds,
            half_open_max_calls=settings.circuit_half_open_max_calls,
        )
        vector, embedding_stage = await _get_embedding(
            cache,
            registry,
            normalized_query,
            settings,
            deadline,
            reasons,
        )
        outcome = await retrieve_resilient(
            _retriever(settings, SparseRetriever),
            _retriever(settings, DenseRetriever),
            normalized_query,
            vector,
            sparse_k=sparse_k,
            dense_k=dense_k,
            final_top_k=final_top_k,
            filters=filters,
            deadline=deadline,
            registry=registry,
        )
        reasons.extend(outcome.degraded_reasons)
        reasons = list(dict.fromkeys(reasons))
        outcome.degraded_reasons = reasons
        cached_payload = _outcome_payload(outcome)
        try:
            await cache.set_json(cache_key, cached_payload, settings.retrieval_cache_ttl_seconds)
        except CacheUnavailable:
            reasons = list(dict.fromkeys([*reasons, "CACHE_UNAVAILABLE"]))
            outcome.degraded_reasons = reasons
        snapshot = {
            "query": payload.query,
            "rewritten_query": normalized_query,
            "candidates": [
                {
                    "chunk_id": hit.chunk_id,
                    "rank": hit.rank,
                    "score": hit.score,
                    "channels": hit.channels,
                    # Persist the exact text used to build the Chat prompt so
                    # evaluation (RAGAS) can score faithfulness and context
                    # precision/recall from the online Trace alone.
                    "content": hit.source.get("content", ""),
                    "document_id": hit.source.get("document_id"),
                    "file_name": hit.source.get("file_name"),
                }
                for hit in outcome.fused
            ],
        }
        await _write_trace(
            session,
            user=user,
            knowledge_base_id=knowledge_base.id,
            knowledge_base_version=kb_version,
            query_hash=query_hash,
            started=started,
            deadline=deadline,
            manifest=manifest,
            status="degraded" if reasons else "completed",
            cache_hit=False,
            reasons=reasons,
            stages=[embedding_stage, *outcome.stages],
            timeout_summary={
                **outcome.timeout_summary,
                **_timeout_summary(deadline, [embedding_stage, *outcome.stages]),
                "timeout_stages": list(
                    dict.fromkeys(
                        [
                            *outcome.timeout_summary.get("timeout_stages", []),
                            *_timeout_summary(
                                deadline, [embedding_stage, *outcome.stages]
                            )["timeout_stages"],
                        ]
                    )
                ),
            },
            retrieval_snapshot=snapshot,
        )
        return api_response(
            success=True,
            code=0,
            message="ok",
            data={
                **cached_payload,
                "query": payload.query,
                "retrieval_version": settings.retrieval_version,
                "dense_k": dense_k,
                "sparse_k": sparse_k,
                "final_top_k": final_top_k,
                "filters": filters,
                "status": "degraded" if reasons else "completed",
                "degraded_reasons": reasons,
                "cache_hit": False,
            },
        )
    except AllRetrieversFailed as exc:
        error_code = (
            ErrorCode.RETRIEVAL_TIMEOUT
            if exc.timed_out
            else ErrorCode.RETRIEVAL_UNAVAILABLE
        )
        error_type = (
            "RETRIEVAL_TIMEOUT" if exc.timed_out else "RETRIEVAL_UNAVAILABLE"
        )
        await _write_trace(
            session,
            user=user,
            knowledge_base_id=knowledge_base.id,
            knowledge_base_version=kb_version,
            query_hash=query_hash,
            started=started,
            deadline=deadline,
            manifest=manifest,
            status="failed",
            cache_hit=False,
            reasons=reasons,
            stages=[],
            timeout_summary=_timeout_summary(deadline),
            retrieval_snapshot=None,
            error_code=error_type,
        )
        raise AppError(
            error_code,
            "检索服务暂时不可用",
            error_type,
            503,
            True,
        ) from exc
    finally:
        if "lock_token" in locals() and lock_token is not None:
            try:
                await cache.release_lock(cache_key, lock_token)
            except CacheUnavailable:
                pass
        await cache.close()


async def _get_embedding(
    cache: RedisCache,
    registry: Any,
    query: str,
    settings: Settings,
    deadline: Deadline,
    reasons: list[str],
) -> tuple[list[float] | None, dict[str, Any]]:
    started = time.monotonic()
    started_at = _now_iso()
    cache_hit = False
    vector: list[float] | None = None
    breaker = await registry.get("qwen.embedding")
    try:
        await breaker.before_call()
        parameters = {
            "model": settings.qwen_embedding_model,
            "dimension": settings.qwen_embedding_dimension,
            "query": query,
        }
        key = cache.key("emb", "v1", "global", parameters)
        try:
            cached = await cache.get_json(key)
        except CacheUnavailable:
            cached = None
            reasons.append("CACHE_UNAVAILABLE")
        if cached is not None:
            vector = cached["vector"]
            cache_hit = True
        elif deadline.remaining_seconds > 0:
            client = QwenEmbeddingClient(
                base_url=settings.qwen_embedding_base_url,
                api_key=settings.qwen_embedding_api_key.get_secret_value(),
                model=settings.qwen_embedding_model,
                dimension=settings.qwen_embedding_dimension,
                timeout_seconds=min(settings.embedding_timeout_seconds, deadline.remaining_seconds),
            )
            vector = (
                await asyncio.wait_for(
                    client.embed([query]), timeout=deadline.remaining_seconds
                )
            )[0]
            try:
                await cache.set_json(
                    key,
                    {
                        "model": settings.qwen_embedding_model,
                        "dimension": settings.qwen_embedding_dimension,
                        "vector": vector,
                    },
                    settings.embedding_cache_ttl_seconds,
                )
            except CacheUnavailable:
                reasons.append("CACHE_UNAVAILABLE")
        await breaker.record_success()
    except CircuitOpenError:
        reasons.append("EMBEDDING_UNAVAILABLE")
    except (asyncio.TimeoutError, EmbeddingDimensionError):
        await breaker.record_failure()
        reasons.append("EMBEDDING_TIMEOUT")
    except Exception:
        await breaker.record_failure()
        reasons.append("EMBEDDING_UNAVAILABLE")
    return vector, _stage(
        "embedding",
        "completed" if vector is not None else "degraded",
        started,
        deadline,
        started_at=started_at,
        timeout_ms=int(settings.embedding_timeout_seconds * 1000),
        error_code=None if vector is not None else reasons[-1],
        fallback="sparse" if vector is None else None,
        cache_hit=cache_hit,
    )
