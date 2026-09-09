from __future__ import annotations

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.db.session import get_db_session
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            code=ErrorCode.TOKEN_INVALID,
            message="登录状态无效",
            error_type="TOKEN_INVALID",
            status_code=401,
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "type"]},
        )
    except ExpiredSignatureError as exc:
        raise AppError(
            code=ErrorCode.TOKEN_EXPIRED,
            message="登录状态已过期",
            error_type="TOKEN_EXPIRED",
            status_code=401,
        ) from exc
    except InvalidTokenError as exc:
        raise AppError(
            code=ErrorCode.TOKEN_INVALID,
            message="登录状态无效",
            error_type="TOKEN_INVALID",
            status_code=401,
        ) from exc

    if payload.get("type") != "access":
        raise AppError(
            code=ErrorCode.TOKEN_INVALID,
            message="登录状态无效",
            error_type="TOKEN_INVALID",
            status_code=401,
        )
    user_id = payload.get("sub")
    result = await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            code=ErrorCode.TOKEN_INVALID,
            message="登录状态无效",
            error_type="TOKEN_INVALID",
            status_code=401,
        )
    return user
