from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import api_response
from app.core.security import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserResponse
from app.services.auth import authenticate_user, create_access_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=dict[str, Any], summary="用户登录")
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    user = await authenticate_user(session, payload.username, payload.password)
    token, expires_in = create_access_token(user, settings)
    data = LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )
    return api_response(success=True, code=0, message="ok", data=data.model_dump())


@router.get("/me", response_model=dict[str, Any], summary="当前用户")
async def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return api_response(
        success=True,
        code=0,
        message="ok",
        data=UserResponse.model_validate(user).model_dump(),
    )
