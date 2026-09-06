"""AI 会话服务（PostgreSQL kh_ai_session / kh_ai_message）。"""
from __future__ import annotations

import datetime

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..common import next_snowflake_id
from ..models import AiMessage, AiSession
from .chat_types import ChatSource

DEFAULT_TITLE = "新对话"


class ChatSessionService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def page_mine(self, user_id: str, page: int = 1, page_size: int = 20) -> dict:
        page = page or 1
        page_size = min(page_size or 20, 100)
        async with self._session_factory() as session:
            total_stmt = select(AiSession).where(AiSession.user_id == user_id)
            total = len((await session.execute(total_stmt)).scalars().all())
            stmt = (
                select(AiSession)
                .where(AiSession.user_id == user_id)
                .order_by(AiSession.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            items = (await session.execute(stmt)).scalars().all()
        return {
            "items": [self._session_to_dict(s) for s in items],
            "total": total,
            "page": page,
            "pageSize": page_size,
        }

    async def create(self, user_id: str, title: str | None = None) -> AiSession:
        title = (title or "").strip() or DEFAULT_TITLE
        session = AiSession(
            id=next_snowflake_id(),
            user_id=user_id,
            title=title[:80],
        )
        async with self._session_factory() as db:
            db.add(session)
            await db.commit()
            await db.refresh(session)
        return session

    async def rename(self, user_id: str, session_id: str, title: str) -> AiSession:
        async with self._session_factory() as db:
            session = await self._get_owned(db, user_id, session_id)
            session.title = title.strip()[:80]
            await db.commit()
            await db.refresh(session)
            return session

    async def remove(self, user_id: str, session_id: str) -> dict:
        async with self._session_factory() as db:
            await self._get_owned(db, user_id, session_id)
            await db.execute(delete(AiMessage).where(AiMessage.session_id == session_id))
            await db.execute(delete(AiSession).where(AiSession.id == session_id))
            await db.commit()
        return {"message": "已删除"}

    async def list_messages(self, user_id: str, session_id: str) -> list[dict]:
        async with self._session_factory() as db:
            await self._get_owned(db, user_id, session_id)
            stmt = (
                select(AiMessage)
                .where(AiMessage.session_id == session_id)
                .order_by(AiMessage.created_at.asc(), AiMessage.id.asc())
            )
            messages = (await db.execute(stmt)).scalars().all()
        return [self._message_to_dict(m) for m in messages]

    async def append_turn(
        self,
        user_id: str,
        session_id: str | None,
        question: str,
        answer: str,
        sources: list[ChatSource],
    ) -> AiSession:
        async with self._session_factory() as db:
            session = None
            if session_id:
                session = await self._get_owned(db, user_id, session_id)
            if session is None:
                session = AiSession(
                    id=next_snowflake_id(),
                    user_id=user_id,
                    title=_title_from_question(question),
                )
                db.add(session)
                # create(userId, ...) 先落库 session，
                # 再保存消息（SQLAlchemy 对无 relationship 的纯 FK 不保证跨 mapper 顺序）
                await db.flush()

            if session.title == DEFAULT_TITLE:
                session.title = _title_from_question(question)
            session.updated_at = datetime.datetime.now()
            db.add(session)
            await db.flush()  # 标题/时间更新先落库

            db.add(
                AiMessage(
                    id=next_snowflake_id(),
                    session_id=session.id,
                    role="user",
                    content=question,
                )
            )
            db.add(
                AiMessage(
                    id=next_snowflake_id(),
                    session_id=session.id,
                    role="assistant",
                    content=answer,
                    sources=[s.__dict__ for s in sources] if sources else None,
                )
            )
            await db.commit()
            await db.refresh(session)
            return session

    async def _get_owned(self, db, user_id: str, session_id: str) -> AiSession:
        stmt = select(AiSession).where(
            AiSession.id == session_id, AiSession.user_id == user_id
        )
        session = (await db.execute(stmt)).scalars().first()
        if session is None:
            raise HTTPException(404, "会话不存在")
        return session

    @staticmethod
    def _session_to_dict(s: AiSession) -> dict:
        return {
            "id": s.id,
            "userId": s.user_id,
            "title": s.title,
            "createdAt": s.created_at.isoformat() if s.created_at else None,
            "updatedAt": s.updated_at.isoformat() if s.updated_at else None,
        }

    @staticmethod
    def _message_to_dict(m: AiMessage) -> dict:
        return {
            "id": m.id,
            "sessionId": m.session_id,
            "role": m.role,
            "content": m.content,
            "sources": m.sources,
            "createdAt": m.created_at.isoformat() if m.created_at else None,
        }


def _title_from_question(question: str) -> str:
    text = " ".join(question.split()).strip()
    if not text:
        return DEFAULT_TITLE
    return f"{text[:30]}…" if len(text) > 30 else text
