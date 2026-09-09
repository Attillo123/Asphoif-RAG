from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.user import User

try:
    from pwdlib import PasswordHash
except ModuleNotFoundError:  # pragma: no cover - only used before uv sync
    PasswordHash = None  # type: ignore[assignment,misc]


def _password_hash():
    if PasswordHash is None:
        raise RuntimeError("pwdlib is required; run uv sync")
    return PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _password_hash().hash(password)


def verify_password(password: str, encoded_password: str) -> bool:
    return _password_hash().verify(password, encoded_password)


async def authenticate_user(session: AsyncSession, username: str, password: str) -> User:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AppError(
            code=ErrorCode.LOGIN_FAILED,
            message="用户名或密码错误",
            error_type="LOGIN_FAILED",
            status_code=401,
        )
    return user


def create_access_token(user: User, settings: Settings) -> tuple[str, int]:
    expires_in = settings.jwt_access_token_expire_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_in
