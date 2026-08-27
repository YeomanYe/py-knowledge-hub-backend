"""密码重置验证码（Redis 存储，10 分钟有效，60 秒冷却；）。"""
from __future__ import annotations

from ..redis_service import RedisService

RESET_CODE_PREFIX = "password:reset:code:"
RESET_CODE_TTL_SECONDS = 10 * 60
RESET_CODE_COOLDOWN_SECONDS = 60


class PasswordResetService:
    def __init__(self, redis: RedisService) -> None:
        self._redis = redis

    def _key(self, email: str) -> str:
        return f"{RESET_CODE_PREFIX}{email.lower()}"

    async def set(self, email: str, code: str) -> None:
        await self._redis.set(self._key(email), code, RESET_CODE_TTL_SECONDS)

    async def get(self, email: str) -> str | None:
        return await self._redis.get(self._key(email))

    async def get_ttl(self, email: str) -> int:
        return await self._redis.ttl(self._key(email))

    async def verify(self, email: str, code: str) -> bool:
        stored = await self.get(email)
        return stored is not None and stored == code

    async def delete(self, email: str) -> None:
        await self._redis.delete(self._key(email))
