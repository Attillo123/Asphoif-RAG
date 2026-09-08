from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import Settings
from app.core.errors import ErrorCode, api_response
from app.db.session import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _check_result(
    status: str, started: float, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if details:
        result["details"] = details
    return result


async def check_mysql() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        return _check_result("up", started)
    except Exception as exc:
        logger.warning("Dependency health check failed", extra={"dependency": "mysql"})
        return _check_result("down", started, {"error_type": type(exc).__name__})


async def check_redis(settings: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    client: Redis | None = None
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return _check_result("up", started)
    except Exception as exc:
        logger.warning("Dependency health check failed", extra={"dependency": "redis"})
        return _check_result("down", started, {"error_type": type(exc).__name__})
    finally:
        if client is not None:
            await client.aclose()


async def check_opensearch(settings: Settings) -> dict[str, Any]:
    started = time.perf_counter()
    auth = None
    if settings.opensearch_username:
        password = ""
        if settings.opensearch_password:
            password = settings.opensearch_password.get_secret_value()
        auth = (settings.opensearch_username, password)

    try:
        timeout = httpx.Timeout(settings.healthcheck_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=settings.opensearch_url.rstrip("/"),
            auth=auth,
            verify=settings.opensearch_verify_ssl,
            timeout=timeout,
        ) as client:
            info_response = await client.get("/")
            info_response.raise_for_status()
            info = info_response.json()

            plugin_response = await client.get("/_cat/plugins", params={"format": "json"})
            plugin_response.raise_for_status()
            plugin_count = len(plugin_response.json())
            analyzer_checks = {}
            for analyzer in ("ik_smart", "ik_max_word"):
                analyzer_response = await client.post(
                    "/_analyze",
                    json={"analyzer": analyzer, "text": "RAG analyzer health check"},
                )
                analyzer_checks[analyzer] = analyzer_response.is_success

            ik_installed = all(analyzer_checks.values())
            if not ik_installed:
                return _check_result(
                    "degraded",
                    started,
                    {
                        "version": info.get("version", {}).get("number"),
                        "ik_plugin": "missing",
                        "plugin_count": plugin_count,
                        "analyzers": analyzer_checks,
                    },
                )
            return _check_result(
                "up",
                started,
                {
                    "version": info.get("version", {}).get("number"),
                    "ik_plugin": "installed",
                    "plugin_count": plugin_count,
                    "analyzers": analyzer_checks,
                },
            )
    except Exception as exc:
        logger.warning("Dependency health check failed", extra={"dependency": "opensearch"})
        return _check_result("down", started, {"error_type": type(exc).__name__})


async def readiness(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    mysql, redis, opensearch = await asyncio.gather(
        check_mysql(),
        check_redis(settings),
        check_opensearch(settings),
    )
    dependencies = {"mysql": mysql, "redis": redis, "opensearch": opensearch}
    ready = all(item["status"] == "up" for item in dependencies.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content=api_response(
            success=ready,
            code=0 if ready else ErrorCode.DEPENDENCY_UNAVAILABLE,
            message="ready" if ready else "一个或多个必要依赖不可用",
            data={"status": "ready" if ready else "not_ready", "dependencies": dependencies},
            error=None
            if ready
            else {
                "type": "DEPENDENCY_UNAVAILABLE",
                "retryable": True,
                "details": {"dependencies": dependencies},
            },
        ),
    )


@router.get("/health/live", summary="存活检查")
async def live() -> dict[str, Any]:
    return api_response(success=True, code=0, message="ok", data={"status": "alive"})


@router.get("/health/ready", summary="依赖就绪检查")
@router.get("/api/v1/ready", include_in_schema=False)
async def ready(request: Request) -> JSONResponse:
    return await readiness(request)


@router.get("/api/v1/health", summary="服务健康检查")
async def health(request: Request) -> JSONResponse:
    return await readiness(request)
