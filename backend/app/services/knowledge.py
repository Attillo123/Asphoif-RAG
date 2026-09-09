from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.knowledge import Document, KnowledgeBase
from app.models.user import User


def visible_knowledge_bases(user: User) -> Select[tuple[KnowledgeBase]]:
    statement = select(KnowledgeBase).where(KnowledgeBase.status != "deleted")
    if user.role != "admin":
        statement = statement.where(KnowledgeBase.owner_id == user.id)
    return statement.order_by(KnowledgeBase.created_at.desc())


async def get_visible_knowledge_base(
    session: AsyncSession, user: User, knowledge_base_id: str
) -> KnowledgeBase:
    statement = visible_knowledge_bases(user).where(KnowledgeBase.id == knowledge_base_id)
    result = await session.execute(statement)
    knowledge_base = result.scalar_one_or_none()
    if knowledge_base is None:
        raise AppError(
            code=ErrorCode.RESOURCE_NOT_FOUND,
            message="知识库不存在",
            error_type="RESOURCE_NOT_FOUND",
            status_code=404,
        )
    return knowledge_base


async def create_knowledge_base(
    session: AsyncSession, user: User, name: str, description: str | None
) -> KnowledgeBase:
    knowledge_base = KnowledgeBase(
        owner_id=user.id,
        name=name,
        description=description,
        status="active",
    )
    session.add(knowledge_base)
    await session.commit()
    await session.refresh(knowledge_base)
    return knowledge_base


async def soft_delete_knowledge_base(
    session: AsyncSession, user: User, knowledge_base_id: str
) -> KnowledgeBase:
    knowledge_base = await get_visible_knowledge_base(session, user, knowledge_base_id)
    knowledge_base.status = "deleted"
    knowledge_base.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.commit()
    await session.refresh(knowledge_base)
    return knowledge_base


def visible_documents(user: User, knowledge_base_id: str | None = None) -> Select[tuple[Document]]:
    statement = (
        select(Document)
        .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
        .where(KnowledgeBase.status != "deleted", Document.status != "deleted")
    )
    if user.role != "admin":
        statement = statement.where(KnowledgeBase.owner_id == user.id)
    if knowledge_base_id is not None:
        statement = statement.where(Document.knowledge_base_id == knowledge_base_id)
    return statement.order_by(Document.created_at.desc())
