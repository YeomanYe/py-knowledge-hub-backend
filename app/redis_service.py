"""Redis 服务（get/set/del/ttl）。"""
from __future__ import annotations

import redis.asyncio as aioredis

from .config import get_settings


class RedisService:
    def __init__(self) -> None:
        settings = get_settings()
        self._client: aioredis.Redis | None = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
        )

    async def get(self, key: str) -> str | None:
        value = await self._client.get(key)
        return value.decode("utf-8") if isinstance(value, bytes) else value

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is not None:
            await self._client.set(key, value, ex=ttl_seconds)
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def ttl(self, key: str) -> int:
        return await self._client.ttl(key)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
