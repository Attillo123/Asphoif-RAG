from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import secrets
import unicodedata
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis


class CacheUnavailable(RuntimeError):
    pass


def normalize_query(query: str) -> str:
    value = unicodedata.normalize("NFKC", query).strip().casefold()
    return re.sub(r"\s+", " ", value)


def cache_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RedisCache:
    """Small cache-aside adapter; Redis failures never become core-path failures."""

    def __init__(
        self,
        redis_url: str,
        *,
        environment: str,
        timeout_seconds: float = 1.0,
    ) -> None:
        self.client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )
        self.environment = environment

    def key(self, layer: str, version: str, scope: str, parameters: Mapping[str, Any]) -> str:
        return f"rag:{self.environment}:{layer}:{version}:{scope}:{cache_hash(parameters)}"

    async def get_json(self, key: str) -> Any | None:
        try:
            value = await self.client.get(key)
            return None if value is None else json.loads(value)
        except Exception as exc:
            raise CacheUnavailable("Redis get failed") from exc

    async def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        # Jitter avoids a large number of keys expiring at precisely the same time.
        ttl = max(1, ttl_seconds + random.randint(0, min(300, max(ttl_seconds // 10, 1))))
        try:
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as exc:
            raise CacheUnavailable("Redis set failed") from exc

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            # Cache cleanup must not turn a successful core request into a failure.
            pass


    async def wait_for_json(
        self, key: str, *, attempts: int = 20, interval_seconds: float = 0.05
    ) -> Any | None:
        for _ in range(attempts):
            value = await self.get_json(key)
            if value is not None:
                return value
            await asyncio.sleep(interval_seconds)
        return None

    async def acquire_lock(self, key: str, ttl_seconds: int = 10) -> str | None:
        token = secrets.token_urlsafe(16)
        try:
            acquired = await self.client.set(f"{key}:lock", token, ex=ttl_seconds, nx=True)
            return token if acquired else None
        except Exception as exc:
            raise CacheUnavailable("Redis lock failed") from exc

    async def release_lock(self, key: str, token: str) -> None:
        try:
            current = await self.client.get(f"{key}:lock")
            if current == token:
                await self.client.delete(f"{key}:lock")
        except Exception as exc:
            raise CacheUnavailable("Redis unlock failed") from exc
