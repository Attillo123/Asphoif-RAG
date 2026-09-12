from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.retrieval import search as retrieval_search
from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode
from app.core.request_id import get_request_id
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.retrieval import ChatRequest, RetrievalRequest

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _sse(event: str, seq: int, payload: dict[str, Any]) -> str:
    body = json.dumps({"seq": seq, **payload}, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n"


def _context(result: dict[str, Any], max_chars: int) -> tuple[str, list[dict[str, Any]]]:
    citations: list[dict[str, Any]] = []
    parts: list[str] = []
    used = 0
    for item in result.get("data", {}).get("fused", []):
        source = item.get("source", {})
        content = str(source.get("content", "")).strip()
        if not content:
            continue
        chunk_id = str(item.get("chunk_id", ""))
        citation = {
            "chunk_id": chunk_id,
            "document_id": source.get("document_id"),
            "file_name": source.get("metadata", {}).get("file_name"),
            "rank": item.get("rank"),
        }
        prefix = f"[{len(citations) + 1}] "
        remaining = max_chars - used - len(prefix)
        if remaining <= 0:
            break
        content = content[:remaining]
        parts.append(prefix + content)
        citations.append(citation)
        used += len(prefix) + len(content)
    return "\n\n".join(parts), citations


async def _upstream_events(
    settings: Settings, messages: list[dict[str, str]], total_timeout: float
) -> AsyncIterator[str]:
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key.get_secret_value()}",
        "Accept": "text/event-stream",
    }
    body = {"model": settings.chat_model, "messages": messages, "stream": True}
    async with httpx.AsyncClient(timeout=httpx.Timeout(total_timeout)) as client:
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.is_error:
                detail = (await response.aread()).decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"chat provider returned HTTP {response.status_code}: {detail}")
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices") or []
                    # OpenAI-compatible providers may send a final usage-only
                    # chunk with choices=[] before [DONE].
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}).get("content")
                    if delta:
                        yield delta


@router.post("/completions", summary="基于检索上下文的 SSE 问答")
async def completions(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    trace_id = get_request_id() or f"req_{uuid.uuid4().hex}"
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    started = time.monotonic()

    async def stream() -> AsyncIterator[str]:
        seq = 0
        answer: list[str] = []
        try:
            retrieval = await retrieval_search(
                RetrievalRequest(
                    query=payload.query,
                    knowledge_base_id=payload.knowledge_base_id,
                    knowledge_base_version=payload.knowledge_base_version,
                    dense_k=payload.dense_k,
                    sparse_k=payload.sparse_k,
                ),
                session=session,
                user=user,
                settings=settings,
            )
            context, citations = _context(retrieval, settings.chat_context_max_chars)
            seq += 1
            yield _sse(
                "start",
                seq,
                {
                    "trace_id": trace_id,
                    "request_id": trace_id,
                    "conversation_id": conversation_id,
                    "model": settings.chat_model,
                    "prompt_version": settings.prompt_version,
                    "retrieval_status": retrieval["data"]["status"],
                },
            )
            for citation in citations:
                seq += 1
                yield _sse("citation", seq, citation)
            if not context:
                context = "未检索到可用上下文。请明确说明无法从知识库中找到依据。"
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是企业知识库问答助手。仅依据给定上下文回答；"
                        "如果上下文不足，请明确说明。引用使用 [1]、[2] 等编号。"
                    ),
                },
                {"role": "user", "content": f"上下文：\n{context}\n\n问题：{payload.query}"},
            ]
            # Start the first-token clock when the Chat provider request starts,
            # rather than including retrieval and context construction latency.
            chat_started = time.monotonic()
            first_delta = True
            async for delta in _upstream_events(
                settings, messages, settings.chat_total_timeout_seconds
            ):
                if (
                    first_delta
                    and time.monotonic() - chat_started
                    > settings.chat_first_token_timeout_seconds
                ):
                    raise TimeoutError("chat first token timeout")
                first_delta = False
                answer.append(delta)
                seq += 1
                yield _sse("delta", seq, {"text": delta})
            seq += 1
            yield _sse(
                "end",
                seq,
                {
                    "trace_id": trace_id,
                    "conversation_id": conversation_id,
                    "answer": "".join(answer),
                    "citations": citations,
                    "status": retrieval["data"]["status"],
                    "degraded_reasons": retrieval["data"].get("degraded_reasons", []),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            seq += 1
            yield _sse(
                "error",
                seq,
                {
                    "trace_id": trace_id,
                    "code": ErrorCode.INTERNAL_ERROR,
                    "type": "CHAT_GENERATION_FAILED",
                    "message": str(exc)[:500],
                    "retryable": True,
                },
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
