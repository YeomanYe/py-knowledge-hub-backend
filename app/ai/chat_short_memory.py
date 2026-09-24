"""对话短期记忆：Redis 热窗口。

miss / 故障时由调用方从 Postgres 回填再 save。
只存 user/assistant 原文，不含检索资料、思考、Mem0 系统消息。
"""
from __future__ import annotations

import json
import logging

from ..config import get_settings
from ..redis_service import RedisService
from .chat_memory_util import is_working_message

logger = logging.getLogger("chat-short-memory")


class ChatShortMemoryService:
    def __init__(self, redis: RedisService) -> None:
        settings = get_settings()
        self._redis = redis
        self._ttl_seconds = settings.chat_short_memory_ttl_seconds
        self._max_messages = settings.chat_short_memory_max_messages
        self._key_prefix = settings.chat_short_memory_key_prefix

    @property
    def window_size(self) -> int:
        return self._max_messages

    async def try_load(self, user_id: str, session_id: str) -> list[dict] | None:
        try:
            raw = await self._redis.get(self._key(user_id, session_id))
            if not raw:
                return None
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return None
            return [m for m in parsed if is_working_message(m)]
        except Exception as err:
            logger.warning("短期记忆 Redis 读取失败，改走数据库：%s", err)
            return None

    async def save(
        self, user_id: str, session_id: str, messages: list[dict]
    ) -> None:
        working = [m for m in messages if is_working_message(m)][-self._max_messages :]
        try:
            await self._redis.set(
                self._key(user_id, session_id),
                json.dumps(working, ensure_ascii=False),
                self._ttl_seconds,
            )
        except Exception as err:
            logger.warning("短期记忆 Redis 写入失败：%s", err)

    async def append_turn(
        self,
        user_id: str,
        session_id: str,
        history: list[dict],
        question: str,
        answer: str,
    ) -> None:
        await self.save(
            user_id,
            session_id,
            [
                *history,
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
        )

    async def clear(self, user_id: str, session_id: str) -> None:
        try:
            await self._redis.delete(self._key(user_id, session_id))
        except Exception as err:
            logger.warning("短期记忆 Redis 删除失败：%s", err)

    def _key(self, user_id: str, session_id: str) -> str:
        return f"{self._key_prefix}:{user_id}:{session_id}:messages"
