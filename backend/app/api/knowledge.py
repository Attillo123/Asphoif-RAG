from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import api_response
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.knowledge import (
    DocumentResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseResponse,
)
from app.services.knowledge import (
    create_knowledge_base,
    get_visible_knowledge_base,
    soft_delete_knowledge_base,
    visible_documents,
    visible_knowledge_bases,
)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])


@router.post("/knowledge-bases", response_model=dict[str, Any], summary="创建知识库")
async def create_knowledge_base_api(
    payload: KnowledgeBaseCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    knowledge_base = await create_knowledge_base(session, user, payload.name, payload.description)
    return api_response(
        success=True,
        code=0,
        message="ok",
        data=KnowledgeBaseResponse.model_validate(knowledge_base).model_dump(mode="json"),
    )


@router.get("/knowledge-bases", response_model=dict[str, Any], summary="查询知识库")
async def list_knowledge_bases(
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    result = await session.execute(visible_knowledge_bases(user))
    data = [
        KnowledgeBaseResponse.model_validate(item).model_dump(mode="json")
        for item in result.scalars()
    ]
    return api_response(success=True, code=0, message="ok", data={"items": data})


@router.get(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=dict[str, Any],
    summary="查询知识库详情",
)
async def get_knowledge_base(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    knowledge_base = await get_visible_knowledge_base(session, user, knowledge_base_id)
    return api_response(
        success=True,
        code=0,
        message="ok",
        data=KnowledgeBaseResponse.model_validate(knowledge_base).model_dump(mode="json"),
    )


@router.delete(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=dict[str, Any],
    summary="删除知识库",
)
async def delete_knowledge_base(
    knowledge_base_id: str,
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    knowledge_base = await soft_delete_knowledge_base(session, user, knowledge_base_id)
    return api_response(
        success=True,
        code=0,
        message="知识库已删除",
        data={"id": knowledge_base.id, "status": knowledge_base.status},
    )


@router.get("/documents", response_model=dict[str, Any], summary="查询文档")
async def list_documents(
    knowledge_base_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    result = await session.execute(visible_documents(user, knowledge_base_id))
    data = [
        DocumentResponse.model_validate(item).model_dump(mode="json")
        for item in result.scalars()
    ]
    return api_response(success=True, code=0, message="ok", data={"items": data})
