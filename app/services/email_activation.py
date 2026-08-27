"""邮箱激活令牌（Redis 存储，24 小时有效；）。"""
from __future__ import annotations

import secrets

from ..redis_service import RedisService

TOKEN_PREFIX = "email:activation:token:"
USER_PREFIX = "email:activation:user:"
ACTIVATION_TOKEN_TTL_SECONDS = 24 * 3600


class EmailActivationService:
    def __init__(self, redis: RedisService) -> None:
        self._redis = redis

    def _token_key(self, token: str) -> str:
        return f"{TOKEN_PREFIX}{token}"

    def _user_key(self, user_id: str) -> str:
        return f"{USER_PREFIX}{user_id}"

    async def create_token(self, user_id: str) -> str:
        existing = await self._redis.get(self._user_key(user_id))
        if existing:
            await self._redis.delete(self._token_key(existing))

        token = secrets.token_hex(32)
        await self._redis.set(
            self._token_key(token), user_id, ACTIVATION_TOKEN_TTL_SECONDS
        )
        await self._redis.set(
            self._user_key(user_id), token, ACTIVATION_TOKEN_TTL_SECONDS
        )
        return token

    async def consume_token(self, token: str) -> str | None:
        user_id = await self._redis.get(self._token_key(token))
        if not user_id:
            return None
        await self._redis.delete(self._token_key(token))
        await self._redis.delete(self._user_key(user_id))
        return user_id

    async def delete_by_token(self, token: str) -> None:
        user_id = await self._redis.get(self._token_key(token))
        await self._redis.delete(self._token_key(token))
        if user_id:
            await self._redis.delete(self._user_key(user_id))
