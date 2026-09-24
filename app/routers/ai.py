"""AI / RAG 接口（混合检索 + 对话 + 流式 + 会话）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..container import Container
from ..deps import get_current_user, require_permission
from ..schemas import (
    ChatDto,
    ChatStreamDto,
    CreateSessionDto,
    QuerySessionDto,
    RagSearchDto,
    UpdateSessionDto,
)

router = APIRouter(tags=["ai"])

SEARCH_PERM = Depends(require_permission("search"))


@router.post("/rag/search", dependencies=[SEARCH_PERM])
async def rag_search(
    dto: RagSearchDto, user: dict = Depends(get_current_user)
):
    hits = await Container.instance().hybrid_retrieval().retrieve(
        dto.query.strip(), dto.topK, user
    )
    return [h.__dict__ for h in hits]


@router.post("/ai/chat", dependencies=[SEARCH_PERM])
async def ai_chat(
    dto: ChatDto, user: dict = Depends(get_current_user)
):
    return await Container.instance().ai_chat().chat(
        dto.content, dto.topK, user, dto.sessionId
    )


@router.post("/ai/chat/stream", dependencies=[SEARCH_PERM])
async def ai_chat_stream(
    dto: ChatStreamDto, user: dict = Depends(get_current_user)
):
    return Container.instance().ai_stream().stream_chat(dto, user)


@router.get("/ai/sessions", dependencies=[SEARCH_PERM])
async def list_sessions(
    query: QuerySessionDto = Depends(), user: dict = Depends(get_current_user)
):
    return await Container.instance().chat_sessions().page_mine(
        user["userId"], query.page, query.pageSize
    )


@router.post("/ai/sessions", dependencies=[SEARCH_PERM])
async def create_session(
    dto: CreateSessionDto, user: dict = Depends(get_current_user)
):
    session = await Container.instance().chat_sessions().create(user["userId"], dto.title)
    return _session_dict(session)


@router.get("/ai/sessions/{session_id}/messages", dependencies=[SEARCH_PERM])
async def list_messages(
    session_id: str, user: dict = Depends(get_current_user)
):
    return await Container.instance().chat_sessions().list_messages(
        user["userId"], session_id
    )


@router.patch("/ai/sessions/{session_id}", dependencies=[SEARCH_PERM])
async def rename_session(
    session_id: str, dto: UpdateSessionDto, user: dict = Depends(get_current_user)
):
    session = await Container.instance().chat_sessions().rename(
        user["userId"], session_id, dto.title
    )
    return _session_dict(session)


@router.delete("/ai/sessions/{session_id}", dependencies=[SEARCH_PERM])
async def remove_session(
    session_id: str, user: dict = Depends(get_current_user)
):
    return await Container.instance().chat_sessions().remove(
        user["userId"], session_id
    )


def _session_dict(session) -> dict:
    return {
        "id": session.id,
        "userId": session.user_id,
        "title": session.title,
        "createdAt": session.created_at.isoformat() if session.created_at else None,
        "updatedAt": session.updated_at.isoformat() if session.updated_at else None,
    }
