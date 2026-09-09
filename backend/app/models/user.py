from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.ids import new_id

if TYPE_CHECKING:
    from app.models.knowledge import KnowledgeBase


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("personal", "admin", name="user_role"), nullable=False, default="personal"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(back_populates="owner")
