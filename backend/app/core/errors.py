from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)


class ErrorCode:
    INVALID_REQUEST = 10001
    VALIDATION_ERROR = 10002
    RESOURCE_NOT_FOUND = 10003
    TOKEN_INVALID = 20001
    TOKEN_EXPIRED = 20002
    ROLE_FORBIDDEN = 20003
    RESOURCE_FORBIDDEN = 20004
    LOGIN_FAILED = 20005
    INTERNAL_ERROR = 90002
    DEPENDENCY_UNAVAILABLE = 90001


class AppError(Exception):
    def __init__(
        self,
        code: int,
        message: str,
        error_type: str,
        status_code: int = 400,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def api_response(
    *,
    success: bool,
    code: int,
    message: str,
    data: Any = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": success,
        "code": code,
        "message": message,
        "data": data,
        "error": error,
        "request_id": get_request_id(),
        "timestamp": _timestamp(),
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=api_response(
            success=False,
            code=exc.code,
            message=exc.message,
            error={
                "type": exc.error_type,
                "retryable": exc.retryable,
                "details": exc.details,
            },
        ),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=api_response(
            success=False,
            code=ErrorCode.VALIDATION_ERROR,
            message="请求参数校验失败",
            error={
                "type": "VALIDATION_ERROR",
                "retryable": False,
                "details": {"errors": exc.errors()},
            },
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled application error")
    return JSONResponse(
        status_code=500,
        content=api_response(
            success=False,
            code=ErrorCode.INTERNAL_ERROR,
            message="服务器内部错误",
            error={"type": "INTERNAL_ERROR", "retryable": True, "details": {}},
        ),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
